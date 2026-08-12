"""
src/assistant.py — Orchestrateur multi-modele, client MCP (VOIE 2).

CHANGEMENT CLE vs version precedente :
  - NE FAIT PLUS "from src import tools" ni FONCTIONS = {...}.
  - Decouvre les outils DYNAMIQUEMENT via le protocole MCP (mcp_client).
  - Construit la description des outils pour le prompt A PARTIR de cette decouverte.
  => Les appels passent reellement par MCP : c'est la preuve d'interoperabilite.

Le modele (Qwen/Ollama via backends.py) PROPOSE un outil en JSON ; le code l'appelle
via MCP. Si demain Claude Desktop parle au meme serveur, il voit les memes outils.

ROUTAGE EN AMONT (avant le modele), dans poser_question() :
  1. META      : questions sur le perimetre / les sources / les annees / les capacites
                 -> reponse explicative (et non un refus sec).
  2. DEFINITION : « que veut dire X / c'est quoi X » -> get_dictionnaire directement,
                 meme si X est un terme AMC (definir n'est pas hors perimetre ;
                 seul CALCULER des montants AMC l'est).
  3. GARDE-FOU : refus des demandes hors perimetre (AMC data, esthetique, medical, individuel).
  4. MODELE    : routage LLM vers un outil MCP.

Usage :
    from src.assistant import poser_question
    res = poser_question("Taux de couverture de l'optique en 2023 ?")
"""
import sys
import json
import re
import unicodedata
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backends import get_backend
from src.mcp_client import get_mcp_client

from config.config import CHEMIN_DICO

# Dictionnaire charge une fois : on garde les sous-sections utiles au routage amont.
_DICO_FULL = json.load(open(CHEMIN_DICO, encoding="utf-8"))
_DICO_HP = _DICO_FULL.get("hors_perimetre", {})
_DICO_PERIM = _DICO_FULL.get("perimetre", {})


def _sans_accent_hp(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', str(s))
                if unicodedata.category(c) != 'Mn').lower().strip()
    for ch in ("-", "'", "’", "_"):
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _refus_hors_perimetre(question):
    """Inspecte la question AVANT le routage. Renvoie (message, categorie) si
    hors périmètre, sinon (None, None). Empêche tout calcul sur une demande AMC,
    esthétique, médicale ou individuelle."""
    q = _sans_accent_hp(question)
    rs = _DICO_HP.get("reponses_specialisees", {})

    for mot in _DICO_HP.get("mots_amc", []):
        if _sans_accent_hp(mot) in q:
            return (rs.get("amc_mutuelle") or _DICO_HP.get("reponse_type"), "amc")

    for mot in _DICO_HP.get("mots_exclus", []):
        if _sans_accent_hp(mot) in q:
            return (_DICO_HP.get("reponse_type"), "esthetique")

    mots_medicaux = ["posologie", "effet secondaire", "effets secondaires",
                     "quel medicament pour", "comment soigner", "comment guerir",
                     "symptomes de", "traitement pour", "quelle indication"]
    if any(m in q for m in mots_medicaux):
        return (rs.get("medical") or _DICO_HP.get("reponse_type"), "medical")

    for mot in ["individuel", "nominatif", "nom du patient", "identite"]:
        if mot in q:
            return (rs.get("individuel") or _DICO_HP.get("reponse_type"), "individuel")

    return (None, None)


# ---------------------------------------------------------------------------
# ROUTAGE META : questions sur l'assistant lui-meme (perimetre, source, annees,
# capacites). On repond au lieu de refuser. Les faits (annees, sources) sont lus
# dans le dictionnaire : la reponse reste juste si le perimetre evolue.
# ---------------------------------------------------------------------------
_META_ANNEES = ["quelles annees", "quelle annee", "quelle periode", "depuis quand",
                "jusqu a quand", "jusqu quand", "annees couvertes", "periode couverte",
                "quelle plage", "quelles periodes"]
