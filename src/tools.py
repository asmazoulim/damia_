"""
Logique metier des outils MCP : requetes DuckDB + formatage.
Separe du serveur pour etre testable independamment.

CORRECTIONS / OPTIMISATIONS :
  - Routage des postes DATA-DRIVEN depuis le dictionnaire (champ `synonymes` + `filtre`)
    au lieu d'une liste codee en dur -> le dictionnaire est la SOURCE UNIQUE de verite.
    Modifier le JSON modifie desormais reellement le comportement.
  - Refus hors-perimetre et message AMC tires du dictionnaire (reponses_specialisees).
  - Connexion DuckDB persistante (read-only) reutilisee -> plus rapide qu'une
    ouverture/fermeture a chaque requete.
"""
import sys
import json
import unicodedata
from pathlib import Path

from torch import where
from certifi import where
sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from config.config import CHEMIN_DB, CHEMIN_DICO, MESURES

# --- Dictionnaire charge une fois ---
with open(CHEMIN_DICO, encoding="utf-8") as f:
    DICO = json.load(f)


# --- Connexion DuckDB persistante (read-only), ouverte paresseusement ---
_CON = None
def _connexion():
    global _CON
    if _CON is None:
        _CON = duckdb.connect(str(CHEMIN_DB), read_only=True)
    return _CON


def _sans_accent(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', str(s))
                if unicodedata.category(c) != 'Mn').lower().strip()
    # normalise tirets, apostrophes et espaces multiples
    for ch in ("-", "'", "’", "_"):
        s = s.replace(ch, " ")
    return " ".join(s.split())

def _modalites(dimension):
    """Renvoie le dict des modalites d'une dimension : {code: {libelle, synonymes}}."""
    return DICO.get("dimensions", {}).get(dimension, {}).get("modalites", {}) or {}
 
 
def _vers_code(dimension, valeur):
    """ENTREE : traduit ce que dit l'utilisateur en code base.
    'homme' -> '1', 'ile de france' -> '11', '11' -> '11'.
    Renvoie None si non reconnu (le validateur refusera)."""
    if valeur is None:
        return None
    v = _sans_accent(valeur)
    mods = _modalites(dimension)
    if str(valeur).strip() in mods:
        return str(valeur).strip()
    for code, conf in mods.items():
        if _sans_accent(conf.get("libelle", "")) == v:
            return code
    for code, conf in mods.items():
        if any(_sans_accent(s) == v for s in conf.get("synonymes", [])):
            return code
    for code, conf in mods.items():
        if any(_sans_accent(s) and _sans_accent(s) in v for s in conf.get("synonymes", [])):
            return code
    return None
 
 
def _vers_libelle(dimension, code):
    """SORTIE : traduit un code base en libelle lisible. '11' -> 'Île-de-France'."""
    conf = _modalites(dimension).get(str(code))
    return conf.get("libelle", str(code)) if conf else str(code)

# ---------------------------------------------------------------------------
# Routage des postes construit À PARTIR DU DICTIONNAIRE (source unique)
# ---------------------------------------------------------------------------
def _construire_routes():
    """Construit la table de routage poste -> (colonne, valeur) depuis le dico.
    Chaque poste fournit ses `synonymes` et son `filtre` {colonne, valeur}.
    La cle du poste elle-meme sert aussi de synonyme."""
    routes = []
    for nom, conf in DICO.get("postes", {}).items():
        if not isinstance(conf, dict) or "filtre" not in conf:
            continue
        cles = [nom] + list(conf.get("synonymes", []))
        cles_norm = [_sans_accent(c) for c in cles if c]
        col = conf["filtre"]["colonne"]
        val = conf["filtre"]["valeur"]
        routes.append((cles_norm, col, val))
    return routes

_ROUTES = _construire_routes()
_MOTS_EXCLUS = [_sans_accent(m) for m in DICO.get("hors_perimetre", {}).get("mots_exclus", [])]
_MOTS_AMC = [_sans_accent(m) for m in DICO.get("hors_perimetre", {}).get("mots_amc", [])]

