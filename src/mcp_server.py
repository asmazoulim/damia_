"""
Serveur MCP DAMIA — version DÉPLOYABLE (transport HTTP, model-agnostic).

Différence avec la version locale (stdio) : ce serveur écoute en HTTP, donc
N'IMPORTE QUEL client MCP compatible peut s'y connecter à distance, avec SON
PROPRE modèle. Le serveur n'embarque aucun LLM : il n'expose que des outils.

Les docstrings des outils ci-dessous sont LUES par le client/modèle pour décider
quel outil appeler : elles doivent rester claires et précises.

Lancement :
    python src/mcp_server.py
Variables d'environnement :
    MCP_HOST (défaut 0.0.0.0), MCP_PORT (défaut 7860)
Endpoint exposé : http://<hote>:<port>/mcp   (transport streamable-http)
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP
from src import tools

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "7860"))

mcp = FastMCP("DAMIA", host=HOST, port=PORT)


# --------------------------------------------------------------------------
# Outils existants
# --------------------------------------------------------------------------
@mcp.tool()
def query_depenses(mesure: str = "montant_rembourse", poste: str | None = None,
                   annee: int | None = None, region: str | None = None,
                   age: str | None = None, sexe: str | None = None,
                   sous_categorie: str | None = None,
                   decoupage: str | None = None) -> str:
    """Calcule une mesure (montant_rembourse, depense_engagee, nombre_actes...)
    avec filtres optionnels. Trois niveaux de finesse possibles :
    - poste : grand poste de soin (optique, dentaire, pharmacie...).
    - sous_categorie : sous-catégorie précise (ex. 'Prothèse RAC 0', 'Soins Conservateurs').
    - decoupage : distinction fine au niveau prestation, pour les VERRES, MONTURES
      ou LENTILLES en optique (ex. decoupage='verres'). Utiliser ce paramètre
      pour toute question distinguant verres et montures."""
    return tools.query_depenses(mesure, poste, annee, region, age, sexe,
                                sous_categorie, decoupage)


@mcp.tool()
@mcp.tool()
def compare_periods(annee1: int | None = None, annee2: int | None = None,
                    poste: str | None = None,
                    mesure: str = "montant_rembourse") -> str:
    """Compare une mesure entre DEUX années PRÉCISES et calcule l'évolution en %.
    À utiliser UNIQUEMENT si l'utilisateur cite deux années (ex. « entre 2022 et 2024 »).
    Pour une évolution sur toute la période, utiliser evolution_serie à la place."""
    return tools.compare_periods(poste, annee1, annee2, mesure)


@mcp.tool()
def get_dictionnaire(terme: str) -> str:
    """Renvoie la définition d'un terme métier DAMIR (mesure, dimension, poste, panier)."""
    return tools.get_dictionnaire(terme)


@mcp.tool()
def list_valeurs(dimension: str) -> str:
    """Liste les valeurs possibles d'une dimension : poste, region, age, sexe ou annee."""
    return tools.list_valeurs(dimension)

@mcp.tool()
def top_postes(mesure: str = "montant_rembourse", annee: int | None = None, n: int = 5) -> str:
    """Classement des postes de soin par mesure (top N).
    Pour les questions du type 'quels sont les postes qui coûtent le plus', 'top 5 postes'.
    Pour les questions du type 'quels sont les postes qui coûtent le plus en 2023', préciser l'année.
    Pour les questions du type 'quel poste est le plus remboursé en 2023', 
    répondre Le poste X est le plus remboursé en 2023 à hauteur de Y euros."""
    return tools.top_postes(mesure, annee, n)


@mcp.tool()
def repartition(mesure: str = "montant_rembourse", dimension: str = "poste",
                annee: int | None = None, poste: str | None = None,
                sous_categorie: str | None = None, decoupage: str | None = None) -> str:
    """Ventile une mesure selon une dimension : 'poste', 'region', 'age' ou 'sexe'.
    Peut être restreinte à un poste, une sous_categorie, ou un decoupage fin
    (verres, montures, lentilles). Ex. répartition des verres par région."""
    return tools.repartition(mesure, dimension, annee, poste, sous_categorie, decoupage)


@mcp.tool()
def evolution_serie(mesure: str = "montant_rembourse", poste: str | None = None,
                    sous_categorie: str | None = None, decoupage: str | None = None) -> str:
    """Évolution d'une mesure sur TOUTES les années disponibles (2022-2025).
    À utiliser pour « évolution de X », « tendance de X », « X au fil du temps »,
    sans années précises. Peut cibler un poste, une sous_categorie ou un decoupage."""
    return tools.evolution_serie(mesure, poste, sous_categorie, decoupage)

@mcp.tool()
def taux_couverture(poste: str | None = None, annee: int | None = None) -> str:
    """Calcule le taux de couverture (part remboursée par l'AM / dépense totale).
    Pour 'quel est le taux de couverture pour X en 2023'."""
    return tools.taux_couverture(poste, annee)


if __name__ == "__main__":
    # transport HTTP (streamable-http) -> accessible par tout client MCP distant
    mcp.run(transport="sse")