"""Configuration centrale du projet MCP DAMIA.

Charge le .env au premier import, puis expose les chemins et les noms de
colonnes de mesure. Importer ce module a un effet de bord volontaire (peupler
os.environ) : c'est ce qui permet a tous les points d'entree — serveur MCP,
Streamlit, FastAPI, bancs de test — de partager la meme configuration sans
que chacun ait a charger le .env lui-meme.
"""
import os
from pathlib import Path

# Racine du projet (config/ est un sous-dossier direct)
RACINE = Path(__file__).parent.parent


def _charger_env():
    """Charge le fichier .env (racine du projet) dans os.environ.

    Ne remplace PAS une variable deja definie dans l'environnement : le
    terminal reste prioritaire sur le .env. Sans dependance externe (pas de
    python-dotenv a installer)."""
    fichier = RACINE / ".env"
    if not fichier.exists():
        return
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        cle, val = cle.strip(), val.strip().strip('"').strip("'")
        if cle and cle not in os.environ:
            os.environ[cle] = val


_charger_env()

# Dossiers
DOSSIER_DATA = RACINE / "data"
DOSSIER_CONFIG = RACINE / "config"

# Fichiers de donnees (POC : 2 CSV suffisent — Open DAMIR uniquement)
CHEMIN_DB = DOSSIER_DATA / "damia.duckdb"
CSV_FAITS = DOSSIER_DATA / "fact_damir_2022_2025.csv"
CSV_PRESTATIONS = DOSSIER_DATA / "dim_prestations.csv"

# Dictionnaire semantique : SOURCE UNIQUE du vocabulaire metier (postes,
# synonymes, mesures, hors-perimetre). Le modifier change le comportement.
CHEMIN_DICO = DOSSIER_CONFIG / "dictionnaire_semantique_damir.json"

# Modele Ollama par defaut (surchargeable par DAMIA_MODELE)
MODELE_OLLAMA = os.environ.get("DAMIA_MODELE_OLLAMA", "qwen2.5:7b")
TEMPERATURE = 0.1              # bas = moins d'hallucination

# Colonnes de mesures (noms REELS de la table exportee d'EDEN).
# Table agregee : les montants sont deja sommes via GROUP BY.
COL_REMBOURSE = "mt_rembourse"        # montant rembourse
COL_DEPENSE = "mt_paiement_total"     # paiement total (depense engagee)
COL_ACTES = "total_actes_qte"         # nombre d'actes (quantite)
COL_BASE = "base_remboursement"       # base de remboursement
COL_DEPASSEMENT = "mt_depassement"    # depassement

MESURES = {
    "montant_rembourse": COL_REMBOURSE,
    "depense_engagee": COL_DEPENSE,
    "nombre_actes": COL_ACTES,
    "base_remboursement": COL_BASE,
    "depassement": COL_DEPASSEMENT,
}
