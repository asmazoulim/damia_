"""
src/modeles.py — Catalogue des configurations de modèle, source unique.

Un « preset » pose TOUTES les variables d'environnement du backend. C'est
volontaire : basculer de moteur ne doit jamais laisser traîner la DAMIA_BASE_URL
du précédent, ce qui enverrait un modèle Ollama vers l'API Groq.

Consommé par api.py (sélecteur de l'interface web), app.py et tests/banc_test.py
(choix du moteur à comparer). Le catalogue vivait auparavant dans api.py, où le
banc ne pouvait pas l'atteindre.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Importe pour son EFFET DE BORD : peupler os.environ depuis le .env, sans quoi
# description_courante() annoncerait le backend par defaut au lieu du votre.
import config.config  # noqa: F401

BASE_GROQ = "https://api.groq.com/openai/v1"

# L'ordre compte : il pilote l'affichage du menu interactif du banc de test.
CATALOGUE = {
    "groq": {
        "label": "Groq · gpt-oss-120b (raisonnement)",
        "env": {"DAMIA_BACKEND": "openai", "DAMIA_BASE_URL": BASE_GROQ,
                "DAMIA_MODELE": "openai/gpt-oss-120b"},
    },
    "groq-20b": {
        "label": "Groq · gpt-oss-20b (plus rapide, plus léger)",
        "env": {"DAMIA_BACKEND": "openai", "DAMIA_BASE_URL": BASE_GROQ,
                "DAMIA_MODELE": "openai/gpt-oss-20b"},
    },
    "groq-qwen": {
        "label": "Groq · qwen3.8-27b",
        "env": {"DAMIA_BACKEND": "openai", "DAMIA_BASE_URL": BASE_GROQ,
                "DAMIA_MODELE": "qwen/qwen3.8-27b"},
    },
    "ollama": {
        "label": "Ollama · qwen2.5:7b (local, souverain)",
        "env": {"DAMIA_BACKEND": "ollama", "DAMIA_MODELE": "qwen2.5:7b"},
    },
}

# Variables posees par un preset. Elles sont TOUTES effacees avant d'appliquer le
# suivant : un preset Ollama ne doit pas heriter du DAMIA_BASE_URL de Groq.
VARIABLES_BACKEND = ("DAMIA_BACKEND", "DAMIA_BASE_URL", "DAMIA_MODELE")


def appliquer(cle):
    """Pose l'environnement du preset `cle` et renvoie son libellé."""
    if cle not in CATALOGUE:
        raise KeyError(f"Preset inconnu « {cle} ». Connus : {', '.join(CATALOGUE)}.")
    for var in VARIABLES_BACKEND:
        os.environ.pop(var, None)
    os.environ.update(CATALOGUE[cle]["env"])
    return CATALOGUE[cle]["label"]


def appliquer_modele_groq(identifiant):
    """Pose un modèle Groq arbitraire (saisie libre), pour tester un identifiant
    absent du catalogue sans avoir à modifier le code."""
    for var in VARIABLES_BACKEND:
        os.environ.pop(var, None)
    os.environ.update({"DAMIA_BACKEND": "openai", "DAMIA_BASE_URL": BASE_GROQ,
                       "DAMIA_MODELE": identifiant})
    return f"Groq · {identifiant}"


def description_courante():
    """Décrit la configuration active, sans la modifier."""
    backend = os.environ.get("DAMIA_BACKEND", "ollama")
    modele = os.environ.get("DAMIA_MODELE") or "(défaut du backend)"
    return f"{backend} · {modele}"


def slug(texte):
    """Identifiant de fichier sûr : 'openai/gpt-oss-120b' -> 'openai_gpt-oss-120b'."""
    garde = [c if (c.isalnum() or c in "-_") else "_" for c in str(texte)]
    return "".join(garde).strip("_").replace("__", "_") or "modele"


def choisir_interactivement(invite="Choix"):
    """Menu de sélection du moteur. Renvoie le libellé retenu.

    Ne s'affiche QUE sur un terminal interactif : lancé depuis un script, un
    pipe ou une CI, le banc doit tourner sans se bloquer sur une saisie. Dans ce
    cas on garde la configuration du .env, qui est le comportement historique.
    """
    if not sys.stdin.isatty():
        return description_courante()

    cles = list(CATALOGUE)
    print("=== Moteur à utiliser pour le banc de test ===")
    for i, cle in enumerate(cles, 1):
        print(f"  {i}) {CATALOGUE[cle]['label']}")
    print(f"  {len(cles) + 1}) Autre modèle Groq (saisie libre)")
    print(f"  {len(cles) + 2}) Garder la configuration du .env "
          f"({description_courante()})")

    defaut = len(cles) + 2
    try:
        saisie = input(f"{invite} [{defaut}] : ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return description_courante()

    if not saisie:
        return description_courante()
    if not saisie.isdigit():
        print(f"Saisie « {saisie} » non comprise — configuration du .env conservée.")
        return description_courante()

    choix = int(saisie)
    if 1 <= choix <= len(cles):
        return appliquer(cles[choix - 1])
    if choix == len(cles) + 1:
        identifiant = input("Identifiant du modèle Groq : ").strip()
        if identifiant:
            return appliquer_modele_groq(identifiant)
        print("Aucun identifiant saisi — configuration du .env conservée.")
    return description_courante()
