"""
src/backends.py — Couche d'abstraction multi-modeles pour DAMIA / MCP.

Le reste du projet n'appelle QUE backend.generer(prompt). Il ne sait pas s'il parle
a Ollama, a un modele Transformers (PyTorch), ou a une API distante. On change de
moteur via la variable d'environnement DAMIA_BACKEND, sans toucher au code metier :

    "ollama"        -> Ollama local                (DEFAUT, recommande sur un PC sans GPU)
    "transformers"  -> modele HuggingFace PyTorch  (recommande sur Colab GPU)
    "api"           -> API Anthropic
    "gemini"        -> API Google Gemini
    "openai"        -> CONNECTEUR UNIVERSEL OpenAI-compatible
                       (OpenAI, Mistral, Groq, Together, OpenRouter, vLLM, Ollama...)

Le modele peut etre impose via DAMIA_MODELE (sinon valeur par defaut du backend).

Les dependances lourdes (torch, transformers, ollama, openai, anthropic) sont
importees DANS les backends concernes, jamais au chargement du module : installer
Ollama seul suffit a faire tourner la demo.
"""
import logging
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importe pour son EFFET DE BORD : peupler os.environ depuis le .env. Sans cela,
# le backend choisi dependrait de l'ordre des imports — `import src.backends`
# seul retombait silencieusement sur Ollama alors que .env demande openai.
import config.config  # noqa: F401


def _verifier_tls():
    """Verification TLS des appels aux API distantes. ACTIVE par defaut.

    Lue a CHAQUE usage, et non a l'import : config/config.py peuple os.environ
    depuis le .env, et rien ne garantit qu'il soit importe avant ce module.
    Une lecture a l'import donnerait un drapeau fige avant le chargement du .env.

    Ne poser DAMIA_TLS_VERIFY=0 que derriere un proxy d'inspection TLS
    d'entreprise, en sachant que cela expose l'appel a une interception."""
    return os.environ.get("DAMIA_TLS_VERIFY", "1") != "0"


# ---------------------------------------------------------------
# 1) Interface commune
# ---------------------------------------------------------------
class ModelBackend(ABC):
    @abstractmethod
    def generer(self, prompt: str) -> str:
        """Prend un prompt, renvoie le texte genere."""


# ---------------------------------------------------------------
# 2) Backend Ollama — defaut, aligne sur config.py
# ---------------------------------------------------------------
class OllamaBackend(ModelBackend):
    MODELE_DEFAUT = "qwen2.5:7b"
    TEMPERATURE_DEFAUT = 0.1

    def __init__(self, modele=None, temperature=None):
        try:
            from config.config import MODELE_OLLAMA, TEMPERATURE
            defaut_modele, defaut_temp = MODELE_OLLAMA, TEMPERATURE
        except ImportError:
            defaut_modele, defaut_temp = self.MODELE_DEFAUT, self.TEMPERATURE_DEFAUT
        self.modele = modele or os.environ.get("DAMIA_MODELE", defaut_modele)
        self.temperature = temperature if temperature is not None else defaut_temp

    def generer(self, prompt: str) -> str:
        import ollama
        rep = ollama.chat(
            model=self.modele,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": self.temperature},
        )
        return rep["message"]["content"]


