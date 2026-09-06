"""
Serveur MCP DAMIA — version DÉPLOYABLE (streamable-http, model-agnostic).

- Transport par défaut : streamable-http (route **/mcp**), compatible proxy d'entreprise.
- stateless_http=True + json_response=True : robuste derrière le routage HF
  (chaque requête est autonome, pas de session en mémoire à retrouver).
- Route GET / : répond 200 pour satisfaire le health check de Hugging Face.
  Un serveur MCP n'a pas de page d'accueil ; sans cette route, / renvoie 404
  et HF peut couper le container.

Ce module ne contient AUCUNE logique métier : il se contente de déclarer la
signature et la description de chaque outil (ce que le modèle lit pour choisir),
et délègue à src/tools.py. Les descriptions sont donc du prompt : les modifier
change le routage du modèle.

Lancement :
    python src/mcp_server.py                       # HTTP streamable-http (/mcp)
    MCP_TRANSPORT=stdio python src/mcp_server.py   # stdio (Claude Desktop)
Variables : MCP_HOST (0.0.0.0), MCP_PORT (7860), MCP_TRANSPORT (streamable-http)
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP
from starlette.responses import PlainTextResponse

from src import tools

logging.basicConfig(level=os.environ.get("DAMIA_LOG_LEVEL", "INFO"),
                    format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("damia.mcp")

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "7860"))

mcp = FastMCP("DAMIA", host=HOST, port=PORT,
              stateless_http=True,   # robuste derriere le routage HF (pas de session a retrouver)
              json_response=True)    # reponses JSON simples (pas de flux SSE a maintenir)


@mcp.custom_route("/", methods=["GET"])
async def racine(request):
    """Health check Hugging Face — le serveur MCP n'a pas de page d'accueil."""
    return PlainTextResponse(
        "DAMIA — serveur MCP Open DAMIR. Endpoint MCP : /mcp (streamable-http). "
        "Ce serveur se consomme via un client MCP, pas dans un navigateur."
    )


# --------------------------------------------------------------------------
# Outils exposés
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
      ou LENTILLES en optique (ex. decoupage='verres')."""
    return tools.query_depenses(mesure, poste, annee, region, age, sexe,
                                sous_categorie, decoupage)


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
    Pour 'quels sont les postes qui coûtent le plus', 'top 5 postes'.
    Préciser l'année pour 'top 5 postes en 2023'."""
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
    À utiliser pour « évolution de X », « tendance de X », « X au fil du temps ».
    Peut cibler un poste, une sous_categorie ou un decoupage."""
    return tools.evolution_serie(mesure, poste, sous_categorie, decoupage)


@mcp.tool()
def taux_couverture(poste: str | None = None, annee: int | None = None) -> str:
    """Calcule le taux de couverture (part remboursée par l'AM / dépense totale).
    Pour 'quel est le taux de couverture pour X en 2023'."""
    return tools.taux_couverture(poste, annee)


@mcp.tool()
def decrire_variable(nom: str) -> str:
    """Décrit une variable du référentiel Open DAMIR (schéma, pas données) :
    libellé, catégorie, description, modalités possibles et statut d'exploitation.
    Ex. decrire_variable('PRS_REM_TYP'), decrire_variable('EXO_MTF').
    Répond aux questions « que contient la variable X », « quelles modalités pour X »."""
    return tools.decrire_variable(nom)


@mcp.tool()
def lister_variables(categorie: str | None = None) -> str:
    """Liste les variables du référentiel Open DAMIR, éventuellement filtrées par
    catégorie (periode, beneficiaire, prestation, executant, prescripteur...).
    Sans argument : liste les catégories. Répond à « quelles variables existent »."""
    return tools.lister_variables(categorie)


if __name__ == "__main__":
    import asyncio

    try:
        outils = asyncio.run(mcp.list_tools())
        log.info("%d outils enregistres : %s", len(outils), [t.name for t in outils])
    except Exception:
        log.exception("Impossible de lister les outils au demarrage")

    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    log.info("Demarrage transport=%s host=%s port=%s", transport, HOST, PORT)
    mcp.run(transport=transport)
