
# DAMIA — Assistant conversationnel Open DAMIR

DAMIA interroge en langage naturel les dépenses de l'**Assurance Maladie obligatoire** (base **Open DAMIR**, 2022-2025). L'utilisateur pose une question en français ; un modèle de langage l'interprète et **choisit un outil** ; le code exécute la requête sur la base et renvoie un résultat chiffré, avec tableau et graphique.

Le serveur expose ses outils via le protocole **MCP** (Model Context Protocol) en HTTP — n'importe quel client MCP compatible peut s'y connecter avec son propre modèle. **Aucun modèle n'est embarqué** dans le serveur : le client apporte son IA.

## Qu'est-ce que DAMIA ?

DAMIA est un **assistant conversationnel outillé** (tool-using), et non un agent : il traduit une question en **un appel d'outil unique**, exécuté de façon déterministe par le code. Il ne planifie pas et n'enchaîne pas d'actions de façon autonome. Ce choix privilégie la fiabilité à l'autonomie.

Son principe directeur est l'**absence d'hallucination numérique** : le modèle ne produit jamais un chiffre. Il se limite à désigner un outil et ses paramètres ; c'est le code Python, via **DuckDB**, qui calcule la valeur exacte. Séparer l'*interprétation* (le modèle) de l'*exécution* (le code) est la garantie de fiabilité du système.

DAMIA est en outre **model-agnostic** : le moteur de raisonnement est interchangeable (Ollama en local, Groq et autres fournisseurs OpenAI-compatibles, Claude, Gemini…) via une simple variable d'environnement, sans toucher au code métier.

## Fonctionnement

Chaque question traverse la fonction d'orchestration `poser_question`, qui applique quatre étapes dans l'ordre :