def _mots(texte):
    """Ensemble des mots normalises d'un texte (pour matching mot-entier)."""
    return set(_sans_accent(texte).split())


def _match_mots(cle, mots_texte):
    """Vrai si la cle (mot ou expression) est presente en MOTS ENTIERS dans le texte.
    Evite que 'dentaire' matche 'protheses dentaires' ou 'dents' matche 'dentifrice'."""
    cle_mots = _sans_accent(cle).split()
    if not cle_mots:
        return False
    return all(m in mots_texte for m in cle_mots)


def _clause_poste(poste):
    """Renvoie (fragment_sql, params, reconnu).
    reconnu=False -> poste hors perimetre : il faut REFUSER (anti-hallucination)."""
    p = _sans_accent(poste)
    # 1) exclusions explicites (esthetique, confort, AMC...) -> refus
    if any(m in p for m in _MOTS_EXCLUS) or any(m in p for m in _MOTS_AMC):
        return (None, [], False)
    # 2) routage depuis le dico
    for cles_norm, col, val in _ROUTES:
        if any(c in p for c in cles_norm):
            return (f"p.{col} = ?", [val], True)
    # 3) inconnu -> on ne devine pas
    return (None, [], False)

# ---------------------------------------------------------------------------
# Enrichissement + résolution de cible (bloc complet à ajouter)
# ---------------------------------------------------------------------------
def _poste_dico(poste):
    """Retrouve la clé de poste du dictionnaire correspondant à l'entrée utilisateur."""
    if not poste:
        return None
    p = _sans_accent(poste)
    for nom, conf in DICO.get("postes", {}).items():
        if not isinstance(conf, dict):
            continue
        cles = [nom] + list(conf.get("synonymes", []))
        if any(_sans_accent(c) and _sans_accent(c) in p for c in cles):
            return nom
    return None


def _note_poste(poste):
    """Renvoie la note contextuelle du dico pour ce poste (ou '')."""
    nom = _poste_dico(poste)
    if not nom:
        return ""
    conf = DICO["postes"].get(nom, {})
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


def _montant(val, mesure):
    """Formate un nombre seul (sans label), en français."""
    val = val or 0
    if mesure == "nombre_actes":
        return f"{val:,.0f} actes".replace(",", " ")
    if abs(val) >= 1e9:
        return f"{val/1e9:.2f} Md€".replace(".", ",")
    if abs(val) >= 1e6:
        return f"{val/1e6:.2f} M€".replace(".", ",")
    return f"{val:,.2f} €".replace(",", " ").replace(".", ",")


_MARQ_DEBUT = "\n\n<!--DAMIA_TABLE:"
_MARQ_FIN = "-->"
 
 
def _bloc_table(colonnes, lignes, titre=None, unite=None):
    """Serialise un tableau exportable a la fin de la reponse texte.
    Invisible pour un client texte classique, exploite par l'interface
    (affichage en tableau + export CSV). Le texte reste la source de verite."""
    payload = {"titre": titre, "colonnes": colonnes, "lignes": lignes, "unite": unite}
    return _MARQ_DEBUT + json.dumps(payload, ensure_ascii=False) + _MARQ_FIN
 
 
def extraire_table(reponse):
    """Recupere le tableau structure d'une reponse, ou None.
    Utilise par l'interface. Renvoie (texte_sans_marqueur, dict_table|None)."""
    if not reponse or _MARQ_DEBUT.strip() not in reponse:
        return (reponse, None)
    try:
        debut = reponse.index(_MARQ_DEBUT)
        fin = reponse.index(_MARQ_FIN, debut)
        brut = reponse[debut + len(_MARQ_DEBUT):fin]
        return (reponse[:debut].rstrip(), json.loads(brut))
    except (ValueError, json.JSONDecodeError):
        return (reponse, None)

