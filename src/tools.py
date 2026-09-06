"""
Logique metier des outils MCP : requetes DuckDB + formatage.
Separe du serveur pour etre testable independamment.

PRINCIPES :
  - Routage des postes DATA-DRIVEN depuis le dictionnaire (champ `synonymes` +
    `filtre`) : le dictionnaire est la SOURCE UNIQUE de verite. Modifier le JSON
    modifie reellement le comportement, sans toucher au code.
  - Refus hors-perimetre et message AMC tires du dictionnaire (reponses_specialisees).
  - FAIL-CLOSED : un parametre fourni mais non reconnu provoque un refus explicite,
    jamais un chiffre calcule en ignorant le filtre.
  - Connexion DuckDB persistante (read-only) reutilisee -> plus rapide qu'une
    ouverture/fermeture a chaque requete.

Le formatage des valeurs et le transport du tableau structure vivent dans
src/formatage.py ; la normalisation de texte dans src/normalisation.py.
"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

from config.config import CHEMIN_DB, CHEMIN_DICO, MESURES
from src.formatage import bloc_table, montant
from src.normalisation import (match_mot_entier, meilleur_match, sans_accent,
                               specificite)

# --- Dictionnaire charge une fois ---
DICO = json.loads(Path(CHEMIN_DICO).read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# Connexion DuckDB
# ---------------------------------------------------------------------------
# Ouverte paresseusement et reutilisee. Le verrou protege l'initialisation :
# le serveur MCP sert plusieurs requetes en parallele, sans lui deux threads
# pourraient ouvrir deux connexions concurrentes sur le meme fichier.
_CON = None
_VERROU_CON = threading.Lock()


def _connexion():
    global _CON
    if _CON is None:
        with _VERROU_CON:
            if _CON is None:
                if not Path(CHEMIN_DB).exists():
                    raise FileNotFoundError(
                        f"Base DuckDB introuvable : {CHEMIN_DB}. "
                        f"Genere-la avec `python src/data_load.py`.")
                _CON = duckdb.connect(str(CHEMIN_DB), read_only=True)
    return _CON


def _executer(sql, params):
    """Execute une requete et renvoie les lignes. Centralise pour n'avoir qu'un
    seul endroit ou brancher un log ou un cache."""
    return _connexion().execute(sql, params).fetchall()


def _executer_un(sql, params):
    """Execute une requete d'agregat et renvoie la valeur unique (0 si NULL)."""
    res = _connexion().execute(sql, params).fetchone()
    return res[0] if res and res[0] is not None else 0


# Jointure commune a toutes les requetes : les faits portent prs_nat, la
# dimension `prestations` porte la classification en postes de soin.
_FROM_JOIN = ('FROM faits f JOIN prestations p '
              'ON CAST(f.prs_nat AS VARCHAR) = CAST(p.prs_nat AS VARCHAR)')


def _clause_where(conditions):
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


# ---------------------------------------------------------------------------
# Traduction dimensions <-> codes base
# ---------------------------------------------------------------------------
def _modalites(dimension):
    """Renvoie le dict des modalites d'une dimension : {code: {libelle, synonymes}}."""
    return DICO.get("dimensions", {}).get(dimension, {}).get("modalites", {}) or {}


def _vers_code(dimension, valeur):
    """ENTREE : traduit ce que dit l'utilisateur en code base.
    'homme' -> '1', 'ile de france' -> '11', '11' -> '11'.
    Renvoie None si non reconnu (le validateur refusera)."""
    if valeur is None:
        return None
    v = sans_accent(valeur)
    mods = _modalites(dimension)
    if str(valeur).strip() in mods:
        return str(valeur).strip()
    for code, conf in mods.items():
        if sans_accent(conf.get("libelle", "")) == v:
            return code
    for code, conf in mods.items():
        if any(sans_accent(s) == v for s in conf.get("synonymes", [])):
            return code
    for code, conf in mods.items():
        if any(sans_accent(s) and sans_accent(s) in v for s in conf.get("synonymes", [])):
            return code
    return None


def _vers_libelle(dimension, code):
    """SORTIE : traduit un code base en libelle lisible. '11' -> 'Île-de-France'."""
    conf = _modalites(dimension).get(str(code))
    return conf.get("libelle", str(code)) if conf else str(code)


