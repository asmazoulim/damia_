"""Configuration centrale du projet MCP DAMIA."""
from pathlib import Path
import os

# ===================================================================
# À AJOUTER TOUT EN HAUT de config/config.py (avant tout le reste),
# pour charger automatiquement le fichier .env au démarrage.
# ===================================================================
import os
from pathlib import Path


def _charger_env():
    """Charge le fichier .env (racine du projet) dans os.environ.
    Ne remplace PAS une variable déjà définie dans l'environnement
    (priorité au terminal > .env). Sans dépendance externe."""
    racine = Path(__file__).parent.parent
    fichier = racine / ".env"
    if not fichier.exists():
        return
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        cle, val = cle.strip(), val.strip().strip('"').strip("'")
        if cle and cle not in os.environ:   # le terminal reste prioritaire
            os.environ[cle] = val


_charger_env()

# Racine du projet
RACINE = Path(__file__).parent.parent

# Dossiers
DOSSIER_DATA = RACINE / "data"
DOSSIER_CONFIG = RACINE / "config"

# Fichiers de donnees (POC : 2 CSV suffisent — Open DAMIR uniquement)
CHEMIN_DB = DOSSIER_DATA / "damia.duckdb"
CSV_FAITS = DOSSIER_DATA / "fact_damir_2022_2025.csv"
CSV_PRESTATIONS = DOSSIER_DATA / "dim_prestations.csv"
# (correspondance_ATC retiree : pas d'Open Medic dans le perimetre MCP)

# Dictionnaire semantique
CHEMIN_DICO = DOSSIER_CONFIG / "dictionnaire_semantique_damir.json"

# Modele Ollama
MODELE_OLLAMA = "qwen2.5:7b"   # "qwen2.5:7b" 
TEMPERATURE = 0.1              # bas = moins d'hallucination

# Colonnes de mesures (noms REELS de la table exportee d'EDEN)
# Table agregee : montants deja sommes via GROUP BY
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