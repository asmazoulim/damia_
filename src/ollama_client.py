"""
Client de demonstration : relie Ollama (LLM local) aux outils metier.
C'est CE script qui fait tourner la demo du 22 (pas Claude Desktop).

Boucle : question utilisateur -> Ollama choisit un outil -> on l'execute
-> on renvoie le resultat a Ollama -> reponse finale en francais.
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import ollama
from config.config import MODELE_OLLAMA, TEMPERATURE
from src import tools

# Declaration des outils pour Ollama (format function-calling)
OUTILS_OLLAMA = [
    {"type": "function", "function": {
        "name": "query_depenses",
        "description": "Calcule un montant ou un nombre d'actes de sante, avec filtres",
        "parameters": {"type": "object", "properties": {
            "mesure": {"type": "string", "enum": ["montant_rembourse","depense_engagee","nombre_actes"]},
            "poste": {"type": "string", "description": "ex: Optique Medicale, Audioprothese"},
            "annee": {"type": "integer"},
            "region": {"type": "string"},
        }, "required": ["mesure"]}}},
    {"type": "function", "function": {
        "name": "get_dictionnaire",
        "description": "Definit un terme metier (mesure, dimension, panier)",
        "parameters": {"type": "object", "properties": {
            "terme": {"type": "string"}}, "required": ["terme"]}}},
    {"type": "function", "function": {
        "name": "list_valeurs",
        "description": "Liste les valeurs possibles d'une dimension",
        "parameters": {"type": "object", "properties": {
            "dimension": {"type": "string"}}, "required": ["dimension"]}}},
    {"type": "function", "function": {
        "name": "compare_periods",
        "description": "Compare une mesure entre DEUX annees (evolution). "
                       "A utiliser pour les questions de comparaison ou d'evolution entre annees.",
        "parameters": {"type": "object", "properties": {
            "poste": {"type": "string", "description": "ex: dentaire, optique, audio"},
            "annee1": {"type": "integer", "description": "premiere annee"},
            "annee2": {"type": "integer", "description": "seconde annee"},
            "mesure": {"type": "string", "enum": ["montant_rembourse","depense_engagee","nombre_actes"]},
        }, "required": ["annee1", "annee2"]}}},
]

# Mapping nom -> fonction Python
FONCTIONS = {
    "query_depenses": tools.query_depenses,
    "get_dictionnaire": tools.get_dictionnaire,
    "list_valeurs": tools.list_valeurs,
    "compare_periods": tools.compare_periods,
}

SYSTEME = (
    "Tu es un assistant qui interroge les donnees Open DAMIR (depenses Assurance "
    "Maladie 2022-2025). Utilise les outils fournis pour repondre. "
    "IMPORTANT : passe toujours les parametres en francais, sans les traduire "
    "(ecris 'dentaire' et non 'dental', 'optique' et non 'optical'). "
    "Pour une comparaison entre deux annees, utilise compare_periods. "
    "Par defaut, la mesure est 'montant_rembourse' sauf si l'utilisateur demande "
    "explicitement la depense engagee ou le nombre d'actes. "
    "Si la question sort du perimetre (donnees non disponibles), dis-le clairement "
    "sans inventer. Reponds de facon concise, en francais."
)


def poser_question(question):
    messages = [{"role": "system", "content": SYSTEME},
                {"role": "user", "content": question}]
    # 1er appel : le LLM choisit un outil
    rep = ollama.chat(model=MODELE_OLLAMA, messages=messages,
                      tools=OUTILS_OLLAMA, options={"temperature": TEMPERATURE})
    msg = rep["message"]

    if not msg.get("tool_calls"):
        return {"reponse": msg.get("content", "(pas de reponse)"),
                "outil": None, "parametres": None}

    # Executer les outils demandes
    outil_appele, params_appeles = None, None
    messages.append(msg)
    for tc in msg["tool_calls"]:
        nom = tc["function"]["name"]
        args = tc["function"]["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        outil_appele, params_appeles = nom, args
        print(f"[DEBUG] Outil: {nom} | Paramètres: {args}")
        resultat = FONCTIONS[nom](**args) if nom in FONCTIONS else "Outil inconnu"
        messages.append({"role": "tool", "content": str(resultat)})

    # 2e appel : le LLM formule la reponse finale a partir du resultat
    rep2 = ollama.chat(model=MODELE_OLLAMA, messages=messages,
                       options={"temperature": TEMPERATURE})
    return {"reponse": rep2["message"]["content"],
            "outil": outil_appele, "parametres": params_appeles}


if __name__ == "__main__":
    print("=== Assistant DAMIA (Ctrl+C pour quitter) ===")
    while True:
        try:
            q = input("\nQuestion > ")
            if not q.strip():
                continue
            print(poser_question(q)["reponse"])
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir.")
            break