# ---------------------------------------------------------------
# 3) Backend Transformers / PyTorch (HuggingFace) — utile sur Colab GPU
# ---------------------------------------------------------------
class TransformersBackend(ModelBackend):
    MODELE_DEFAUT = "Qwen/Qwen3-4B-Instruct-2507"

    def __init__(self, model_id=None, quantize_4bit=None, max_new_tokens=256):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id = model_id or os.environ.get("DAMIA_MODELE", self.MODELE_DEFAUT)
        self.max_new_tokens = max_new_tokens
        gpu = torch.cuda.is_available()

        if quantize_4bit is None:
            quantize_4bit = gpu          # la quantification 4 bits n'a de sens que sur GPU
        if not gpu:
            print("[TransformersBackend] ATTENTION : aucun GPU CUDA detecte. "
                  "Le modele tournera sur CPU (tres lent). Sur ce poste, "
                  "prefere Ollama : DAMIA_BACKEND=ollama.")

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)

        kwargs = {}
        if gpu:
            kwargs["device_map"] = {"": 0}
            if quantize_4bit:
                from transformers import BitsAndBytesConfig
                kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
            else:
                kwargs["dtype"] = torch.float16
        else:
            kwargs["dtype"] = torch.float32

        self.model = AutoModelForCausalLM.from_pretrained(self.model_id, **kwargs)
        print(f"[TransformersBackend] {self.model_id} charge sur "
              f"{next(self.model.parameters()).device} "
              f"(quantize_4bit={bool(quantize_4bit and gpu)}, "
              f"max_new_tokens={self.max_new_tokens})")

    def generer(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        try:
            texte = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            texte = prompt               # modele sans gabarit de chat : prompt brut
        entrees = self.tokenizer(texte, return_tensors="pt").to(self.model.device)
        sortie = self.model.generate(**entrees, max_new_tokens=self.max_new_tokens,
                                     do_sample=False)
        nouveaux = sortie[0][entrees["input_ids"].shape[1]:]
        return self.tokenizer.decode(nouveaux, skip_special_tokens=True)


# ---------------------------------------------------------------
# 4) Backend API distante (Anthropic)
# ---------------------------------------------------------------
class APIBackend(ModelBackend):
    MODELE_DEFAUT = "claude-sonnet-5"
    MAX_TOKENS_DEFAUT = 2048        # marge pour un eventuel raisonnement etendu

    def __init__(self, modele=None, max_tokens=None):
        self.modele = modele or os.environ.get("DAMIA_MODELE", self.MODELE_DEFAUT)
        self.max_tokens = (max_tokens if max_tokens is not None
                           else int(os.environ.get("DAMIA_MAX_TOKENS",
                                                   self.MAX_TOKENS_DEFAUT)))
        self.cle = os.environ.get("DAMIA_API_KEY")
        if not self.cle:
            raise RuntimeError("Cle API manquante : definis DAMIA_API_KEY")
        self._client = None

    def generer(self, prompt: str) -> str:
        import anthropic
        if self._client is None:          # un seul client reutilise entre les appels
            self._client = anthropic.Anthropic(api_key=self.cle)
        msg = self._client.messages.create(
            model=self.modele,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------
# 5) Backend Google Gemini
# ---------------------------------------------------------------
class GeminiBackend(ModelBackend):
    MODELE_DEFAUT = "gemini-1.5-flash"

    def __init__(self, modele=None):
        self.modele = modele or os.environ.get("DAMIA_MODELE", self.MODELE_DEFAUT)
        self.cle = os.environ.get("DAMIA_API_KEY")
        if not self.cle:
            raise RuntimeError("Cle API manquante : definis DAMIA_API_KEY")
        self._model = None

    def generer(self, prompt: str) -> str:
        import google.generativeai as genai
        if self._model is None:
            genai.configure(api_key=self.cle)
            self._model = genai.GenerativeModel(self.modele)
        return self._model.generate_content(prompt).text


# ---------------------------------------------------------------
# 6) Backend OpenAI-compatible — CONNECTEUR UNIVERSEL
# ---------------------------------------------------------------
# Parle a TOUTE API respectant le format OpenAI /v1/chat/completions :
# OpenAI, Mistral, Groq, Together, OpenRouter, Fireworks, vLLM, LM Studio,
# et meme Ollama (via son endpoint compatible). On change de fournisseur en
# ne touchant QUE des variables d'environnement, jamais le code.
#
# Variables :
#   DAMIA_BASE_URL : URL de base de l'API (ex. https://api.mistral.ai/v1)
#   DAMIA_API_KEY  : cle du fournisseur (certains serveurs locaux n'en exigent pas)
#   DAMIA_MODELE   : identifiant du modele chez ce fournisseur
#
# Exemples :
#   Mistral    : DAMIA_BASE_URL=https://api.mistral.ai/v1       DAMIA_MODELE=mistral-large-latest
#   Groq       : DAMIA_BASE_URL=https://api.groq.com/openai/v1  DAMIA_MODELE=openai/gpt-oss-120b
#   OpenRouter : DAMIA_BASE_URL=https://openrouter.ai/api/v1    DAMIA_MODELE=qwen/qwen-2.5-72b-instruct
#   OpenAI     : DAMIA_BASE_URL=https://api.openai.com/v1       DAMIA_MODELE=gpt-4o-mini
#   Ollama     : DAMIA_BASE_URL=http://localhost:11434/v1       DAMIA_MODELE=qwen2.5:7b  (cle="ollama")
class OpenAICompatibleBackend(ModelBackend):
    MODELE_DEFAUT = "mistral-large-latest"

    # Les modeles a RAISONNEMENT (gpt-oss, o-series, qwen3-thinking...) facturent
    # leur chaine de pensee sur le meme budget que la reponse. Mesure sur
    # gpt-oss-120b pour une question de routage : ~40 jetons de JSON utile pour
    # 240 a 380 jetons de raisonnement, tres variables d'un tirage a l'autre.
    # A 512, un raisonnement un peu long tronquait le JSON en plein milieu et la
    # question echouait — par intermittence, donc difficile a diagnostiquer.
    MAX_TOKENS_DEFAUT = 2048

    def __init__(self, modele=None, base_url=None, temperature=None, max_tokens=None):
        self.base_url = base_url or os.environ.get("DAMIA_BASE_URL")
        if not self.base_url:
            raise RuntimeError("URL manquante : definis DAMIA_BASE_URL "
                               "(ex. https://api.mistral.ai/v1)")
        self.modele = modele or os.environ.get("DAMIA_MODELE", self.MODELE_DEFAUT)
        self.cle = os.environ.get("DAMIA_API_KEY", "no-key")  # serveurs locaux : cle facultative
        self.temperature = (temperature if temperature is not None
                            else float(os.environ.get("DAMIA_TEMPERATURE", "0.1")))
        self.max_tokens = (max_tokens if max_tokens is not None
                           else int(os.environ.get("DAMIA_MAX_TOKENS",
                                                   self.MAX_TOKENS_DEFAUT)))
        self._client = None

    def _obtenir_client(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.cle, "base_url": self.base_url}
            if not _verifier_tls():
                # Rustine proxy d'entreprise, opt-in explicite (DAMIA_TLS_VERIFY=0).
                import httpx
                logging.getLogger(__name__).warning(
                    "Vérification TLS désactivée (DAMIA_TLS_VERIFY=0) vers %s",
                    self.base_url)
                kwargs["http_client"] = httpx.Client(verify=False)
            self._client = OpenAI(**kwargs)
        return self._client

    def generer(self, prompt: str) -> str:
        rep = self._obtenir_client().chat.completions.create(
            model=self.modele,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        choix = rep.choices[0]

        # Une reponse coupee par la limite de jetons produit un JSON tronque, donc
        # une question qui echoue sans raison apparente. Sans ce signal, la panne
        # est invisible : on ne voit qu'un routage rate, par intermittence.
        if choix.finish_reason == "length":
            usage = getattr(rep, "usage", None)
            details = getattr(usage, "completion_tokens_details", None)
            raisonnement = getattr(details, "reasoning_tokens", None)
            logging.getLogger(__name__).warning(
                "Reponse tronquee par max_tokens=%d (modele %s%s). "
                "Augmentez DAMIA_MAX_TOKENS.", self.max_tokens, self.modele,
                f", dont {raisonnement} jetons de raisonnement" if raisonnement else "")

        return choix.message.content


# ---------------------------------------------------------------
# 7) Fabrique (avec cache : un seul backend charge par processus)
# ---------------------------------------------------------------
BACKENDS = {
    "ollama": OllamaBackend,
    "transformers": TransformersBackend,
    "api": APIBackend,
    "gemini": GeminiBackend,
    "openai": OpenAICompatibleBackend,
    "compatible": OpenAICompatibleBackend,
    "universel": OpenAICompatibleBackend,
}

_BACKEND = None


def get_backend(force_reload=False) -> ModelBackend:
    """Backend actif, construit une seule fois par processus.

    force_reload=True : reconstruit (utilise par les interfaces quand
    l'utilisateur change de moteur a chaud)."""
    global _BACKEND
    if _BACKEND is not None and not force_reload:
        return _BACKEND

    choix = os.environ.get("DAMIA_BACKEND", "ollama").lower()
    classe = BACKENDS.get(choix)
    if classe is None:
        raise ValueError(f"Backend inconnu : {choix}. "
                         f"Valeurs possibles : {', '.join(sorted(set(BACKENDS)))}.")
    _BACKEND = classe()
    return _BACKEND


if __name__ == "__main__":
    backend = get_backend()
    print(f"Backend actif : {backend.__class__.__name__}")
    print(backend.generer("Dis bonjour en une phrase."))
