"""
src/backends.py — Couche d'abstraction multi-modeles pour DAMIA / MCP.

Le reste du projet n'appelle QUE backend.generer(prompt). Il ne sait pas s'il parle
a Ollama, a un modele Transformers (PyTorch), ou a une API. On change de moteur via
la variable d'environnement DAMIA_BACKEND, sans toucher au code metier :
    "ollama"        -> Ollama local            (DEFAUT, recommande sur ton PC)
    "transformers"  -> modele HuggingFace PyTorch (recommande sur Colab GPU)
    "api"           -> API Anthropic
    "gemini"        -> API Google Gemini       (ancien SDK, a terme remplaçable par openai)
    "openai"        -> CONNECTEUR UNIVERSEL OpenAI-compatible
                       (OpenAI, Mistral, Groq, Together, OpenRouter, vLLM, Ollama...)

Le modele peut etre impose via DAMIA_MODELE (sinon valeur par defaut du backend).
"""
import os
import sys
from pathlib import Path
from abc import ABC, abstractmethod

import httpx
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------
# 1) Interface commune
# ---------------------------------------------------------------
class ModelBackend(ABC):
    @abstractmethod
    def generer(self, prompt: str) -> str:
        """Prend un prompt, renvoie le texte genere."""
        ...


# ---------------------------------------------------------------
# 2) Backend Ollama — defaut, aligne sur config.py
# ---------------------------------------------------------------
class OllamaBackend(ModelBackend):
    def __init__(self, modele=None, temperature=None):
        try:
            from config.config import MODELE_OLLAMA, TEMPERATURE
            defaut_modele, defaut_temp = MODELE_OLLAMA, TEMPERATURE
        except Exception:
            defaut_modele, defaut_temp = "qwen2.5:7b", 0.1
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
            quantize_4bit = gpu

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
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                )
            else:
                kwargs["dtype"] = torch.float16
        else:
            kwargs["dtype"] = torch.float32

        self.model = AutoModelForCausalLM.from_pretrained(self.model_id, **kwargs)

        dev = next(self.model.parameters()).device
        print(f"[TransformersBackend] {self.model_id} charge sur {dev} "
              f"(quantize_4bit={bool(quantize_4bit and gpu)}, "
              f"max_new_tokens={self.max_new_tokens})")

    def generer(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        try:
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            text = prompt
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        out = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                                  do_sample=False)
        nouveaux = out[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(nouveaux, skip_special_tokens=True)


# ---------------------------------------------------------------
# 4) Backend API distante (Anthropic)
# ---------------------------------------------------------------
class APIBackend(ModelBackend):
    def __init__(self, modele=None):
        self.modele = modele or os.environ.get("DAMIA_MODELE", "claude-sonnet-4-6")
        self.cle = os.environ.get("DAMIA_API_KEY")
        if not self.cle:
            raise RuntimeError("Cle API manquante : definis DAMIA_API_KEY")

    def generer(self, prompt: str) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.cle)
        msg = client.messages.create(
            model=self.modele,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------
# 5) Backend Google Gemini (ancien SDK google.generativeai)
#    NOTE : a terme, remplacable par le connecteur OpenAI-compatible (7).
# ---------------------------------------------------------------
class GeminiBackend(ModelBackend):
    def __init__(self, modele=None):
        self.modele = modele or os.environ.get("DAMIA_MODELE", "gemini-1.5-flash")
        self.cle = os.environ.get("DAMIA_API_KEY")
        if not self.cle:
            raise RuntimeError("Cle API manquante : definis DAMIA_API_KEY")

    def generer(self, prompt: str) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.cle)
        model = genai.GenerativeModel(self.modele)
        reponse = model.generate_content(prompt)
        return reponse.text


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
#   Mistral   : DAMIA_BASE_URL=https://api.mistral.ai/v1        DAMIA_MODELE=mistral-large-latest
#   Groq      : DAMIA_BASE_URL=https://api.groq.com/openai/v1   DAMIA_MODELE=llama-3.3-70b-versatile
#   OpenRouter: DAMIA_BASE_URL=https://openrouter.ai/api/v1     DAMIA_MODELE=qwen/qwen-2.5-72b-instruct
#   OpenAI    : DAMIA_BASE_URL=https://api.openai.com/v1        DAMIA_MODELE=gpt-4o-mini
#   Ollama    : DAMIA_BASE_URL=http://localhost:11434/v1        DAMIA_MODELE=qwen2.5:7b  (cle="ollama")
class OpenAICompatibleBackend(ModelBackend):
    def __init__(self, modele=None, base_url=None, temperature=0.1, max_tokens=512):
        self.base_url = base_url or os.environ.get("DAMIA_BASE_URL")
        if not self.base_url:
            raise RuntimeError(
                "URL manquante : definis DAMIA_BASE_URL "
                "(ex. https://api.mistral.ai/v1)")
        self.modele = modele or os.environ.get("DAMIA_MODELE", "mistral-large-latest")
        self.cle = os.environ.get("DAMIA_API_KEY", "no-key")  # serveurs locaux : cle facultative
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generer(self, prompt: str) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.cle, base_url=self.base_url,http_client=httpx.Client(verify=False))
        rep = client.chat.completions.create(
            model=self.modele,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return rep.choices[0].message.content


# ---------------------------------------------------------------
# 7) Fabrique (avec cache : un seul backend charge par processus)
# ---------------------------------------------------------------
_BACKEND = None
def get_backend(force_reload=False) -> ModelBackend:
    global _BACKEND
    if _BACKEND is not None and not force_reload:
        return _BACKEND

    choix = os.environ.get("DAMIA_BACKEND", "ollama").lower()

    if choix == "ollama":
        _BACKEND = OllamaBackend()
    elif choix == "transformers":
        _BACKEND = TransformersBackend()
    elif choix == "api":
        _BACKEND = APIBackend()
    elif choix == "gemini":
        _BACKEND = GeminiBackend()
    elif choix in ("openai", "compatible", "universel"):
        _BACKEND = OpenAICompatibleBackend()
    else:
        raise ValueError(f"Backend inconnu : {choix}")

    return _BACKEND


if __name__ == "__main__":
    backend = get_backend()
    print(f"Backend actif : {backend.__class__.__name__}")
    print(backend.generer("Dis bonjour en une phrase."))