# ---------------------------------------------------------------------------
# Routage des postes construit A PARTIR DU DICTIONNAIRE (source unique)
# ---------------------------------------------------------------------------
def _construire_routes():
    """Construit la table de routage poste -> (colonne, valeur) depuis le dico.
    Chaque poste fournit ses `synonymes` et son `filtre` {colonne, valeur}.
    La cle du poste elle-meme sert aussi de synonyme."""
    routes = []
    for nom, conf in DICO.get("postes", {}).items():
        if nom.startswith("_") or not isinstance(conf, dict) or "filtre" not in conf:
            continue
        cles = [nom] + list(conf.get("synonymes", []))
        cles_norm = [c for c in (sans_accent(x) for x in cles if x) if c]
        routes.append((cles_norm, conf["filtre"]["colonne"], conf["filtre"]["valeur"]))
    return routes


_ROUTES = _construire_routes()
_MOTS_EXCLUS = [sans_accent(m) for m in DICO.get("hors_perimetre", {}).get("mots_exclus", [])]
_MOTS_AMC = [sans_accent(m) for m in DICO.get("hors_perimetre", {}).get("mots_amc", [])]


def _match_poste(poste):
    """Cherche le poste le plus specifique correspondant au terme.
    Renvoie (colonne, valeur, cle_matchee) ou (None, None, None)."""
    p = sans_accent(poste)
    meilleure = None
    for cles_norm, col, val in _ROUTES:
        cle = meilleur_match(cles_norm, p)
        if cle and (meilleure is None or specificite(cle) > specificite(meilleure[2])):
            meilleure = (col, val, cle)
    return meilleure or (None, None, None)


def _clause_poste(poste):
    """Renvoie (fragment_sql, params, reconnu).
    reconnu=False -> poste hors perimetre : il faut REFUSER (anti-hallucination)."""
    p = sans_accent(poste)
    # 1) exclusions explicites (esthetique, confort, AMC...) -> refus
    if any(m in p for m in _MOTS_EXCLUS) or any(m in p for m in _MOTS_AMC):
        return (None, [], False)
    # 2) routage depuis le dico, par mot entier (voir src/normalisation.py)
    col, val, _cle = _match_poste(poste)
    if col:
        return (f"p.{col} = ?", [val], True)
    # 3) inconnu -> on ne devine pas
    return (None, [], False)


# ---------------------------------------------------------------------------
# Enrichissement : note contextuelle du dictionnaire
# ---------------------------------------------------------------------------
def _poste_dico(poste):
    """Retrouve la cle de poste du dictionnaire correspondant a l'entree utilisateur."""
    if not poste:
        return None
    p = sans_accent(poste)
    meilleur_nom, meilleure_cle = None, None
    for nom, conf in DICO.get("postes", {}).items():
        if nom.startswith("_") or not isinstance(conf, dict):
            continue
        cle = meilleur_match([nom] + list(conf.get("synonymes", [])), p)
        if cle and (meilleure_cle is None or specificite(cle) > specificite(meilleure_cle)):
            meilleur_nom, meilleure_cle = nom, cle
    return meilleur_nom


def _note_poste(poste):
    """Renvoie la note contextuelle du dico pour ce poste (ou '')."""
    nom = _poste_dico(poste)
    if not nom:
        return ""
    conf = DICO.get("postes", {}).get(nom, {})
    note = (conf.get("note") or "").strip()
    cov = (conf.get("couverture_amo_typique") or "").strip()
    bouts = []
    if cov and cov != "—":
        bouts.append(f"Couverture AMO typique : {cov}.")
    if note:
        bouts.append(note)
    return " ".join(bouts)


def _contexte_final(poste):
    note = _note_poste(poste)
    return f"\n\nÀ noter : {note}" if note else ""


def _message_refus(poste):
    """Message de refus propre pour un poste hors perimetre."""
    p = sans_accent(poste) if poste else ""
    rs = DICO.get("hors_perimetre", {}).get("reponses_specialisees", {})
    if any(m in p for m in _MOTS_AMC) and "amc_mutuelle" in rs:
        return rs["amc_mutuelle"]
    return (f"Le poste « {poste} » ne fait pas partie du périmètre Open DAMIR "
            f"(soins remboursés par l'Assurance Maladie, 2022-2025). "
            f"Aucune donnée disponible — je ne peux pas avancer de chiffre. "
            f"Je peux en revanche vous renseigner un poste réellement couvert "
            f"(pharmacie, hospitalisation, optique, dentaire, audioprothèse, imagerie).")


