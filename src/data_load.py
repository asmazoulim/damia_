"""
data_load.py — (Re)construit la base DuckDB du POC depuis les CSV sources.

Cree deux tables :
  - faits        <- fact_damir_2022_2025.csv  (separateur ',')
  - prestations  <- dim_prestations.csv       (separateur ';')

Ces noms sont ceux attendus par tools.py (FROM faits f JOIN prestations p).
Idempotent : les tables sont remplacees si elles existent deja.

USAGE :
    python src/data_load.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

from config.config import CHEMIN_DB, CSV_FAITS, CSV_PRESTATIONS


def construire():
    manquants = [c for c in (CSV_FAITS, CSV_PRESTATIONS) if not Path(c).exists()]
    if manquants:
        raise SystemExit("CSV introuvable(s) :\n  "
                         + "\n  ".join(str(c) for c in manquants))

    CHEMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(CHEMIN_DB))     # lecture-ecriture
    try:
        # Les chemins sont passes en PARAMETRE, pas interpoles dans le SQL.
        con.execute("DROP TABLE IF EXISTS faits")
        con.execute("CREATE TABLE faits AS "
                    "SELECT * FROM read_csv_auto(?, header=true, delim=',')",
                    [str(CSV_FAITS)])

        con.execute("DROP TABLE IF EXISTS prestations")
        con.execute("CREATE TABLE prestations AS "
                    "SELECT * FROM read_csv_auto(?, header=true, delim=';')",
                    [str(CSV_PRESTATIONS)])

        nb_faits = con.execute("SELECT COUNT(*) FROM faits").fetchone()[0]
        nb_prestations = con.execute("SELECT COUNT(*) FROM prestations").fetchone()[0]

        # Controle de coherence de la jointure : une prs_nat absente de la
        # dimension ferait disparaitre des faits de TOUS les resultats en silence.
        orphelins = con.execute("""
            SELECT COUNT(*) FROM faits f
            LEFT JOIN prestations p
              ON CAST(f.prs_nat AS VARCHAR) = CAST(p.prs_nat AS VARCHAR)
            WHERE p.prs_nat IS NULL
        """).fetchone()[0]

        print(f"OK  faits = {nb_faits:,} lignes  |  "
              f"prestations = {nb_prestations:,} lignes".replace(",", " "))
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