_META_SOURCE = ["d ou viennent", "d ou vient", "d ou proviennent", "quelle est ta source",
                "quelle est la source", "quelles sources", "tes sources", "quelle base",
                "sur quelles donnees", "provenance", "origine des donnees",
                "quelles donnees utilises"]
_META_CAP = ["que sais tu faire", "que peux tu faire", "que fais tu", "a quoi sers tu",
             "a quoi tu sers", "quelles questions", "quel type de question",
             "types de questions", "sur quoi peux tu", "sur quoi tu peux",
             "comment ca marche", "tes capacites", "quelles sont tes capacites",
             "que sais tu", "quel est ton perimetre", "quel perimetre", "ton perimetre",
             "que couvres tu", "que couvre tu", "couvres tu", "couvre tu",
             "est ce que tu gere", "est ce que tu geres", "geres tu", "gere tu",
             "prends tu en compte", "prend tu en compte", "inclus tu",
             "est ce que tu inclus", "traites tu", "traite tu", "gerez vous",
             "savez vous faire"]


def _intention_meta(question):
    """Renvoie 'annees' | 'source' | 'capacites' si la question porte sur l'assistant
    lui-même, sinon None. L'ordre privilégie les intentions les plus spécifiques."""
    q = _sans_accent_hp(question)
    if any(s in q for s in _META_ANNEES):
        return "annees"
    if any(s in q for s in _META_SOURCE):
        return "source"
    if any(s in q for s in _META_CAP):
        return "capacites"
    return None


def _reponse_meta(intention):
    """Réponse explicative construite à partir du dictionnaire (périmètre / sources)."""
    annees = (_DICO_PERIM.get("assistant_v1") or {}).get("annees", "2022-2025")
    sources = _DICO_PERIM.get("sources") or ["Open DAMIR (CNAM)"]

    if intention == "annees":
        return (f"Je couvre les données Open DAMIR de {annees} (part Assurance Maladie "
                f"obligatoire, tous régimes). Les autres périodes ne sont pas chargées "
                f"pour cette démonstration.")
    if intention == "source":
        return ("Mes données proviennent de : " + ", ".join(sources) + ". Ce sont des "
                "données ouvertes, agrégées et anonymes de l'Assurance Maladie "
                "obligatoire (aucune donnée individuelle).")
    # capacites / perimetre
    return (f"Je réponds sur les remboursements de l'Assurance Maladie obligatoire (AMO) "
            f"à partir d'Open DAMIR, pour les années {annees}. Je peux donner un montant "
            f"remboursé par poste (optique, dentaire, hospitalisation…), le ventiler par "
            f"région, sexe ou âge, suivre son évolution dans le temps, comparer deux années, "
            f"ou classer les postes les plus remboursés. Je ne traite pas la part des "
            f"complémentaires (mutuelles / AMC), les questions médicales, ni les données "
            f"individuelles.")


# ---------------------------------------------------------------------------
# ROUTAGE DEFINITION : « que veut dire X », « c'est quoi X », « definis X »...
# -> get_dictionnaire, meme si X est un mot AMC. Une vraie question de donnees
# (contenant une annee, ou longue) N'EST PAS traitee comme une definition.
# ---------------------------------------------------------------------------
_LEAD_DEF = ["que veut dire", "que signifie", "qu est ce que", "qu est ce qu",
             "c est quoi", "definition de", "definition d", "definis moi", "definis",
             "explique moi", "explique le", "explique la", "explique l", "explique",
             "signification de", "peux tu definir", "que represente"]