# ---------------------------------------------------------------------------
# Granularite : sous-categorie (niveau 2) et decoupage fin prs_nat (niveau 3)
# ---------------------------------------------------------------------------
def _sous_categorie_valide(sous_cat):
    if not sous_cat:
        return None
    s = sans_accent(sous_cat)
    for domaine, liste in DICO.get("catalogue_sous_categories", {}).items():
        if domaine.startswith("_") or not isinstance(liste, list):
            continue
        for val in liste:
            if sans_accent(val) == s or s in sans_accent(val):
                return val
    return None


def _decoupage_match_detail(terme):
    """Comme _decoupage_match, mais renvoie aussi la cle qui a matche, afin de
    pouvoir arbitrer par specificite face a un poste concurrent.
    Renvoie (groupe, cible, type_cible, cle_matchee)."""
    if not terme:
        return (None, None, None, None)
    t = sans_accent(terme)
    meilleur = (None, None, None, None)
    for bloc_nom, bloc in DICO.get("decoupages_fins", {}).items():
        if bloc_nom.startswith("_") or not isinstance(bloc, dict):
            continue
        for groupe, gconf in bloc.get("groupes", {}).items():
            cle = meilleur_match([groupe] + list(gconf.get("synonymes", [])), t)
            if not cle or specificite(cle) <= specificite(meilleur[3]):
                continue
            if gconf.get("codes"):
                meilleur = (groupe, gconf["codes"], "codes", cle)
            elif gconf.get("sous_categories"):
                meilleur = (groupe, gconf["sous_categories"], "sous_categories", cle)
    return meilleur


def _decoupage_match(terme):
    """Cherche un decoupage fin correspondant au terme, dans DICO['decoupages_fins'].
    Renvoie (nom_groupe, cible, type_cible) ou (None, None, None).
    type_cible = 'codes' (prs_nat) ou 'sous_categories'."""
    return _decoupage_match_detail(terme)[:3]


def _clause_decoupage(cible, type_cible="codes"):
    """Filtre niveau 3 : liste de codes prs_nat OU liste de sous-categories."""
    if not cible:
        return (None, [])
    ph = ",".join("?" for _ in cible)
    if type_cible == "sous_categories":
        return (f"p.sous_categorie_cor IN ({ph})", [str(c) for c in cible])
    return (f"CAST(p.prs_nat AS VARCHAR) IN ({ph})", [str(c) for c in cible])


def _resoudre_cible(poste=None, sous_categorie=None, decoupage=None):
    """Resout la cible au niveau le plus fin demande (decoupage > sous_cat > poste).

    Renvoie (fragment_sql, params, libelle_cible, refus)."""
    # Le LLM place parfois un decoupage fin ('montures') dans `poste` ou
    # `sous_categorie` : on le repere et on le promeut au bon niveau.
    #
    # La promotion est ARBITREE par specificite : un terme peut matcher a la fois
    # un decoupage et un poste (« prothèses auditives » matche la cle generique
    # « prothese » du decoupage dentaire ET le poste audioprothese). On garde le
    # match le plus specifique — sans quoi une question sur l'audioprothese
    # repondrait avec les chiffres du dentaire. A specificite egale, le decoupage
    # l'emporte : c'est le niveau le plus fin, donc le plus informatif.
    if not decoupage:
        for cand in (sous_categorie, poste):
            if not cand:
                continue
            _, cible_d, _, cle_d = _decoupage_match_detail(cand)
            if not cible_d:
                continue
            _, _, cle_p = _match_poste(cand)
            if cle_p and specificite(cle_p) > specificite(cle_d):
                continue          # le poste est plus specifique : pas de promotion
            decoupage = cand
            if sous_categorie == cand:
                sous_categorie = None
            if poste == cand:
                poste = None
            break

    if decoupage:
        groupe, cible, typ = _decoupage_match(decoupage)
        if groupe and cible:
            frag, params = _clause_decoupage(cible, typ)
            return (frag, params, groupe.lower(), False)
        return (None, [], "", True)

    if sous_categorie:
        cat = _sous_categorie_valide(sous_categorie)
        if cat:
            return ("p.sous_categorie_cor = ?", [cat], f"« {cat} »", False)

    if poste:
        frag, params, reconnu = _clause_poste(poste)
        if not reconnu:
            return (None, [], "", True)
        return (frag, params, f"poste « {poste} »", False)

    return (None, [], "", False)