def _message_refus(poste):
    """Message de refus propre pour un poste hors périmètre."""
    p = _sans_accent(poste) if poste else ""
    rs = DICO.get("hors_perimetre", {}).get("reponses_specialisees", {})
    mots_amc = [_sans_accent(m) for m in DICO.get("hors_perimetre", {}).get("mots_amc", [])]
    if any(m in p for m in mots_amc) and "amc_mutuelle" in rs:
        return rs["amc_mutuelle"]
    return (f"Le poste « {poste} » ne fait pas partie du périmètre Open DAMIR "
            f"(soins remboursés par l'Assurance Maladie, 2022-2025). "
            f"Aucune donnée disponible — je ne peux pas avancer de chiffre. "
            f"Je peux en revanche vous renseigner un poste réellement couvert "
            f"(pharmacie, hospitalisation, optique, dentaire, audioprothèse, imagerie).")


# ---- Granularité : sous-catégorie (niveau 2) et découpage fin prs_nat (niveau 3) ----
def _clause_sous_categorie(sous_cat):
    if not sous_cat:
        return (None, [])
    return ("p.sous_categorie_cor = ?", [sous_cat])


def _sous_categorie_valide(sous_cat):
    if not sous_cat:
        return None
    s = _sans_accent(sous_cat)
    for domaine, liste in DICO.get("catalogue_sous_categories", {}).items():
        if domaine.startswith("_") or not isinstance(liste, list):
            continue
        for val in liste:
            if _sans_accent(val) == s or s in _sans_accent(val):
                return val
    return None


def _decoupage_match(terme):
    """Cherche un decoupage fin correspondant au terme, dans DICO['decoupages_fins'].
    Renvoie (nom_groupe, cible, type_cible) ou (None, None, None).
    type_cible = 'codes' (prs_nat) ou 'sous_categories'."""
    if not terme:
        return (None, None, None)
    t = _sans_accent(terme)
    for bloc_nom, bloc in DICO.get("decoupages_fins", {}).items():
        if bloc_nom.startswith("_") or not isinstance(bloc, dict):
            continue
        for groupe, gconf in bloc.get("groupes", {}).items():
            cles = [groupe] + list(gconf.get("synonymes", []))
            if any(_sans_accent(c) and _sans_accent(c) in t for c in cles if c):
                if gconf.get("codes"):
                    return (groupe, gconf["codes"], "codes")
                if gconf.get("sous_categories"):
                    return (groupe, gconf["sous_categories"], "sous_categories")
    return (None, None, None)


def _clause_decoupage(cible, type_cible="codes"):
    """Filtre niveau 3 : liste de codes prs_nat OU liste de sous-categories."""
    if not cible:
        return (None, [])
    ph = ",".join("?" for _ in cible)
    if type_cible == "sous_categories":
        return (f"p.sous_categorie_cor IN ({ph})", [str(c) for c in cible])
    return (f"CAST(p.prs_nat AS VARCHAR) IN ({ph})", [str(c) for c in cible])


def _resoudre_cible(poste=None, sous_categorie=None, decoupage=None):
    """Resout la cible au niveau le plus fin demande (decoupage > sous_cat > poste)."""
    if not decoupage:
        for cand in (sous_categorie, poste):
            if cand:
                g, cible, typ = _decoupage_match(cand)
                if g and cible:
                    decoupage = cand
                    if sous_categorie == cand: sous_categorie = None
                    if poste == cand: poste = None
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
            frag, params = _clause_sous_categorie(cat)
            return (frag, params, f"« {cat} »", False)
    if poste:
        frag, p_params, reconnu = _clause_poste(poste)
        if not reconnu:
            return (None, [], "", True)
        return (frag, p_params, f"poste « {poste} »", False)
    return (None, [], "", False)

def _vide(x):
    """Le LLM envoie souvent '' / 'null' / 'none' au lieu d'omettre un parametre."""
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        if s == "" or s.lower() in ("null", "none", "n/a", "aucun", "-"):
            return None
        return s
    return x


def _refus_parametre(nom, valeur, possibles=None):
    """Refus quand un parametre est fourni mais NON RECONNU (fail-closed)."""
    msg = (f"Le paramètre « {nom} » vaut « {valeur} », que je ne reconnais pas. "
           f"Je préfère ne pas répondre plutôt que de donner un chiffre qui "
           f"ignorerait ce filtre.")
    if possibles:
        apercu = ", ".join(str(p) for p in list(possibles)[:12])
        msg += f" Valeurs possibles : {apercu}."
    return msg


