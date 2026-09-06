"""
api.py — Mini serveur FastAPI pour DAMIA.

Expose src.assistant.poser_question à une page web autonome (static/index.html).
La logique métier n'est PAS touchée : ce fichier appelle poser_question, sépare
l'éventuel tableau structuré (via src/formatage.py, le même code que l'app
Streamlit) et renvoie du JSON au front.

Installation :
    pip install -r requirements.txt

Lancement (depuis la racine du projet) :
    uvicorn api:app --port 8000
    puis ouvrir  http://localhost:8000

Les clés des fournisseurs cloud sont lues depuis .env via config/config.py.
"""
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import backends, modeles
from src.assistant import poser_question
from src.formatage import extraire_table

log = logging.getLogger("damia.api")

BASE = Path(__file__).parent
app = FastAPI(title="DAMIA")

# Catalogue partage avec tests/banc_test.py : un seul endroit ou declarer un
# moteur, sinon l'interface et le banc finiraient par tester des choses
# differentes sous le meme nom.
MODELES = modeles.CATALOGUE


class Question(BaseModel):
    question: str


class ChoixModele(BaseModel):
    key: str


@app.get("/")
def index():
    return FileResponse(str(BASE / "static" / "index.html"))


@app.get("/api/models")
def liste_modeles():
    courant = os.environ.get("DAMIA_BACKEND", "ollama").lower()
    actif = "groq" if courant in ("openai", "groq") else "ollama"
    return {"actif": actif,
            "models": [{"key": k, "label": v["label"]} for k, v in MODELES.items()]}


@app.post("/api/model")
def choisir_modele(choix: ChoixModele):
    if choix.key not in MODELES:
        return JSONResponse({"ok": False, "error": "modèle inconnu"}, status_code=400)

    modeles.appliquer(choix.key)

    try:
        backends.get_backend(force_reload=True)
    except Exception as e:
        log.exception("Bascule de backend vers %s echouee", choix.key)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": True, "label": MODELES[choix.key]["label"]}


@app.post("/api/ask")
def poser(q: Question):
    t0 = time.perf_counter()
    try:
        res = poser_question(q.question)
    except Exception as e:
        # Une panne du moteur ne doit pas se traduire par un 500 muet cote page :
        # on renvoie un message affichable, et la trace part dans les logs serveur.
        log.exception("poser_question a echoue")
        return {"texte": f"Une erreur est survenue : {e}", "table": None,
                "outil": None, "parametres": None, "hors_perimetre": False,
                "duree": round(time.perf_counter() - t0, 2)}

    texte, table = extraire_table(res.get("reponse") or "", couper_enumeration=True)
    if table:
        colonnes = table.get("colonnes") or ["", ""]
        table["est_temporel"] = str(colonnes[0]).lower().startswith("ann")

    return {
        "texte": texte,
        "table": table,
        "outil": res.get("outil"),
        "parametres": res.get("parametres"),
        "hors_perimetre": bool(res.get("hors_perimetre")),
        "duree": round(time.perf_counter() - t0, 2),
    }


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