# ---------------------------------------------------------------------------
# Normalisation et validation des parametres envoyes par le LLM
# ---------------------------------------------------------------------------
_VALEURS_VIDES = ("", "null", "none", "n/a", "aucun", "-")


def _vide(x):
    """Le LLM envoie souvent '' / 'null' / 'none' au lieu d'omettre un parametre."""
    if isinstance(x, str):
        s = x.strip()
        return None if s.lower() in _VALEURS_VIDES else s
    return x


def _refus_parametre(nom, valeur, possibles=None):
    """Refus quand un parametre est fourni mais NON RECONNU (fail-closed)."""
    msg = (f"Le paramètre « {nom} » vaut « {valeur} », que je ne reconnais pas. "
           f"Je préfère ne pas répondre plutôt que de donner un chiffre qui "
           f"ignorerait ce filtre.")
    if possibles:
        msg += " Valeurs possibles : " + ", ".join(str(p) for p in list(possibles)[:12]) + "."
    return msg


def _decoupages_disponibles():
    noms = []
    for bloc_nom, bloc in DICO.get("decoupages_fins", {}).items():
        if not bloc_nom.startswith("_") and isinstance(bloc, dict):
            noms.extend(bloc.get("groupes", {}).keys())
    return noms


def _sous_categories_disponibles():
    cats = []
    for dom, lst in DICO.get("catalogue_sous_categories", {}).items():
        if not dom.startswith("_") and isinstance(lst, list):
            cats.extend(lst)
    return cats


def _valider_parametres(poste=None, sous_categorie=None, decoupage=None,
                        region=None, age=None, sexe=None):
    """FAIL-CLOSED : un parametre fourni mais non reconnu -> message de refus.
    Renvoie None si tout est valide.

    NB : `poste` n'est pas valide ici mais dans _resoudre_cible, qui distingue
    un poste inconnu (refus perimetre) d'un decoupage mal place (rattrapable)."""
    if decoupage and not _decoupage_match(decoupage)[0]:
        return _refus_parametre("decoupage", decoupage, _decoupages_disponibles())

    if (sous_categorie and not _sous_categorie_valide(sous_categorie)
            and not _decoupage_match(sous_categorie)[0]):
        return _refus_parametre("sous_categorie", sous_categorie,
                                _sous_categories_disponibles())

    for nom, val in (("region", region), ("age", age), ("sexe", sexe)):
        if val is not None and _vers_code(nom, val) is None:
            libelles = [c.get("libelle") for c in _modalites(nom).values()]
            return _refus_parametre(nom, val, libelles)
    return None


def _traduire_dimensions(region=None, age=None, sexe=None):
    """Traduit les 3 dimensions en codes base. A appeler APRES validation."""
    return (_vers_code("region", region) if region is not None else None,
            _vers_code("age", age) if age is not None else None,
            _vers_code("sexe", sexe) if sexe is not None else None)


def _annees_couvertes():
    p = DICO.get("perimetre", {}).get("assistant_v1", {}).get("annees", "2022-2025")
    try:
        debut, fin = str(p).split("-")
        return int(debut), int(fin)
    except (ValueError, AttributeError):
        return (2022, 2025)


def _valider_annee(annee):
    """Refuse une annee hors perimetre (plutot que de renvoyer 0 en silence)."""
    if annee is None:
        return None
    debut, fin = _annees_couvertes()
    if not (debut <= int(annee) <= fin):
        return (f"L'année {annee} est hors du périmètre couvert "
                f"({debut}-{fin}). Aucune donnée disponible — "
                f"je ne peux pas avancer de chiffre.")
    return None


_SYNONYMES_MESURE = {
    "montant rembourse": "montant_rembourse", "montant_rembourse": "montant_rembourse",
    "rembourse": "montant_rembourse", "remboursement": "montant_rembourse",
    "depense engagee": "depense_engagee", "depense_engagee": "depense_engagee",
    "depense": "depense_engagee", "paiement": "depense_engagee",
    "nombre acte": "nombre_actes", "nombre_acte": "nombre_actes", "acte": "nombre_actes",
    "base remboursement": "base_remboursement", "base_remboursement": "base_remboursement",
    "base": "base_remboursement",
    "depassement": "depassement", "depassement honoraire": "depassement",
}


