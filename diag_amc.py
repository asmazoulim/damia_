"""
diag_amc.py — Diagnostic du cas glossaire AMC via le chemin EXACT du banc.
À placer à la racine du projet (mcp_damia\) et lancer :  python diag_amc.py
"""
import json
from src.assistant import poser_question

CAS = json.load(open("config/cas_tests.json", encoding="utf-8"))
c = next(x for x in CAS["cas"]
         if x.get("categorie") == "Glossaire" and "AMC" in x["question"])

print("question :", c["question"])
print("attendu  :", c.get("contient_attendu"))

r = poser_question(c["question"])
rep = (r.get("reponse") or "").lower()
manquants = [s for s in c.get("contient_attendu", []) if s.lower() not in rep]

print("outil    :", r.get("outil"))
print("params   :", r.get("parametres"))
print("manque   :", manquants)

routage_ok = r.get("outil") in c.get("outils_acceptes", [])
print("verdict  :", "OK" if routage_ok and not manquants else "ECHEC")
print("reponse  :", repr(r.get("reponse"))[:200])