def _decoupages_disponibles():
    noms = []
    for bloc_nom, bloc in DICO.get("decoupages_fins", {}).items():
        if bloc_nom.startswith("_") or not isinstance(bloc, dict):
            continue
        noms.extend(bloc.get("groupes", {}).keys())
    return noms


def _valider_parametres(poste=None, sous_categorie=None, decoupage=None,
                        region=None, age=None, sexe=None):
    """FAIL-CLOSED : un parametre fourni mais non reconnu -> refus.
    Les dimensions sont validees APRES traduction en code (voir _traduire_dimensions)."""
    if decoupage and not _decoupage_match(decoupage)[0]:
        return _refus_parametre("decoupage", decoupage, _decoupages_disponibles())
 
    if sous_categorie and not _sous_categorie_valide(sous_categorie) \
            and not _decoupage_match(sous_categorie)[0]:
        cats = []
        for dom, lst in DICO.get("catalogue_sous_categories", {}).items():
            if not dom.startswith("_") and isinstance(lst, list):
                cats.extend(lst)
        return _refus_parametre("sous_categorie", sous_categorie, cats)
 
    for nom, val in (("region", region), ("age", age), ("sexe", sexe)):
        if val is not None and _vers_code(nom, val) is None:
            libelles = [c.get("libelle") for c in _modalites(nom).values()]
            return _refus_parametre(nom, val, libelles)
    return None

def _traduire_dimensions(region=None, age=None, sexe=None):
    """Traduit les 3 dimensions en codes base. À appeler APRÈS validation."""
    return (_vers_code("region", region) if region is not None else None,
            _vers_code("age", age) if age is not None else None,
            _vers_code("sexe", sexe) if sexe is not None else None)

def _annees_couvertes():
    p = DICO.get("perimetre", {}).get("assistant_v1", {}).get("annees", "2022-2025")
    try:
        a, b = str(p).split("-")
        return int(a), int(b)
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

def _normaliser_mesure(mesure):
    """Tolere les variantes du LLM : 's' final, accents, casse, synonymes."""
    m = _sans_accent(mesure).rstrip('s')
    SYN = {
        "montant rembourse": "montant_rembourse", "montant_rembourse": "montant_rembourse",
        "rembourse": "montant_rembourse", "remboursement": "montant_rembourse",
        "depense engagee": "depense_engagee", "depense_engagee": "depense_engagee",
        "depense": "depense_engagee", "paiement": "depense_engagee",
        "nombre acte": "nombre_actes", "nombre_acte": "nombre_actes", "acte": "nombre_actes",
        "base remboursement": "base_remboursement", "base_remboursement": "base_remboursement",
        "base": "base_remboursement",
        "depassement": "depassement", "depassement honoraire": "depassement",
    }
    if m in SYN:
        return SYN[m]
    for cle in MESURES:
        if _sans_accent(cle).rstrip('s') == m:
            return cle
    return None


def _normaliser_annee(annee):
    """Tolere tout ce que le LLM peut envoyer : int, '2023', ['2023',...],
    '2022-2025', '', None. Renvoie un int unique, ou None (= pas de filtre annee)."""
    if annee is None or annee == "":
        return None
    if isinstance(annee, (list, tuple)):
        # une liste d'annees -> on ne filtre pas (tout le perimetre)
        return None
    s = str(annee).strip()
    if "-" in s or "/" in s:
        # une plage type "2022-2025" -> pas de filtre (tout le perimetre)
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None
    