def _normaliser_mesure(mesure):
    """Tolere les variantes du LLM : 's' final, accents, casse, synonymes.
    Renvoie la mesure canonique, ou 'montant_rembourse' par defaut.

    Le defaut est volontaire : une mesure inconnue ne doit pas bloquer la
    reponse (contrairement a un FILTRE inconnu, qui fausserait le chiffre)."""
    m = sans_accent(mesure).rstrip("s")
    if m in _SYNONYMES_MESURE:
        return _SYNONYMES_MESURE[m]
    for cle in MESURES:
        if sans_accent(cle).rstrip("s") == m:
            return cle
    return "montant_rembourse"


def _normaliser_annee(annee):
    """Tolere tout ce que le LLM peut envoyer : int, '2023', ['2023', ...],
    '2022-2025', '', None. Renvoie un int unique, ou None (= pas de filtre annee)."""
    if annee is None or annee == "":
        return None
    if isinstance(annee, (list, tuple)):
        return None                       # liste d'annees -> tout le perimetre
    s = str(annee).strip()
    if "-" in s or "/" in s:
        return None                       # plage « 2022-2025 » -> tout le perimetre
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _libelle_mesure(mesure):
    return DICO.get("mesures", {}).get(mesure, {}).get("label", mesure)


def _unite(mesure):
    return "actes" if mesure == "nombre_actes" else "€"


# ---------------------------------------------------------------------------
# Outils exposes
# ---------------------------------------------------------------------------
def query_depenses(mesure="montant_rembourse", poste=None, annee=None,
                   region=None, age=None, sexe=None,
                   sous_categorie=None, decoupage=None):
    """Calcule une mesure avec filtres optionnels (decoupage > sous_categorie > poste)."""
    poste, region, age, sexe = _vide(poste), _vide(region), _vide(age), _vide(sexe)
    sous_categorie, decoupage = _vide(sous_categorie), _vide(decoupage)

    err = _valider_parametres(poste, sous_categorie, decoupage, region, age, sexe)
    if err:
        return err
    annee = _normaliser_annee(annee)
    err = _valider_annee(annee)
    if err:
        return err

    region, age, sexe = _traduire_dimensions(region, age, sexe)

    mesure = _normaliser_mesure(mesure)
    col = MESURES[mesure]

    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?")
        params.append(annee)

    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag)
        params += cible_params

    filtres_txt = []
    for valeur, colonne, dim in ((region, "f.ben_res_reg", "region"),
                                 (age, "f.age_ben_snds", "age"),
                                 (sexe, "f.ben_sex_cod", "sexe")):
        if valeur is not None:
            where.append(f"{colonne} = ?")
            params.append(valeur)
            filtres_txt.append(_vers_libelle(dim, valeur))

    val = _executer_un(
        f'SELECT SUM(f."{col}") {_FROM_JOIN} {_clause_where(where)}', params)

    debut, fin = _annees_couvertes()
    cible_phrase = f" pour {cible_txt}" if cible_txt else ""
    periode = f" en {annee}" if annee else f" sur la période {debut}-{fin}"
    filtre_txt = (" — " + ", ".join(filtres_txt)) if filtres_txt else ""
    phrase = (f"{_libelle_mesure(mesure)}{cible_phrase}{periode}{filtre_txt} : "
              f"{montant(val, mesure)}.")
    # La note contextuelle ne vaut que pour un poste entier, pas pour un sous-ensemble.
    ctx = _contexte_final(poste) if poste and not decoupage and not sous_categorie else ""
    return phrase + ctx


def _valeur_brute(mesure_col, poste, annee):
    """Valeur numerique brute, ou None si le poste est hors perimetre."""
    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?")
        params.append(int(annee))
    if poste is not None:
        frag, p_params, reconnu = _clause_poste(poste)
        if not reconnu:
            return None
        where.append(frag)
        params += p_params
    return _executer_un(
        f'SELECT SUM(f."{mesure_col}") {_FROM_JOIN} {_clause_where(where)}', params)


def taux_couverture(poste=None, annee=None):
    """Taux de couverture AMO = montant remboursé / dépense engagée."""
    poste = _vide(poste)
    annee = _normaliser_annee(annee)
    err = _valider_annee(annee)
    if err:
        return err

    num = _valeur_brute(MESURES["montant_rembourse"], poste, annee)
    den = _valeur_brute(MESURES["depense_engagee"], poste, annee)
    if num is None or den is None:
        return _message_refus(poste)
    if not den:
        return "Donnée insuffisante pour calculer un taux."

    poste_txt = f" ({poste})" if poste else ""
    annee_txt = f" en {annee}" if annee else ""
    return f"Taux de couverture{poste_txt}{annee_txt} : {num/den*100:.1f}%"


