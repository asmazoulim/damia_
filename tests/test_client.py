"""
Client de test MCP — valide la chaine complete : connexion HTTP -> serveur ->
outils -> DuckDB, sans passer par un modele de langage.

PREREQUIS (pour un test local) : le serveur tourne dans un AUTRE terminal :
    python src/mcp_server.py

Puis :
    python tests/test_client.py                     # local (ou DAMIA_MCP_URL)
    python tests/test_client.py https://<compte>-damia-mcp.hf.space/mcp

NB : l'endpoint est /mcp (transport streamable-http), pas /sse.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from src.mcp_client import URL_DEFAUT

# URL : argument de ligne de commande > DAMIA_MCP_URL > defaut local.
URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DAMIA_MCP_URL", URL_DEFAUT)

# (outil, parametres, ce qu'on attend de voir)
TESTS = [
    ("query_depenses", {"mesure": "montant_rembourse", "annee": 2023}, "~128 Md€"),
    ("top_postes", {"mesure": "montant_rembourse", "annee": 2023, "n": 5},
     "Pharmacie ~29,72 Md€"),
    ("evolution_serie", {"mesure": "montant_rembourse", "poste": "dentaire"}, "2022→2025"),
    ("repartition", {"mesure": "montant_rembourse", "dimension": "poste", "annee": 2023},
     "ventilation par poste"),
    # Cas negatif : le refus fait partie du contrat, au meme titre qu'un chiffre juste.
    ("query_depenses", {"mesure": "montant_rembourse", "poste": "mutuelle"},
     "REFUS (hors périmètre)"),
]


async def main():
    print(f"→ Connexion à {URL}\n")
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✓ Connexion établie et session initialisée\n")

            outils = await session.list_tools()
            noms = [t.name for t in outils.tools]
            print(f"✓ {len(noms)} outils exposés : {', '.join(noms)}\n" + "-" * 60)

            for nom, params, attendu in TESTS:
                if nom not in noms:
                    print(f"⚠ Outil '{nom}' absent du serveur — ignoré")
                    continue
                res = await session.call_tool(nom, params)
                texte = res.content[0].text if res.content else "(vide)"
                print(f"\n▶ {nom}({params})")
                print(f"  attendu : {attendu}")
                print(f"  réponse : {texte.split('<!--DAMIA_TABLE')[0].strip()}")

    print("\n" + "-" * 60)
    print("✓ Test terminé. Si les réponses ci-dessus sont cohérentes, la chaîne")
    print("  (client → serveur HTTP → outils → DuckDB) est validée.")


if __name__ == "__main__":
    asyncio.run(main())