def _terme_a_definir(question):
    """Si la question est une demande de définition, renvoie le terme à définir,
    sinon None. Écarte les formulations qui ressemblent à une question de données
    (présence d'une année, ou plus de 5 mots après la tournure)."""
    q = _sans_accent_hp(question)
    matched = False
    for lead in sorted(_LEAD_DEF, key=len, reverse=True):
        if q.startswith(lead):
            q = q[len(lead):].strip()
            matched = True
            break
    if not matched:
        return None
    for art in ["le ", "la ", "les ", "l ", "un ", "une ", "d ", "de ", "du ", "des "]:
        if q.startswith(art):
            q = q[len(art):].strip()
            break
    q = q.strip(" ?.!")
    if re.search(r"\b(19|20)\d\d\b", q) or len(q.split()) > 5:
        return None  # ressemble à une vraie question de données, pas à une définition
    return q or None


INSTRUCTION_JSON = """\
Reponds UNIQUEMENT par un objet JSON, sans aucun texte autour, sans balise de code.
Format EXACT :
{"outil": "<nom_outil>", "parametres": {<cles:valeurs>}}

Regles :
- Choisis UN SEUL outil, et uniquement parmi la liste ci-dessus. N'invente jamais de nom d'outil.
- Parametres TOUJOURS en francais ('dentaire' pas 'dental').
- Ne remplis QUE les parametres utiles. Laisse de cote ceux dont tu n'as pas besoin (ne mets pas de chaine vide).
- VERRES, MONTURES et LENTILLES ne sont PAS des postes : mets-les dans 'decoupage', jamais dans 'poste' ni 'sous_categorie'.
- 'mesure' vaut par defaut 'montant_rembourse' (le remboursement). Ne mets un mot dans 'mesure' que s'il designe vraiment une mesure.
- N'invente jamais de chiffre.

Exemples :
Question : "Quel est le remboursement de l'optique en 2023 ?"
{"outil": "query_depenses", "parametres": {"poste": "optique", "annee": 2023}}

Question : "Quel est le remboursement des montures ?"
{"outil": "query_depenses", "parametres": {"decoupage": "montures"}}

Question : "Evolution du remboursement des verres ?"
{"outil": "evolution_serie", "parametres": {"decoupage": "verres"}}

Question : "Depense engagee pour les montures en 2024 ?"
{"outil": "query_depenses", "parametres": {"mesure": "depense_engagee", "decoupage": "montures", "annee": 2024}}

Question : "Top 5 des postes en 2023 ?"
{"outil": "top_postes", "parametres": {"annee": 2023}}

Question : "Que veut dire AMC ?"
{"outil": "get_dictionnaire", "parametres": {"terme": "AMC"}}

Question : "Remboursement des protheses dentaires en 2023 ?"
{"outil": "query_depenses", "parametres": {"decoupage": "protheses dentaires", "annee": 2023}}

Question : "Evolution des remboursements pour tous les postes ?"
{"outil": "evolution_serie", "parametres": {}}

Question : "Compare les depenses dentaires entre 2022 et 2024"
{"outil": "compare_periods", "parametres": {"poste": "dentaire", "annee1": 2022, "annee2": 2024}}
"""


def _decrire_outils(tools):
    """Construit la description injectee au prompt A PARTIR des outils MCP decouverts."""
    lignes = ["Tu disposes des outils suivants pour interroger les donnees Open DAMIR "
              "(depenses Assurance Maladie obligatoire, 2022-2025). "
              "Choisis OBLIGATOIREMENT un outil dans cette liste :\n"]
    for t in tools:
        props = (t.inputSchema or {}).get("properties", {})
        params = ", ".join(props.keys()) if props else "(aucun)"
        desc = (t.description or "").strip().replace("\n", " ")
        lignes.append(f"- {t.name}({params}) : {desc}")
    noms = ", ".join(t.name for t in tools)
    lignes.append(f"\nNoms d'outils valides (aucun autre autorise) : {noms}.")
    return "\n".join(lignes)


def _construire_prompt(question, description_outils):
    return (f"{description_outils}\n\n{INSTRUCTION_JSON}\n\n"
            f'Question : "{question}"\n')


