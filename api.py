"""
api.py — Mini serveur FastAPI pour DAMIA.

Expose src.assistant.poser_question à une page web custom (static/index.html).
La logique métier n'est PAS touchée : ce fichier ne fait qu'appeler poser_question,
extraire l'éventuel tableau structuré, et renvoyer du JSON propre au front.

Installation :
    pip install fastapi "uvicorn[standard]"

Lancement (depuis la racine du projet mcp_damia) :
    uvicorn api:app --port 8000
    puis ouvrir  http://localhost:8000

La clé du fournisseur cloud (Groq) doit être disponible via DAMIA_API_KEY (.env).
"""
import os
import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.assistant import poser_question
from src import backends as _backends

BASE = Path(__file__).parent
app = FastAPI(title="DAMIA")

# Modèles proposés : chaque entrée pose TOUTES les variables d'environnement du backend.
MODELES = {
    "groq": {
        "label": "Groq · llama-3.3-70b",
        "env": {
            "DAMIA_BACKEND": "openai",
            "DAMIA_BASE_URL": "https://api.groq.com/openai/v1",
            "DAMIA_MODELE": "llama-3.3-70b-versatile",
        },
    },
    "ollama": {
        "label": "Ollama · qwen2.5:7b",
        "env": {"DAMIA_BACKEND": "ollama", "DAMIA_MODELE": "qwen2.5:7b"},
    },
}

_MARQ_DEBUT = "<!--DAMIA_TABLE:"
_MARQ_FIN = "-->"


def extraire_table(reponse):
    """Sépare le texte lisible du tableau structuré éventuel (même logique que l'app)."""
    if not reponse or _MARQ_DEBUT not in reponse:
        return reponse, None
    try:
        d = reponse.index(_MARQ_DEBUT)
        f = reponse.index(_MARQ_FIN, d)
        table = json.loads(reponse[d + len(_MARQ_DEBUT):f])
        texte = reponse[:d].rstrip()
        titre = table.get("titre")
        if titre and texte.startswith(titre):
            texte = titre
        return texte, table
    except (ValueError, json.JSONDecodeError):
        return reponse, None


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
def choisir_modele(c: ChoixModele):
    if c.key not in MODELES:
        return JSONResponse({"ok": False, "error": "modèle inconnu"}, status_code=400)
    for v in ("DAMIA_BACKEND", "DAMIA_BASE_URL", "DAMIA_MODELE"):
        os.environ.pop(v, None)
    for var, val in MODELES[c.key]["env"].items():
        os.environ[var] = val
    try:
        _backends.get_backend(force_reload=True)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": True, "label": MODELES[c.key]["label"]}


@app.post("/api/ask")
def poser(q: Question):
    t0 = time.perf_counter()
    try:
        res = poser_question(q.question)
    except Exception as e:  # noqa: BLE001
        return {"texte": f"Une erreur est survenue : {e}", "table": None,
                "outil": None, "parametres": None, "hors_perimetre": False, "duree": 0.0}
    if isinstance(res, str):
        res = {"reponse": res}
    texte, table = extraire_table(res.get("reponse", "") or "")
    if table:
        cols = table.get("colonnes") or ["", ""]
        table["est_temporel"] = str(cols[0]).lower().startswith("ann")
    return {
        "texte": texte,
        "table": table,
        "outil": res.get("outil"),
        "parametres": res.get("parametres"),
        "hors_perimetre": bool(res.get("hors_perimetre")),
        "duree": round(time.perf_counter() - t0, 2),
    }


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
