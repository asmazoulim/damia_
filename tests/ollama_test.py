"""
Test du pont Python <-> Ollama.
Objectif : verifier (1) la vitesse sur reponse COURTE, (2) le tool-calling.
"""
import sys
import time
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import ollama
from config.config import MODELE_OLLAMA, TEMPERATURE


def test_1_vitesse_reponse_courte():
    """Mesure le temps sur une reponse FORCEE courte."""
    print("\n=== TEST 1 : vitesse sur reponse courte ===")
    t0 = time.time()
    rep = ollama.chat(
        model=MODELE_OLLAMA,
        messages=[
            {"role": "system", "content": "Reponds en une seule phrase courte, sans detail."},
            {"role": "user", "content": "Qu'est-ce que l'Assurance Maladie ?"},
        ],
        options={"temperature": TEMPERATURE},
    )
    duree = time.time() - t0
    print(f"Reponse : {rep['message']['content']}")
    print(f">>> Temps : {duree:.1f} secondes")
    return duree


def test_2_tool_calling():
    """Verifie que le modele sait CHOISIR un outil et remplir les parametres."""
    print("\n=== TEST 2 : tool-calling ===")
    # On declare un outil factice simple
    outils = [{
        "type": "function",
        "function": {
            "name": "query_depenses",
            "description": "Calcule un montant de depenses de sante par poste et annee",
            "parameters": {
                "type": "object",
                "properties": {
                    "poste": {"type": "string", "description": "ex: Optique, Audioprothese, Dentaire"},
                    "annee": {"type": "integer", "description": "annee entre 2022 et 2025"},
                },
                "required": ["poste", "annee"],
            },
        },
    }]
    t0 = time.time()
    rep = ollama.chat(
        model=MODELE_OLLAMA,
        messages=[{"role": "user", "content": "Quel est le montant rembourse pour l'optique en 2023 ?"}],
        tools=outils,
        options={"temperature": TEMPERATURE},
    )
    duree = time.time() - t0
    msg = rep["message"]
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            print(f"Outil choisi : {tc['function']['name']}")
            print(f"Parametres : {tc['function']['arguments']}")
        print(f">>> Tool-calling REUSSI en {duree:.1f}s")
        return True
    else:
        print(f"Pas d'appel d'outil. Reponse texte : {msg.get('content','')[:200]}")
        print(f">>> Tool-calling ECHOUE (le modele n'a pas appele l'outil)")
        return False


if __name__ == "__main__":
    print(f"Modele teste : {MODELE_OLLAMA}")
    print("FERME les applis lourdes (Power BI, navigateur) pour liberer la RAM.")
    try:
        d1 = test_1_vitesse_reponse_courte()
        ok = test_2_tool_calling()
        print("\n" + "="*50)
        print("BILAN :")
        print(f"  Vitesse reponse courte : {d1:.1f}s", "(OK)" if d1 < 30 else "(LENT)")
        print(f"  Tool-calling : ", "FONCTIONNE" if ok else "A REVOIR")
        print("="*50)
    except Exception as e:
        print(f"\nERREUR : {e}")
        print("Verifie qu'Ollama tourne (ollama serve) et que le modele est telecharge.")