"""
data_load.py — (Re)construit la base DuckDB du POC depuis les CSV sources.

Crée deux tables :
  - faits        <- fact_damir_2022_2025.csv  (séparateur ',')
  - prestations  <- dim_prestations.csv        (séparateur ';')

Ces noms de tables sont ceux attendus par tools.py (FROM faits f JOIN prestations p).
Idempotent : on remplace les tables si elles existent déjà.

USAGE :
    python src/data_load.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from config.config import CHEMIN_DB, CSV_FAITS, CSV_PRESTATIONS


def construire():
    for chemin in (CSV_FAITS, CSV_PRESTATIONS):
        if not Path(chemin).exists():
            print(f"ERREUR : CSV introuvable -> {chemin}")
            return

    con = duckdb.connect(str(CHEMIN_DB))  # lecture-écriture
    try:
        # Table de faits (CSV séparé par des virgules, en-tête entre guillemets)
        con.execute("DROP TABLE IF EXISTS faits")
        con.execute(f"""
            CREATE TABLE faits AS
            SELECT * FROM read_csv_auto('{CSV_FAITS}', header=true, delim=',')
        """)

        # Table de dimension (CSV séparé par des points-virgules, BOM possible)
        con.execute("DROP TABLE IF EXISTS prestations")
        con.execute(f"""
            CREATE TABLE prestations AS
            SELECT * FROM read_csv_auto('{CSV_PRESTATIONS}', header=true, delim=';')
        """)

        nb_f = con.execute("SELECT COUNT(*) FROM faits").fetchone()[0]
        nb_p = con.execute("SELECT COUNT(*) FROM prestations").fetchone()[0]

        # Contrôle de cohérence de la jointure (anti-mauvaise surprise)
        orphelins = con.execute("""
            SELECT COUNT(*) FROM faits f
            LEFT JOIN prestations p
              ON CAST(f.prs_nat AS VARCHAR) = CAST(p.prs_nat AS VARCHAR)
            WHERE p.prs_nat IS NULL
        """).fetchone()[0]

        print(f"OK  faits = {nb_f:,} lignes  |  prestations = {nb_p:,} lignes".replace(",", " "))
        if orphelins:
            print(f"  ⚠ {orphelins:,} lignes de faits sans prestation correspondante "
                  f"(prs_nat absent de la dimension).".replace(",", " "))
        else:
            print("  Jointure prs_nat : 100 % des faits ont une prestation correspondante.")
        print(f"Base écrite -> {CHEMIN_DB}")
    finally:
        con.close()


if __name__ == "__main__":
    construire()