1. **Routage méta** — question sur l'assistant lui-même (périmètre, sources, années, capacités) : réponse explicative construite à partir du dictionnaire.
2. **Routage définition** — demande de définition (« que veut dire X ») : appel direct au glossaire, même sur un terme hors périmètre (définir n'est pas calculer).
3. **Garde-fou** — refus des demandes hors périmètre (complémentaire/AMC, esthétique, médical, individuel ; année ou valeur hors référentiel). La validation est **fail-closed et vit dans les outils eux-mêmes** : elle protège donc même lorsqu'un client tiers appelle un outil sans passer par l'orchestrateur.
4. **Routage LLM** — le modèle choisit **un** outil dans la liste découverte via MCP et en remplit les paramètres (sortie JSON stricte, validée). L'outil est ensuite appelé **via le protocole MCP**, interroge DuckDB et renvoie un texte accompagné, si pertinent, d'un tableau structuré.

Le serveur est **interopérable sur deux axes** : le **modèle** (interchangeable) et le **client** — les mêmes outils sont consommés indifféremment par le client DAMIA et par un client tiers standard comme **Claude Desktop**.

## Outils exposés par le serveur MCP

Le serveur expose **10 outils** ; leurs descriptions sont lues par le client/modèle pour décider lequel appeler.

- **`query_depenses`** — calcule une mesure (montant remboursé, dépense engagée, nombre d'actes…) avec filtres optionnels : poste, sous-catégorie, découpage fin (verres, montures, lentilles), année, région, âge, sexe.
- **`repartition`** — ventile une mesure selon une dimension (poste, région, âge, sexe).
- **`evolution_serie`** — évolution d'une mesure sur toutes les années du périmètre.
- **`compare_periods`** — compare une mesure entre deux années précises et calcule l'évolution en %.
- **`top_postes`** — classement des postes de soin les plus remboursés (top N).
- **`taux_couverture`** — part remboursée par l'Assurance Maladie rapportée à la dépense totale.
- **`get_dictionnaire`** — définition d'un terme métier (AMO, AMC, C2S, ticket modérateur…), avec référence légale et mention de périmètre lorsque applicable.
- **`list_valeurs`** — valeurs possibles d'une dimension (poste, région, âge, sexe, année).
- **`decrire_variable`** — décrit une variable du référentiel Open DAMIR (libellé, catégorie, modalités, statut d'exploitation). Ex. `PRS_REM_TYP`, `EXO_MTF`.
- **`lister_variables`** — liste les variables Open DAMIR, filtrables par catégorie, en distinguant celles exploitées de celles seulement documentées.

Tous les outils qui calculent une valeur sont **fail-closed** : un poste inconnu, une valeur ou une année hors périmètre renvoie un refus explicite, jamais un chiffre inventé. Les outils n'exposent que des paramètres agrégés — aucune requête individuelle n'est possible par construction.

## Interfaces

Deux interfaces se branchent sur le même moteur (`poser_question`) :

**Interface Streamlit** (`app.py`) — chat avec rendu des tableaux, graphiques aux couleurs de la charte (barres triées pour les répartitions, colonnes pour les séries, courbe ou camembert au choix), export **CSV** et **PNG**, sélecteur de moteur d'IA à chaud, et un panneau **« Log d'exécution technique »** sous chaque réponse exposant l'outil appelé et ses paramètres — la preuve visible que le chiffre vient de la base.

**Interface web personnalisée** (`api.py` + `static/index.html`) — un serveur **FastAPI** expose `poser_question`, et une page autonome offre une mise en page plein écran, un sélecteur de modèle dans l'en-tête, et des **graphiques interactifs (barres / courbe / camembert au choix)** pour toute réponse contenant un tableau, avec export CSV et PNG. Elle vise un rendu proche des assistants professionnels, hors des contraintes de mise en page de Streamlit.

Dans les deux cas, le **rendu riche** (graphiques, exports, routage déterministe, marque) relève de l'interface, non du protocole : un client tiers comme Claude Desktop consomme les mêmes outils mais affiche un rendu brut.

## Périmètre des données

Open DAMIR 2022-2025, part **Assurance Maladie obligatoire (AMO)**, tous régimes. Sources : Open DAMIR, Open Medic, Open LPP. Sont **hors périmètre** (et refusés) : la part complémentaire (AMC / mutuelles), les questions médicales, les données individuelles, les actes à visée esthétique, ainsi que toute année ou valeur hors référentiel. Certaines notions (AME, ALD, motif d'exonération) sont **définies mais pas encore requêtables** — leur intégration est prévue.

## Lancer le serveur

Endpoint MCP : `/mcp`. Le transport est configurable :

```bash
# HTTP (par défaut) — pour le client DAMIA et les clients distants
python src/mcp_server.py

# stdio — pour Claude Desktop (qui lance le serveur en sous-processus)
MCP_TRANSPORT=stdio python src/mcp_server.py
```

Variables : `MCP_HOST` (défaut `0.0.0.0`), `MCP_PORT` (défaut `7860`), `MCP_TRANSPORT` (défaut `streamable-http`).

Côté client, l'URL du serveur se règle avec `DAMIA_MCP_URL` (défaut `http://127.0.0.1:7860/mcp`) — c'est ce qui permet de basculer entre serveur local et Space Hugging Face sans toucher au code.

## Fédération de plusieurs sources

Le client n'est pas lié à un serveur unique. `DAMIA_MCP_SOURCES` déclare N serveurs, interrogés par la même voie :

```bash
DAMIA_MCP_SOURCES="damir=http://127.0.0.1:7860/mcp,medic=http://127.0.0.1:7861/mcp"
```

Les outils sont alors présentés au modèle sous un **nom qualifié** `source.outil` (`damir.query_depenses`, `medic.query_depenses`). Sans cette qualification, deux sources exposant le même nom d'outil seraient indiscernables au moment du routage. Un nom court reste accepté tant qu'il n'est pas ambigu ; s'il l'est, l'appel est **refusé** plutôt que résolu au hasard — un chiffre juste issu de la mauvaise base est une erreur silencieuse.

Une source injoignable est signalée et mise de côté : les autres restent servies. L'assistant ne refuse de démarrer que si aucune source n'est joignable.

## Choix du moteur (model-agnostic)

Le moteur se sélectionne via `DAMIA_BACKEND` (et `DAMIA_MODELE`) :

```bash
# Local, souverain
DAMIA_BACKEND=ollama  DAMIA_MODELE=qwen2.5:7b

# Cloud rapide (connecteur OpenAI-compatible : Groq, Mistral…)
DAMIA_BACKEND=openai  DAMIA_BASE_URL=https://api.groq.com/openai/v1  DAMIA_MODELE=openai/gpt-oss-120b
```

Les clés API sont fournies via un fichier `.env` (voir `.env.example`, à copier en `.env` ; le `.env` n'est jamais versionné).

## Structure du dépôt

```
config/config.py      chemins, colonnes de mesure, chargement du .env
config/dictionnaire_semantique_damir.json
                      SOURCE UNIQUE du vocabulaire métier : postes, synonymes,
                      filtres SQL, glossaire, hors-périmètre. Le modifier change
                      le comportement sans toucher au code.
src/mcp_server.py     déclare les 10 outils MCP (signatures + descriptions)
src/tools.py          logique métier : requêtes DuckDB, validation fail-closed
src/assistant.py      orchestrateur : routage amont + appel du modèle + MCP
src/mcp_client.py     client MCP synchrone (boucle asyncio dans un thread dédié)
src/backends.py       abstraction multi-modèles (Ollama, Groq, Claude, Gemini…)
src/formatage.py      formatage des valeurs + tableau structuré (source unique)
src/normalisation.py  normalisation de texte (accents, tirets) — source unique
src/graphiques.py     rendu PNG matplotlib
src/data_load.py      (re)construit la base DuckDB depuis les CSV
app.py                interface Streamlit
api.py + static/      interface web FastAPI
tests/banc_test.py    banc de test de bout en bout -> rapports/ (HTML + CSV)
tests/test_client.py  test de la chaîne MCP, sans modèle de langage
```

## Installation

```bash
pip install -r requirements.txt          # développement : interfaces + backends
pip install -r requirements-dev.txt      # + pytest (suite automatique)
pip install -r requirements-server.txt   # serveur MCP seul (utilisé par le Dockerfile)
```

## Lancer l'assistant

Le serveur MCP doit tourner dans un terminal séparé — c'est lui qui porte les données :

```bash
python src/mcp_server.py       # terminal 1, à laisser ouvert
```

Puis, au choix :

```bash
python src/assistant.py        # chat dans le terminal
streamlit run app.py           # interface Streamlit
uvicorn api:app --port 8000    # interface web  ->  http://localhost:8000
```

## Tests

```bash
python -m pytest               # 153 tests, ~3 s, sans LLM ni serveur
python tests/test_client.py    # chaîne MCP -> DuckDB, sans LLM (déterministe)
python tests/banc_test.py      # bout en bout avec le modèle -> rapports/
python benchmark_backends.py   # comparaison de vitesse entre backends
```

`banc_test.py` propose un menu de choix du moteur ; `python tests/banc_test.py groq-20b` impose un preset (voir `src/modeles.py`), `--env` garde la configuration du `.env`.