def query_depenses(mesure="montant_rembourse", poste=None, annee=None,
                   region=None, age=None, sexe=None,
                   sous_categorie=None, decoupage=None):
    """Calcule une mesure avec filtres optionnels (poste > sous_categorie > decoupage)."""
    poste, region, age, sexe = _vide(poste), _vide(region), _vide(age), _vide(sexe)
    sous_categorie, decoupage = _vide(sous_categorie), _vide(decoupage)
 
    # --- Validation fail-closed ---
    err = _valider_parametres(poste, sous_categorie, decoupage, region, age, sexe)
    if err:
        return err
    annee = _normaliser_annee(annee)
    err = _valider_annee(annee)
    if err:
        return err
 
    # --- Traduction des dimensions en codes base ('homme' -> '1') ---
    region, age, sexe = _traduire_dimensions(region, age, sexe)
 
    mesure_canon = _normaliser_mesure(mesure)
    if mesure_canon is None:
        mesure_canon = "montant_rembourse"
    mesure = mesure_canon
    col = MESURES[mesure]
    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?"); params.append(annee)
 
    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag); params += cible_params
 
    filtres_txt = []
    if region is not None:
        where.append("f.ben_res_reg = ?"); params.append(region)
        filtres_txt.append(_vers_libelle("region", region))
    if age is not None:
        where.append("f.age_ben_snds = ?"); params.append(age)
        filtres_txt.append(_vers_libelle("age", age))
    if sexe is not None:
        where.append("f.ben_sex_cod = ?"); params.append(sexe)
        filtres_txt.append(_vers_libelle("sexe", sexe))
 
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f'''SELECT SUM(f."{col}") FROM faits f
            JOIN prestations p
              ON CAST(f.prs_nat AS VARCHAR) = CAST(p.prs_nat AS VARCHAR)
            {clause}'''
    res = _connexion().execute(sql, params).fetchone()
    val = res[0] if res and res[0] is not None else 0
 
    label = DICO["mesures"].get(mesure, {}).get("label", mesure)
    cible_phrase = f" pour {cible_txt}" if cible_txt else ""
    debut, fin = _annees_couvertes()
    periode = f" en {annee}" if annee else f" sur la période {debut}-{fin}"
    filtre_txt = (" — " + ", ".join(filtres_txt)) if filtres_txt else ""
    phrase = f"{label}{cible_phrase}{periode}{filtre_txt} : {_montant(val, mesure)}."
    ctx = _contexte_final(poste) if poste and not decoupage and not sous_categorie else ""
    return phrase + ctx


def _formater(val, mesure, label):
    val = val or 0
    if mesure == "nombre_actes":
        return f"{label} : {val:,.0f} actes".replace(",", " ")
    if abs(val) >= 1e9:
        return f"{label} : {val/1e9:.2f} Md EUR"
    if abs(val) >= 1e6:
        return f"{label} : {val/1e6:.2f} M EUR"
    return f"{label} : {val:,.2f} EUR".replace(",", " ")


def _valeur_brute(mesure_col, poste, annee):
    """Valeur numerique brute, ou None si poste hors perimetre."""
    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?"); params.append(int(annee))
    if poste is not None:
        frag, p_params, reconnu = _clause_poste(poste)
        if not reconnu:
            return None
        where.append(frag); params += p_params
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f'''SELECT SUM(f."{mesure_col}") FROM faits f
            JOIN prestations p ON CAST(f.prs_nat AS VARCHAR)=CAST(p.prs_nat AS VARCHAR)
            {clause}'''
    res = _connexion().execute(sql, params).fetchone()
    return res[0] if res and res[0] is not None else 0

def taux_couverture(poste=None, annee=None):
    num = _valeur_brute(MESURES["montant_rembourse"], poste, annee)
    den = _valeur_brute(MESURES["depense_engagee"], poste, annee)
    if num is None or den is None:
        return _message_refus(poste)
    if not den:
        return "Donnée insuffisante pour calculer un taux."
    poste_txt = f" ({poste})" if poste else ""
    return f"Taux de couverture{poste_txt} : {num/den*100:.1f}%"

