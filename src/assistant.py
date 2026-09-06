"""
src/assistant.py — Orchestrateur multi-modele, client MCP.

Les outils ne sont PAS importes en Python : ils sont decouverts DYNAMIQUEMENT
via le protocole MCP (src/mcp_client.py), et la description injectee au prompt
est construite a partir de cette decouverte. Les appels passent donc reellement
par MCP — c'est la preuve d'interoperabilite : si demain Claude Desktop parle au
meme serveur, il voit exactement les memes outils.

Le modele PROPOSE un outil en JSON ; le code l'APPELLE. Le modele ne produit
jamais un chiffre.

ROUTAGE EN AMONT (avant le modele), dans poser_question() :
  1. META       : questions sur le perimetre / les sources / les annees / les
                  capacites -> reponse explicative (et non un refus sec).
  2. DEFINITION : « que veut dire X / c'est quoi X » -> get_dictionnaire,
                  meme si X est un terme AMC (definir n'est pas hors perimetre ;
                  seul CALCULER des montants AMC l'est).
  3. GARDE-FOU  : refus des demandes hors perimetre (AMC, esthetique, medical,
                  individuel).
  4. MODELE     : routage LLM vers un outil MCP.

Usage :
    from src.assistant import poser_question
    res = poser_question("Taux de couverture de l'optique en 2023 ?")
"""
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import CHEMIN_DICO
from src.backends import get_backend
from src.formatage import extraire_table
from src.mcp_client import SEPARATEUR as SEPARATEUR_SOURCE, get_registre
from src.normalisation import sans_accent

log = logging.getLogger(__name__)

# Dictionnaire charge une fois : on garde les sous-sections utiles au routage amont.
_DICO = json.loads(Path(CHEMIN_DICO).read_text(encoding="utf-8"))
_DICO_HP = _DICO.get("hors_perimetre", {})
_DICO_PERIM = _DICO.get("perimetre", {})

# Formulations normalisees une seule fois au chargement (et non a chaque question).
_MOTS_AMC = [sans_accent(m) for m in _DICO_HP.get("mots_amc", [])]
_MOTS_EXCLUS = [sans_accent(m) for m in _DICO_HP.get("mots_exclus", [])]
_MOTS_MEDICAUX = ["posologie", "effet secondaire", "effets secondaires",
                  "quel medicament pour", "comment soigner", "comment guerir",
                  "symptomes de", "traitement pour", "quelle indication"]
_MOTS_INDIVIDUEL = ["individuel", "nominatif", "nom du patient", "identite"]