def _extraire_json(texte):
    """Parseur robuste : recupere le 1er objet JSON meme noye dans du texte."""
    if not texte:
        return None
    try:
        return json.loads(texte.strip())
    except json.JSONDecodeError:
        pass
    nettoye = re.sub(r"```(?:json)?", "", texte).strip()
    try:
        return json.loads(nettoye)
    except json.JSONDecodeError:
        pass
    debut = nettoye.find("{")
    if debut == -1:
        return None
    profondeur = 0
    for i in range(debut, len(nettoye)):
        if nettoye[i] == "{":
            profondeur += 1
        elif nettoye[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                try:
                    return json.loads(nettoye[debut:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def poser_question(question):
    """Point d'entree unique. Renvoie {reponse, outil, parametres}.
    Format inchange : l'app Streamlit n'a rien a adapter cote affichage."""

    # 1) META : questions sur le perimetre / les sources / les annees / les capacites.
    #    Traitees AVANT le garde-fou pour repondre au lieu de refuser sechement.
    intention = _intention_meta(question)
    if intention:
        return {"reponse": _reponse_meta(intention), "outil": "reponse_meta",
                "parametres": None, "meta": True}

    # 2) DEFINITION : « que veut dire X / c'est quoi X » -> get_dictionnaire directement,
    #    meme si X est un terme AMC (definir un terme n'est pas hors perimetre).
    terme = _terme_a_definir(question)
    if terme is not None:
        try:
            client = get_mcp_client()
            resultat = client.call_tool("get_dictionnaire", {"terme": terme})
            return {"reponse": str(resultat), "outil": "get_dictionnaire",
                    "parametres": {"terme": terme}}
        except Exception as e:
            print(f"[DEBUG] Definition MCP get_dictionnaire('{terme}') a echoue : {e}")
            # en cas d'echec, on retombe sur le flux normal

    # 3) GARDE-FOU : refuser en amont une question hors périmètre (AMC data, esthétique, médical…)
    msg_refus, categorie = _refus_hors_perimetre(question)
    if msg_refus:
        return {"reponse": msg_refus, "outil": None, "parametres": None,
                "hors_perimetre": True}

    backend = get_backend()           # cache dans backends.py (charge une fois)
    client = get_mcp_client()         # connexion MCP persistante (lance une fois)

    tools = client.list_tools()
    noms_valides = {t.name for t in tools}
    description = _decrire_outils(tools)

    # 4) le LLM propose (texte) -> JSON
    texte = backend.generer(_construire_prompt(question, description))
    choix = _extraire_json(texte)

    if not choix or "outil" not in choix:
        return {"reponse": "Je n'ai pas pu interpreter cette question dans mon perimetre "
                           "(Open DAMIR 2022-2025). Pouvez-vous la reformuler ?",
                "outil": None, "parametres": None}

    nom = choix.get("outil")
    params = choix.get("parametres", {}) or {}
    if nom not in noms_valides:
        return {"reponse": f"Aucun outil MCP nomme « {nom} » n'est disponible.",
                "outil": nom, "parametres": params}

    # 5) appel via le PROTOCOLE MCP (et non un import Python direct)
    try:
        resultat = client.call_tool(nom, params)
    except Exception as e:
        print(f"[DEBUG] Erreur MCP sur {nom}({params}) : {e}")   # trace pour toi (terminal)
        return {"reponse": ("Je n'ai pas réussi à traiter cette demande avec les "
                            "paramètres proposés. Pouvez-vous reformuler ?"),
                "outil": nom, "parametres": params}

    return {"reponse": str(resultat), "outil": nom, "parametres": params}


if __name__ == "__main__":
    print("=== Assistant DAMIA (client MCP, multi-modele) ===")
    print(f"Backend : {get_backend().__class__.__name__}")
    print("Outils MCP :", [t.name for t in get_mcp_client().list_tools()])
    while True:
        try:
            q = input("\nQuestion > ")
            if not q.strip():
                continue
            r = poser_question(q)
            print(r["reponse"])
            if r["outil"]:
                print(f"   [outil MCP: {r['outil']} | params: {r['parametres']}]")
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir.")
            break