def compare_periods(poste=None, annee1=None, annee2=None, mesure="montant_rembourse"):
    """Compare une mesure entre deux années, évolution en % et valeur."""
    poste = _vide(poste)
    err = _valider_parametres(poste)
    if err:
        return err

    mc = _normaliser_mesure(mesure)
    if mc is None:
        mc = "montant_rembourse"
    col = MESURES[mc]
    label = DICO["mesures"].get(mc, {}).get("label", mc)

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
    if v1:
        evol = (v2 - v1) / v1 * 100
        sens = "hausse" if evol >= 0 else "baisse"
        return (f"{label}{poste_txt} : {annee1} = {_montant(v1, mc)}, "
                f"{annee2} = {_montant(v2, mc)}. Évolution : {evol:+.1f}% ({sens}).")
    return f"{label}{poste_txt} : {annee1} = {_montant(v1, mc)}, {annee2} = {_montant(v2, mc)}."


 
 
def top_postes(mesure="montant_rembourse", annee=None, n=5):
    """Classement des postes de soin par mesure (top N)."""
    mc = _normaliser_mesure(mesure)
    if mc is None:
        mc = "montant_rembourse"
    col = MESURES[mc]
    label = DICO["mesures"].get(mc, {}).get("label", mc)
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
        where.append("f.soi_ann = ?"); params.append(annee)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f'''SELECT p.macro_categorie, SUM(f."{col}") s FROM faits f
            JOIN prestations p ON CAST(f.prs_nat AS VARCHAR)=CAST(p.prs_nat AS VARCHAR)
            {clause} GROUP BY p.macro_categorie ORDER BY s DESC LIMIT ?'''
    rows = _connexion().execute(sql, params + [n]).fetchall()
 
    an = f" ({annee})" if annee else ""
    titre = f"Top {n} postes — {label}{an}"
    texte = titre + " : " + " | ".join(f"{m} : {_montant(s, mc)}" for m, s in rows)
    colonnes = ["Poste de soin", label]
    lignes = [[str(m), float(s or 0)] for m, s in rows]
    unite = "actes" if mc == "nombre_actes" else "€"
    return texte + _bloc_table(colonnes, lignes, titre, unite)
 
 
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
    if mc is None:
        mc = "montant_rembourse"
    col = MESURES[mc]
    label = DICO["mesures"].get(mc, {}).get("label", mc)
 
    DIM = {"region": "f.ben_res_reg", "age": "f.age_ben_snds",
           "sexe": "f.ben_sex_cod", "poste": "p.macro_categorie"}
    d = _sans_accent(dimension)
    if d not in DIM:
        return _refus_parametre("dimension", dimension, list(DIM.keys()))
 
    where, params = [], []
    if annee is not None:
        where.append("f.soi_ann = ?"); params.append(annee)
    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag); params += cible_params
 
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f'''SELECT {DIM[d]} k, SUM(f."{col}") s FROM faits f
            JOIN prestations p ON CAST(f.prs_nat AS VARCHAR)=CAST(p.prs_nat AS VARCHAR)
            {clause} GROUP BY {DIM[d]} ORDER BY s DESC'''
    rows = _connexion().execute(sql, params).fetchall()
 
    an = f" ({annee})" if annee else ""
    cible = f" pour {cible_txt}" if cible_txt else ""
    traduire = d in ("region", "age", "sexe")
    def _k(k):
        return _vers_libelle(d, k) if traduire else str(k)
 
    titre = f"Répartition par {d}{an}{cible} — {label}"
    texte = titre + " : " + " | ".join(f"{_k(k)} : {_montant(s, mc)}" for k, s in rows[:12])
    colonnes = [d.capitalize(), label]
    lignes = [[_k(k), float(s or 0)] for k, s in rows]
    unite = "actes" if mc == "nombre_actes" else "€"
    return texte + _bloc_table(colonnes, lignes, titre, unite)