def _refus_hors_perimetre(question):
    """Inspecte la question AVANT le routage. Renvoie (message, categorie) si
    hors périmètre, sinon (None, None). Empêche tout calcul sur une demande AMC,
    esthétique, médicale ou individuelle."""
    q = sans_accent(question)
    rs = _DICO_HP.get("reponses_specialisees", {})
    defaut = _DICO_HP.get("reponse_type")

    for mots, cle, categorie in ((_MOTS_AMC, "amc_mutuelle", "amc"),
                                 (_MOTS_EXCLUS, None, "esthetique"),
                                 (_MOTS_MEDICAUX, "medical", "medical"),
                                 (_MOTS_INDIVIDUEL, "individuel", "individuel")):
        if any(m in q for m in mots):
            return ((rs.get(cle) if cle else None) or defaut, categorie)
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
_META_CAPACITES = ["que sais tu faire", "que peux tu faire", "que fais tu", "a quoi sers tu",
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
    q = sans_accent(question)
    for formulations, intention in ((_META_ANNEES, "annees"),
                                    (_META_SOURCE, "source"),
                                    (_META_CAPACITES, "capacites")):
        if any(s in q for s in formulations):
            return intention
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
_TOURNURES_DEFINITION = sorted(
    ["que veut dire", "que signifie", "qu est ce que", "qu est ce qu",
     "c est quoi", "definition de", "definition d", "definis moi", "definis",
     "explique moi", "explique le", "explique la", "explique l", "explique",
     "signification de", "peux tu definir", "que represente"],
    key=len, reverse=True)          # la plus longue d'abord : « definis moi » avant « definis »

_ARTICLES = ("le ", "la ", "les ", "l ", "un ", "une ", "d ", "de ", "du ", "des ")
_MAX_MOTS_DEFINITION = 5


def _terme_a_definir(question):
    """Si la question est une demande de définition, renvoie le terme à définir,
    sinon None. Écarte les formulations qui ressemblent à une question de données
    (présence d'une année, ou plus de 5 mots après la tournure)."""
    q = sans_accent(question)
    tournure = next((t for t in _TOURNURES_DEFINITION if q.startswith(t)), None)
    if not tournure:
        return None

    q = q[len(tournure):].strip()
    for art in _ARTICLES:
        if q.startswith(art):
            q = q[len(art):].strip()
            break
    q = q.strip(" ?.!")

    if re.search(r"\b(19|20)\d\d\b", q) or len(q.split()) > _MAX_MOTS_DEFINITION:
        return None                 # ressemble a une question de donnees, pas a une definition
    return q or None


INSTRUCTION_JSON = """\
Reponds UNIQUEMENT par un objet JSON, sans aucun texte autour, sans balise de code.
Format EXACT :
{"outil": "<nom_outil>", "parametres": {<cles:valeurs>}}

Regles :
- Choisis UN SEUL outil, et uniquement parmi la liste ci-dessus. N'invente jamais de nom d'outil.
- Recopie le nom de l'outil EXACTEMENT tel qu'il apparait dans la liste, prefixe de sa source compris (ex. "damir.query_depenses").
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
    """Construit la description injectee au prompt A PARTIR des outils MCP decouverts.

    Les noms sont QUALIFIES (`source.outil`). Avec une seule source cela ne change
    rien pour le modele ; avec plusieurs, c'est ce qui rend le routage decidable."""
    lignes = ["Tu disposes des outils suivants pour interroger les donnees Open DAMIR "
              "(depenses Assurance Maladie obligatoire, 2022-2025). "
              "Choisis OBLIGATOIREMENT un outil dans cette liste :\n"]
    for t in tools:
        props = (t.inputSchema or {}).get("properties", {})
        params = ", ".join(props.keys()) if props else "(aucun)"
        desc = (t.description or "").strip().replace("\n", " ")
        lignes.append(f"- {t.name}({params}) : {desc}")
    lignes.append("\nNoms d'outils valides (aucun autre autorise) : "
                  + ", ".join(t.name for t in tools) + ".")
    return "\n".join(lignes)


def _construire_prompt(question, description_outils):
    return (f"{description_outils}\n\n{INSTRUCTION_JSON}\n\n"
            f'Question : "{question}"\n')


def _extraire_json(texte):
    """Parseur robuste : recupere le 1er objet JSON meme noye dans du texte
    ou entoure d'une balise de code."""
    if not texte:
        return None

    nettoye = re.sub(r"```(?:json)?", "", texte).strip()
    for candidat in (texte.strip(), nettoye):
        try:
            return json.loads(candidat)
        except json.JSONDecodeError:
            pass

    # Dernier recours : premier objet equilibre { ... } trouve dans le texte.
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


def _resultat(reponse, outil=None, parametres=None, **extra):
    """Format de retour unique, consomme tel quel par app.py et api.py."""
    return {"reponse": reponse, "outil": outil, "parametres": parametres, **extra}


def poser_question(question):
    """Point d'entree unique. Renvoie {reponse, outil, parametres}."""

    # 1) META : perimetre / sources / annees / capacites.
    #    Traite AVANT le garde-fou pour repondre au lieu de refuser sechement.
    intention = _intention_meta(question)
    if intention:
        return _resultat(_reponse_meta(intention), outil="reponse_meta", meta=True)

    registre = get_registre()         # connexions MCP persistantes (une par source)

    # 2) DEFINITION : definir un terme n'est jamais hors perimetre, meme un terme AMC.
    terme = _terme_a_definir(question)
    if terme is not None:
        outil_glossaire = registre.trouver("get_dictionnaire")
        if outil_glossaire:
            try:
                resultat = registre.call_tool(outil_glossaire, {"terme": terme})
                return _resultat(str(resultat), outil_glossaire, {"terme": terme})
            except Exception:
                # Le glossaire est un raccourci, pas un passage oblige : en cas
                # d'echec MCP on laisse la question suivre le flux normal.
                log.warning("Definition MCP %s(%r) a echoue", outil_glossaire, terme,
                            exc_info=True)

    # 3) GARDE-FOU : refus en amont (AMC, esthetique, medical, individuel).
    msg_refus, _categorie = _refus_hors_perimetre(question)
    if msg_refus:
        return _resultat(msg_refus, hors_perimetre=True)

    backend = get_backend()           # cache dans backends.py (charge une fois)
    tools = registre.list_tools()

    # 4) le LLM propose (texte) -> JSON
    texte = backend.generer(_construire_prompt(question, _decrire_outils(tools)))
    choix = _extraire_json(texte)

    if not choix or "outil" not in choix:
        # On journalise la sortie BRUTE : c'est la seule facon de distinguer un
        # modele qui a mal compris (JSON valide, mauvais outil) d'un modele coupe
        # net par max_tokens (JSON tronque). Les deux se presentent pareil ici.
        log.warning("Reponse du modele non interpretable (%d caracteres) : %r",
                    len(texte or ""), (texte or "")[:300])
        return _resultat("Je n'ai pas pu interpreter cette question dans mon perimetre "
                         "(Open DAMIR 2022-2025). Pouvez-vous la reformuler ?")

    nom = choix.get("outil")
    params = choix.get("parametres") or {}

    # Le modele peut renvoyer un nom court ou qualifie ; le registre tranche, et
    # refuse un nom court ambigu entre plusieurs sources plutot que de deviner.
    try:
        source, court = registre.resoudre(nom)
        nom_qualifie = f"{source}{SEPARATEUR_SOURCE}{court}"
    except KeyError as e:
        log.info("Outil propose non resolu : %s", e)
        return _resultat(f"Aucun outil MCP nomme « {nom} » n'est disponible.", nom, params)

    # 5) appel via le PROTOCOLE MCP (et non un import Python direct)
    try:
        resultat = registre.call_tool(nom_qualifie, params)
    except Exception:
        log.error("Erreur MCP sur %s(%s)", nom_qualifie, params, exc_info=True)
        return _resultat("Je n'ai pas réussi à traiter cette demande avec les "
                         "paramètres proposés. Pouvez-vous reformuler ?",
                         nom_qualifie, params)

    return _resultat(str(resultat), nom_qualifie, params, source=source)


def _afficher_table_terminal(table, largeur_max=60):
    """Rend le tableau structuré en colonnes alignées, lisibles en console."""
    lignes = table.get("lignes") or []
    if not lignes:
        return
    from src.formatage import format_valeur
    unite = table.get("unite") or "€"
    colonnes = table.get("colonnes") or ["", ""]

    valeurs = [format_valeur(l[1], unite) for l in lignes]
    # Chaque colonne se cale sur le plus long de son en-tete et de son contenu,
    # sinon le titre deborde du filet de separation.
    largeur_cle = min(largeur_max, max([len(str(l[0])) for l in lignes]
                                       + [len(str(colonnes[0]))]))
    largeur_val = max([len(v) for v in valeurs] + [len(str(colonnes[1]))])

    print(f"  {colonnes[0]:<{largeur_cle}}  {colonnes[1]:>{largeur_val}}")
    print(f"  {'-' * largeur_cle}  {'-' * largeur_val}")
    for (cle, _), valeur in zip(lignes, valeurs):
        print(f"  {str(cle)[:largeur_cle]:<{largeur_cle}}  {valeur:>{largeur_val}}")


def _boucle_terminal():
    """Chat en ligne de commande. Le client texte le plus simple du projet :
    aucune interface, juste l'orchestrateur et le protocole MCP."""
    # WARNING par defaut : en INFO, les traces httpx et mcp se melent aux
    # reponses et rendent la conversation illisible. DAMIA_LOG_LEVEL=INFO pour
    # les reafficher lors d'un diagnostic.
    logging.basicConfig(level=os.environ.get("DAMIA_LOG_LEVEL", "WARNING"),
                        format="%(levelname)s %(name)s: %(message)s")

    print("=== Assistant DAMIA (client MCP, multi-modele) ===")
    print(f"Backend : {get_backend().__class__.__name__} "
          f"({os.environ.get('DAMIA_MODELE', 'modele par defaut')})")
    registre = get_registre()
    print(f"Sources MCP : {', '.join(registre.clients)} "
          f"— {len(registre.list_tools())} outils")
    print("Ctrl+C pour quitter.")

    while True:
        try:
            question = input("\nQuestion > ")
            if not question.strip():
                continue
            resultat = poser_question(question)

            # On retire le marqueur de tableau : il est destine aux interfaces
            # graphiques et n'a rien a faire sous les yeux d'un utilisateur.
            texte, table = extraire_table(resultat["reponse"], couper_enumeration=True)
            print(texte)
            if table:
                _afficher_table_terminal(table)
            if resultat["outil"]:
                print(f"   [outil MCP : {resultat['outil']} "
                      f"| params : {resultat['parametres']}]")
        except (KeyboardInterrupt, EOFError):
            print("\nAu revoir.")
            break


if __name__ == "__main__":
    _boucle_terminal()
