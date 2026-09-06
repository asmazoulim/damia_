"""
benchmark_backends.py — Compare la VITESSE des differents modeles / backends.

Lancement, depuis la racine du projet :
    python benchmark_backends.py                    # configurations par defaut
    python benchmark_backends.py ollama openai      # seulement ces backends

Mesure, pour chaque configuration, la latence et une estimation tokens/s sur les
memes questions. Libere la VRAM entre chaque modele -> evite le « CUDA out of
memory » quand plusieurs modeles se succedent sur un seul GPU.

RAPPEL : la vitesse depend de la MACHINE.
  - PC sans GPU  : Ollama uniquement (Transformers y serait inutilisable).
  - Colab GPU T4 : comparer Ollama et Transformers (modeles legers quantifies).

On mesure la generation BRUTE du backend, sans le passage MCP : l'objectif est
d'isoler la vitesse du modele, pas celle de la chaine complete (pour cela, voir
la colonne « Temps » de tests/banc_test.py).
"""
import gc
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.backends import OllamaBackend, OpenAICompatibleBackend, TransformersBackend

# Configurations disponibles : nom court -> (libelle, fabrique).
# La fabrique est paresseuse : un modele n'est charge que s'il est reellement teste.
CONFIGS = {
    "ollama": ("Ollama qwen2.5:7b",
               lambda: OllamaBackend(modele="qwen2.5:7b")),
    "openai": ("Connecteur OpenAI-compatible (DAMIA_BASE_URL)",
               OpenAICompatibleBackend),
    "qwen3-4b": ("Transformers Qwen3-4B (4 bits)",
                 lambda: TransformersBackend(model_id="Qwen/Qwen3-4B-Instruct-2507")),
    "llama3-3b": ("Transformers Llama-3.2-3B fp16",
                  lambda: TransformersBackend(model_id="meta-llama/Llama-3.2-3B-Instruct",
                                              quantize_4bit=False)),
}
CONFIGS_DEFAUT = ["ollama"]

# Vraies questions type de la demo.
PROMPTS = [
    "Quel est le taux de couverture de l'optique en 2023 ?",
    "Combien l'Assurance Maladie a-t-elle rembourse pour le dentaire en 2024 ?",
    "Compare les depenses de pharmacie entre 2022 et 2024.",
]


def estimer_tokens(texte):
    """Estimation commune a tous les backends (~0.75 mot par token).
    Approximative, mais identique partout : elle sert a COMPARER, pas a facturer."""
    return max(1, int(len(texte.split()) / 0.75))


def liberer_vram():
    """Vide la VRAM entre deux backends pour eviter l'empilement (OOM)."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except ImportError:
        pass


def bencher(libelle, fabrique):
    """Renvoie (libelle, latence_moyenne, vitesse_moyenne) ; (libelle, None, None) si echec."""
    print(f"\n=== {libelle} ===")
    backend = None
    try:
        t0 = time.perf_counter()
        backend = fabrique()
        print(f"Chargement : {time.perf_counter() - t0:.1f}s")

        backend.generer("Bonjour")      # tour de chauffe, non mesure

        latences, vitesses = [], []
        for i, prompt in enumerate(PROMPTS, 1):
            t0 = time.perf_counter()
            reponse = backend.generer(prompt)
            duree = time.perf_counter() - t0
            vitesse = estimer_tokens(reponse) / duree
            latences.append(duree)
            vitesses.append(vitesse)
            print(f"  Q{i} : {duree:.2f}s  (~{vitesse:.1f} tokens/s)")
        return (libelle, sum(latences) / len(latences), sum(vitesses) / len(vitesses))
    except Exception as e:
        print(f"  ECHEC : {e}")
        return (libelle, None, None)
    finally:
        backend = None
        liberer_vram()                  # liberation systematique, succes ou echec


def main(noms=None):
    noms = noms or CONFIGS_DEFAUT
    inconnus = [n for n in noms if n not in CONFIGS]
    if inconnus:
        raise SystemExit(f"Configuration inconnue : {', '.join(inconnus)}. "
                         f"Disponibles : {', '.join(CONFIGS)}.")

    resultats = [bencher(*CONFIGS[n]) for n in noms]

    print("\n" + "=" * 60)
    print("RECAPITULATIF (vitesse : plus haut = mieux)")
    print("=" * 60)
    print(f"{'Configuration':<34}{'Latence':>10}{'Vitesse':>14}")
    print("-" * 60)
    for libelle, latence, vitesse in resultats:
        if latence is None:
            print(f"{libelle:<34}{'echec':>10}")
        else:
            print(f"{libelle:<34}{latence:>8.2f}s{vitesse:>10.1f} t/s")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
