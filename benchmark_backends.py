"""
benchmark_backends.py — Compare la VITESSE des differents modeles/backends.

A placer a la RACINE du projet (a cote du dossier src/) et lancer :
    python benchmark_backends.py

Mesure, pour chaque configuration, la latence et une estimation tokens/s sur les
memes questions. Libere la VRAM entre chaque modele -> evite le "CUDA out of memory"
quand plusieurs modeles se succedent (ton souci Ollama + Transformers sur un seul T4).

RAPPEL : la vitesse depend de la MACHINE.
  - PC sans GPU  : Ollama uniquement (Transformers y serait inutilisable).
  - Colab GPU T4 : compare Ollama et Transformers (modeles legers quantifies).
"""
import sys
import gc
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.backends import OllamaBackend, TransformersBackend  # APIBackend dispo aussi

# --- Configurations a comparer : (libelle, fabrique) ---
# Commente/decommente selon la machine et ce que tu veux tester.
# Les modeles Transformers sont LEGERS et quantifies par defaut sur GPU.
CONFIGS = [
    #("Ollama qwen2.5:7b",          lambda: OllamaBackend(modele="qwen2.5:7b")),
    #("Transformers Qwen3-4B",      lambda: TransformersBackend(model_id="Qwen/Qwen3-4B-Instruct-2507")),
    ("Transformers Llama-3.2-3B fp16", lambda: TransformersBackend(model_id="meta-llama/Llama-3.2-3B-Instruct", quantize_4bit=False)),
],

# Vraies questions type de ta demo (on mesure la generation BRUTE du backend,
# pas le passage MCP, pour isoler la vitesse du modele)
PROMPTS = [
    "Quel est le taux de couverture de l'optique en 2023 ?",
    "Combien l'Assurance Maladie a-t-elle rembourse pour le dentaire en 2024 ?",
    "Compare les depenses de pharmacie entre 2022 et 2024.",
]


def estimer_tokens(texte):
    """Estimation commune a tous les backends (~0.75 mot par token)."""
    return max(1, int(len(texte.split()) / 0.75))


def liberer_vram():
    """Vide la VRAM entre deux backends pour eviter l'empilement (OOM)."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass


def bencher(libelle, fabrique):
    print(f"\n=== {libelle} ===")
    backend = None
    try:
        t0 = time.time()
        backend = fabrique()
        print(f"Chargement : {time.time() - t0:.1f}s")

        backend.generer("Bonjour")  # tour de chauffe, ignore

        lats, vits = [], []
        for i, prompt in enumerate(PROMPTS, 1):
            t0 = time.time()
            rep = backend.generer(prompt)
            d = time.time() - t0
            tok = estimer_tokens(rep)
            lats.append(d); vits.append(tok / d)
            print(f"  Q{i} : {d:.2f}s  (~{tok/d:.1f} tokens/s)")
        return (libelle, sum(lats)/len(lats), sum(vits)/len(vits))
    except Exception as e:
        print(f"  ECHEC : {e}")
        return (libelle, None, None)
    finally:
        # liberation systematique de la VRAM, succes ou echec
        backend = None
        liberer_vram()


def main():
    res = [bencher(l, f) for l, f in CONFIGS]
    print("\n" + "=" * 56)
    print("RECAPITULATIF (vitesse : plus haut = mieux)")
    print("=" * 56)
    print(f"{'Configuration':<30}{'Latence':>10}{'Vitesse':>14}")
    print("-" * 56)
    for l, lat, vit in res:
        if lat is None:
            print(f"{l:<30}{'echec':>10}")
        else:
            print(f"{l:<30}{lat:>8.2f}s{vit:>10.1f} t/s")


if __name__ == "__main__":
    main()