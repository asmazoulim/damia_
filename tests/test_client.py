"""
Client de test MCP — valide la connexion HTTP au serveur DAMIA et l'appel d'outils.

PREREQUIS : le serveur doit tourner dans un AUTRE terminal :
    python src/mcp_server.py

Puis, dans un second terminal :
    python test_client.py
    (ou, pour viser la Space HF déployée :)
    python test_client.py https://<ton-compte>-mcp-open-damir.hf.space/mcp

Dépendance : la lib 'mcp' (déjà dans ton requirements). Si besoin :
    pip install mcp
"""
import sys
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# URL de l'endpoint MCP : local par défaut, ou passée en argument
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:7860/mcp"

# Questions de validation : (outil, paramètres, ce qu'on attend)
TESTS = [
    ("query_depenses",  {"mesure": "montant_rembourse", "annee": 2023},                 "~128 Md€"),
    ("top_postes",      {"mesure": "montant_rembourse", "annee": 2023, "n": 5},          "Pharmacie ~29,72 Md€"),
    ("evolution_serie", {"mesure": "montant_rembourse", "poste": "dentaire"},            "2022→2025"),
    ("repartition",     {"mesure": "montant_rembourse", "dimension": "poste", "annee": 2023}, "ventilation postes"),
    ("query_depenses",  {"mesure": "montant_rembourse", "poste": "mutuelle"},            "REFUS (hors périmètre)"),
]


async def main():
    print(f"→ Connexion à {URL}\n")
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✓ Connexion établie et session initialisée\n")

            # 1) Lister les outils exposés par le serveur
            outils = await session.list_tools()
            noms = [t.name for t in outils.tools]
            print(f"✓ {len(noms)} outils exposés : {', '.join(noms)}\n")
            print("-" * 60)

            # 2) Appeler chaque outil de test
            for nom, params, attendu in TESTS:
                if nom not in noms:
                    print(f"⚠ Outil '{nom}' absent du serveur — ignoré")
                    continue
                res = await session.call_tool(nom, params)
                texte = res.content[0].text if res.content else "(vide)"
                print(f"\n▶ {nom}({params})")
                print(f"  attendu : {attendu}")
                print(f"  réponse : {texte}")

    print("\n" + "-" * 60)
    print("✓ Test terminé. Si les réponses ci-dessus sont cohérentes, la chaîne complète")
    print("  (client → serveur HTTP → outils → DuckDB) est validée.")


if __name__ == "__main__":
    asyncio.run(main())