def compare_periods(poste=None, annee1=None, annee2=None, mesure="montant_rembourse"):
    """Compare une mesure entre deux années, évolution en % et en valeur."""
    poste = _vide(poste)
    err = _valider_parametres(poste)
    if err:
        return err

    mc = _normaliser_mesure(mesure)
    col, label = MESURES[mc], _libelle_mesure(mc)

    annee1, annee2 = _normaliser_annee(annee1), _normaliser_annee(annee2)
    if annee1 is None or annee2 is None:
        debut, fin = _annees_couvertes()
        return (f"Pour comparer, il me faut deux années précises "
                f"(entre {debut} et {fin}). Exemple : « compare le dentaire "
                f"entre 2022 et 2024 ». Pour l'évolution sur toute la période, "
                f"demandez plutôt « évolution du ... ».")
    for a in (annee1, annee2):
        err = _valider_annee(a)
        if err:
            return err

    v1 = _valeur_brute(col, poste, annee1)
    v2 = _valeur_brute(col, poste, annee2)
    if v1 is None or v2 is None:
        return _message_refus(poste) + " Comparaison impossible."

    poste_txt = f" ({poste})" if poste else ""
    base = (f"{label}{poste_txt} : {annee1} = {montant(v1, mc)}, "
            f"{annee2} = {montant(v2, mc)}.")
    if not v1:
        return base
    evol = (v2 - v1) / v1 * 100
    sens = "hausse" if evol >= 0 else "baisse"
    return f"{base} Évolution : {evol:+.1f}% ({sens})."


def top_postes(mesure="montant_rembourse", annee=None, n=5):
    """Classement des postes de soin par mesure (top N)."""
    mc = _normaliser_mesure(mesure)
    col, label = MESURES[mc], _libelle_mesure(mc)

    annee = _normaliser_annee(annee)
    err = _valider_annee(annee)
    if err:
        return err
    try:
        n = max(1, min(int(n), 20))
    except (TypeError, ValueError):
        n = 5

    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?")
        params.append(annee)

    rows = _executer(
        f'SELECT p.macro_categorie, SUM(f."{col}") s {_FROM_JOIN} '
        f'{_clause_where(where)} GROUP BY p.macro_categorie ORDER BY s DESC LIMIT ?',
        params + [n])

    titre = f"Top {n} postes — {label}" + (f" ({annee})" if annee else "")
    texte = titre + " : " + " | ".join(f"{m} : {montant(s, mc)}" for m, s in rows)
    lignes = [[str(m), float(s or 0)] for m, s in rows]
    return texte + bloc_table(["Poste de soin", label], lignes, titre, _unite(mc))


# Dimensions ventilables -> colonne SQL. Table blanche : `dimension` vient du
# LLM et est interpolee dans le SQL, elle DOIT rester bornee a ces cles.
_DIMENSIONS_SQL = {
    "region": "f.ben_res_reg",
    "age": "f.age_ben_snds",
    "sexe": "f.ben_sex_cod",
    "poste": "p.macro_categorie",
}


def repartition(mesure="montant_rembourse", dimension="poste", annee=None,
                poste=None, sous_categorie=None, decoupage=None):
    """Ventile une mesure selon une dimension : poste, region, age ou sexe."""
    poste, sous_categorie, decoupage = _vide(poste), _vide(sous_categorie), _vide(decoupage)
    err = _valider_parametres(poste, sous_categorie, decoupage)
    if err:
        return err
    annee = _normaliser_annee(annee)
    err = _valider_annee(annee)
    if err:
        return err

    mc = _normaliser_mesure(mesure)
    col, label = MESURES[mc], _libelle_mesure(mc)

    d = sans_accent(dimension)
    if d not in _DIMENSIONS_SQL:
        return _refus_parametre("dimension", dimension, list(_DIMENSIONS_SQL))

    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?")
        params.append(annee)
    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag)
        params += cible_params

    colonne = _DIMENSIONS_SQL[d]
    rows = _executer(
        f'SELECT {colonne} k, SUM(f."{col}") s {_FROM_JOIN} '
        f'{_clause_where(where)} GROUP BY {colonne} ORDER BY s DESC', params)

    traduire = d in ("region", "age", "sexe")
    def _k(k):
        return _vers_libelle(d, k) if traduire else str(k)

    an = f" ({annee})" if annee else ""
    cible = f" pour {cible_txt}" if cible_txt else ""
    titre = f"Répartition par {d}{an}{cible} — {label}"
    # Le texte n'affiche que les 12 premieres lignes ; le tableau les porte toutes.
    texte = titre + " : " + " | ".join(f"{_k(k)} : {montant(s, mc)}" for k, s in rows[:12])
    lignes = [[_k(k), float(s or 0)] for k, s in rows]
    return texte + bloc_table([d.capitalize(), label], lignes, titre, _unite(mc))