def evolution_serie(mesure="montant_rembourse", poste=None,
                    sous_categorie=None, decoupage=None):
    """Serie temporelle d'une mesure sur toutes les annees du perimetre."""
    poste, sous_categorie, decoupage = _vide(poste), _vide(sous_categorie), _vide(decoupage)
    err = _valider_parametres(poste, sous_categorie, decoupage)
    if err:
        return err
 
    mc = _normaliser_mesure(mesure)
    if mc is None:
        mc = "montant_rembourse"
    col = MESURES[mc]
    label = DICO["mesures"].get(mc, {}).get("label", mc)
 
    where, params = [], []
    frag, cible_params, cible_txt, refus = _resoudre_cible(poste, sous_categorie, decoupage)
    if refus:
        return _message_refus(poste or sous_categorie or decoupage)
    if frag:
        where.append(frag); params += cible_params
 
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f'''SELECT f.soi_ann a, SUM(f."{col}") s FROM faits f
            JOIN prestations p ON CAST(f.prs_nat AS VARCHAR)=CAST(p.prs_nat AS VARCHAR)
            {clause} GROUP BY f.soi_ann ORDER BY f.soi_ann'''
    rows = _connexion().execute(sql, params).fetchall()
 
    cible = f" pour {cible_txt}" if cible_txt else ""
    titre = f"Évolution {label}{cible}"
    texte = titre + " : " + " | ".join(f"{a} : {_montant(s, mc)}" for a, s in rows)
    colonnes = ["Année", label]
    lignes = [[int(a), float(s or 0)] for a, s in rows]
    unite = "actes" if mc == "nombre_actes" else "€"
    return texte + _bloc_table(colonnes, lignes, titre, unite)

_SECTIONS_DICO = ("postes", "themes_specifiques", "glossaire", "mesures", "dimensions")
 
 
def _norm(s):
    """Minuscule, sans accents, espaces normalisés."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())
 
 
def _entree_libelle_def(cle, val):
    """(libellé, définition) d'une entrée, qu'elle soit un dict ou une simple chaîne."""
    if isinstance(val, str):
        return cle, val
    return (val.get("label") or cle), (val.get("definition") or val.get("note") or "")
 
 
def _synonymes_entree(cle, val):
    """Formes normalisées reconnues pour une entrée (clé + label + synonymes)."""
    formes = {cle}
    if isinstance(val, dict):
        if val.get("label"):
            formes.add(val["label"])
        for s in val.get("synonymes", []) or []:
            formes.add(s)
    return {_norm(f) for f in formes if f}
 
 
def get_dictionnaire(terme):
    """Renvoie la définition d'un terme métier. Correspondance exacte (clé/label/
    synonymes) d'abord, puis repli par sous-chaîne, sur toutes les sections utiles."""
    t = _norm(terme)
    if not t:
        return "Terme vide : précisez le mot ou l'acronyme à définir."
 
    # 1) Correspondance exacte (fiable, notamment pour les acronymes AMO/AMC/C2S)
    for section in _SECTIONS_DICO:
        for cle, val in DICO.get(section, {}).items():
            if cle.startswith("_"):
                continue
            if t in _synonymes_entree(cle, val):
                lib, dfn = _entree_libelle_def(cle, val)
                return f"{lib} : {dfn}" if dfn else lib
 
    # 2) Repli par sous-chaîne — formes >= 4 caractères (évite les faux positifs)
    if len(t) >= 3:
        for section in _SECTIONS_DICO:
            for cle, val in DICO.get(section, {}).items():
                if cle.startswith("_"):
                    continue
                for forme in _synonymes_entree(cle, val):
                    if len(forme) >= 4 and (t in forme or forme in t):
                        lib, dfn = _entree_libelle_def(cle, val)
                        return f"{lib} : {dfn}" if dfn else lib
 
    return (f"Terme '{terme}' non trouvé dans le dictionnaire. "
            f"Périmètre : Assurance Maladie Obligatoire (Open DAMIR 2022-2025).")

def list_valeurs(dimension):
    """Liste les valeurs possibles d'une dimension."""
    d = dimension.lower().strip()
    if d == "poste":
        m = DICO.get("classification_postes", {}).get("macro_categories", {})
        return "Postes : " + " | ".join(m.keys())
    if d in ("region", "age", "sexe"):
        v = DICO.get("dimensions", {}).get(d, {}).get("valeurs_disponibles", {})
        if isinstance(v, dict):
            return f"{d} : " + ", ".join(str(x) for x in v.values())
    if d.startswith("ann"):
        return "Annees : 2022, 2023, 2024, 2025"
    return f"Dimension '{dimension}' inconnue."