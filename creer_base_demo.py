"""
creer_base_demo.py — Genere une base DuckDB ALLEGEE pour la demo / la Space HF.

La table `faits` est tres fine (croisement de 11 dimensions). On la RE-AGREGE sur
les seules dimensions exploitees par les outils : les totaux restent EXACTEMENT
identiques, mais le nombre de lignes chute fortement. Le controle affiche en fin
d'execution le verifie (« Ecart » doit valoir ~0).

A lancer depuis la RACINE du projet :
    python creer_base_demo.py

Produit data/damia_demo.duckdb, a renommer data/damia.duckdb sur la Space.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import duckdb

from config.config import DOSSIER_DATA

SOURCE = DOSSIER_DATA / "damia.duckdb"
CIBLE = DOSSIER_DATA / "damia_demo.duckdb"

# Dimensions conservees = celles que les outils savent filtrer ou ventiler.
# Retirer une dimension d'ici la rend inutilisable comme filtre : c'est le
# compromis assume entre taille de la base et finesse d'analyse.
DIMENSIONS_GARDEES = [
    "soi_moi",        # mois (evolution mensuelle)
    "soi_ann",        # annee
    "prs_nat",        # nature de prestation -> jointure vers `prestations` (postes)
    "age_ben_snds",   # age
    "ben_sex_cod",    # sexe
    "ben_res_reg",    # region
]
MESURES_SOMMEES = [
    "total_actes_nbr", "total_actes_qte", "total_actes_cog",
    "mt_rembourse", "base_remboursement", "mt_depassement", "mt_paiement_total",
    "nb_lignes_source",
]


def main():
    if not SOURCE.exists():
        raise SystemExit(f"Base source introuvable : {SOURCE} "
                         f"(lance depuis la racine du projet)")
    if CIBLE.exists():
        CIBLE.unlink()

    dims = ", ".join(DIMENSIONS_GARDEES)
    sommes = ", ".join(f"SUM({m}) AS {m}" for m in MESURES_SOMMEES)

    # On ouvre la CIBLE en ecriture et on y ATTACHE la source en lecture seule :
    # la base d'origine ne peut donc pas etre modifiee par erreur.
    con = duckdb.connect(str(CIBLE))
    try:
        con.execute("ATTACH ? AS src (READ_ONLY)", [str(SOURCE)])

        n_avant = con.execute("SELECT COUNT(*) FROM src.faits").fetchone()[0]
        total_source = con.execute("SELECT SUM(mt_rembourse) FROM src.faits").fetchone()[0]

        con.execute(f"CREATE TABLE faits AS SELECT {dims}, {sommes} "
                    f"FROM src.faits GROUP BY {dims}")
        con.execute("CREATE TABLE prestations AS SELECT * FROM src.prestations")

        n_apres = con.execute("SELECT COUNT(*) FROM faits").fetchone()[0]
        total_demo = con.execute("SELECT SUM(mt_rembourse) FROM faits").fetchone()[0]

        con.execute("DETACH src")
    finally:
        con.close()

    taille_mo = CIBLE.stat().st_size / 1e6
    reduction = 100 * (1 - n_apres / n_avant) if n_avant else 0
    print("=== Base de démo générée ===")
    print(f"Lignes faits  : {n_avant:,} -> {n_apres:,}  (réduction {reduction:.1f} %)")
    print(f"Total remboursé source : {total_source:,.2f}")
    print(f"Total remboursé démo   : {total_demo:,.2f}")
    print(f"Écart : {abs(total_source - total_demo):,.4f}  (doit être ~0)")
    print(f"Fichier : {CIBLE}  ({taille_mo:.1f} Mo)")


if __name__ == "__main__":
    main()