def evolution_serie(mesure="montant_rembourse", poste=None,
                    sous_categorie=None, decoupage=None):
    """Série temporelle d'une mesure sur toutes les années du périmètre."""
    poste, sous_categorie, decoupage = _vide(poste), _vide(sous_categorie), _vide(decoupage)
    err = _valider_parametres(poste, sous_categorie, decoupage)
    if err:
        return err

    mc = _normaliser_mesure(mesure)
    col, label = MESURES[mc], _libelle_mesure(mc)

    where, params = [], []
    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag)
        params += cible_params

    rows = _executer(
        f'SELECT f.soi_ann a, SUM(f."{col}") s {_FROM_JOIN} '
        f'{_clause_where(where)} GROUP BY f.soi_ann ORDER BY f.soi_ann', params)

    titre = f"Évolution {label}" + (f" pour {cible_txt}" if cible_txt else "")
    texte = titre + " : " + " | ".join(f"{a} : {montant(s, mc)}" for a, s in rows)
    lignes = [[int(a), float(s or 0)] for a, s in rows]
    return texte + bloc_table(["Année", label], lignes, titre, _unite(mc))


# ---------------------------------------------------------------------------
# Glossaire
# ---------------------------------------------------------------------------
_SECTIONS_DICO = ("postes", "themes_specifiques", "glossaire", "mesures", "dimensions")


def _synonymes_entree(cle, val):
    """Formes normalisées reconnues pour une entrée (clé + label + synonymes)."""
    formes = {cle}
    if isinstance(val, dict):
        if val.get("label"):
            formes.add(val["label"])
        formes.update(val.get("synonymes") or [])
    return {sans_accent(f) for f in formes if f}


def _rendre_entree(cle, val):
    """Assemble la réponse : définition + référence légale + note de périmètre."""
    if isinstance(val, str):
        return f"{cle} : {val}"
    lib = val.get("label") or cle
    dfn = val.get("definition") or val.get("note") or ""
    txt = f"{lib} : {dfn}" if dfn else lib
    if val.get("base_legale"):
        txt += f" (réf. : {val['base_legale']})"
    if val.get("note_perimetre"):
        txt += f"\n⚠ {val['note_perimetre']}"
    return txt


def _entrees_glossaire():
    """Itère sur (cle, valeur) de toutes les sections de definition du dico."""
    for section in _SECTIONS_DICO:
        for cle, val in DICO.get(section, {}).items():
            if not cle.startswith("_"):
                yield cle, val


def get_dictionnaire(terme):
    """Définition d'un terme métier. Correspondance exacte d'abord, puis repli
    par mot entier. Rend definition + base_legale + note_perimetre si présents."""
    t = sans_accent(terme)
    if not t:
        return "Terme vide : précisez le mot ou l'acronyme à définir."

    for cle, val in _entrees_glossaire():
        if t in _synonymes_entree(cle, val):
            return _rendre_entree(cle, val)

    # Repli par mot entier : n'est tente qu'a partir de 3 caracteres, sinon un
    # sigle court produirait des correspondances arbitraires.
    if len(t) >= 3:
        for cle, val in _entrees_glossaire():
            if any(match_mot_entier(t, forme) for forme in _synonymes_entree(cle, val)):
                return _rendre_entree(cle, val)

    return (f"Terme '{terme}' non trouvé dans le dictionnaire. "
            f"Périmètre : Assurance Maladie Obligatoire (Open DAMIR 2022-2025).")


# ---------------------------------------------------------------------------
# Referentiel des variables Open DAMIR : outils meta (schema, pas donnees)
# ---------------------------------------------------------------------------
def _variables_exploitees():
    """Ensemble des colonnes réellement exploitées en V1 (dimensions + mesures)."""
    ex = set()
    for d in DICO.get("dimensions", {}).values():
        if isinstance(d, dict) and d.get("colonne"):
            ex.update(c.strip().upper() for c in str(d["colonne"]).replace("/", " ").split())
    for m in DICO.get("mesures", {}).values():
        if isinstance(m, dict):
            for k in ("colonne_flt", "colonne_brute", "colonne"):
                if m.get(k):
                    ex.add(str(m[k]).upper())
    return ex


def _index_variables():
    """Aplatit variables_open_damir en {NOM_VARIABLE: (categorie, meta)}."""
    idx = {}
    for cat, contenu in DICO.get("variables_open_damir", {}).items():
        if cat.startswith("_") or not isinstance(contenu, dict):
            continue
        for var, meta in contenu.items():
            idx[var.upper()] = (cat, meta)
    return idx


def decrire_variable(nom):
    """Décrit une variable Open DAMIR : libellé, catégorie, description, modalités
    et statut d'exploitation. Recherche par code ('PRS_REM_TYP') ou par libellé."""
    idx = _index_variables()
    exploitees = _variables_exploitees()

    cible = str(nom).strip().upper()
    match = cible if cible in idx else None
    if not match:
        nn = sans_accent(nom)
        for var, (_, meta) in idx.items():
            lib = sans_accent(meta.get("libelle", ""))
            if lib == nn or (len(nn) >= 4 and nn in lib):
                match = var
                break
    if not match:
        return (f"Variable « {nom} » non documentée dans le référentiel Open DAMIR. "
                f"Utilisez lister_variables pour voir les catégories disponibles.")

    cat, meta = idx[match]
    statut = ("exploitée par l'assistant (V1)" if match in exploitees
              else "documentée, non exploitée en V1")
    out = [f"{match} — {meta.get('libelle', '')}", f"Catégorie : {cat}", f"Statut : {statut}"]

    com = (meta.get("commentaire") or "").strip()
    if com:
        out.append("Description : " + com[:280])

    mod = DICO.get("modalites_cles", {}).get(match)
    if isinstance(mod, dict) and mod.get("modalites"):
        items = list(mod["modalites"].items())
        apercu = "; ".join(f"{k}={v}" for k, v in items[:8])
        out.append(f"Modalités ({len(items)}) : {apercu}" + ("…" if len(items) > 8 else ""))
    return "\n".join(out)


def lister_variables(categorie=None):
    """Liste les variables Open DAMIR, éventuellement filtrées par catégorie.
    ✓ = exploitée en V1."""
    vod = DICO.get("variables_open_damir", {})
    exploitees = _variables_exploitees()
    cats = [c for c in vod if not c.startswith("_") and isinstance(vod[c], dict)]

    if not categorie:
        return ("Catégories de variables Open DAMIR : "
                + ", ".join(f"{c} ({len(vod[c])})" for c in cats)
                + ". Préciser une catégorie pour le détail.")

    cn = sans_accent(categorie)
    cible = next((c for c in cats if sans_accent(c) == cn or cn in sans_accent(c)), None)
    if not cible:
        return f"Catégorie « {categorie} » inconnue. Catégories : {', '.join(cats)}."

    lignes = [f"{'✓' if v.upper() in exploitees else '·'} {v} — {m.get('libelle', '')}"
              for v, m in vod[cible].items()]
    return f"Variables [{cible}] :\n" + "\n".join(lignes) + "\n(✓ = exploitée en V1)"


def list_valeurs(dimension):
    """Liste les valeurs possibles d'une dimension."""
    d = sans_accent(dimension)
    if d == "poste":
        m = DICO.get("classification_postes", {}).get("macro_categories", {})
        return "Postes : " + " | ".join(m.keys())
    if d in ("region", "age", "sexe"):
        v = DICO.get("dimensions", {}).get(d, {}).get("valeurs_disponibles", {})
        if isinstance(v, dict):
            return f"{d} : " + ", ".join(str(x) for x in v.values())
    if d.startswith("ann"):
        debut, fin = _annees_couvertes()
        return "Années : " + ", ".join(str(a) for a in range(debut, fin + 1))
    return f"Dimension '{dimension}' inconnue."
