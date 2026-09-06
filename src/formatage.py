"""
src/formatage.py — Formatage des valeurs et transport du tableau structuré.

SOURCE UNIQUE. Avant, `extraire_table` était réécrite dans tools.py, app.py,
app_streamlit.py et api.py, avec des comportements divergents (certaines
versions coupaient l'énumération du texte, d'autres non). Les interfaces
pouvaient donc afficher un texte différent pour la même réponse.

Deux formatages coexistent volontairement :
  - `montant()`   : pour la PHRASE de réponse (unité incluse, 2 décimales).
  - `format_valeur()` : pour les CELLULES de tableau et les étiquettes de
    graphique (plus compact, entier en dessous du million).
"""
import json

# Marqueur invisible pour un client texte, exploité par les interfaces.
MARQ_DEBUT = "\n\n<!--DAMIA_TABLE:"
MARQ_FIN = "-->"

# Forme sans saut de ligne, pour la recherche dans un texte deja assemble.
_MARQ_DEBUT_NU = MARQ_DEBUT.strip()


def bloc_table(colonnes, lignes, titre=None, unite=None):
    """Sérialise un tableau exportable à la fin de la réponse texte.

    Invisible pour un client texte classique, exploité par l'interface
    (affichage en tableau + export CSV). Le texte reste la source de vérité.
    """
    payload = {"titre": titre, "colonnes": colonnes, "lignes": lignes, "unite": unite}
    return MARQ_DEBUT + json.dumps(payload, ensure_ascii=False) + MARQ_FIN


def extraire_table(reponse, couper_enumeration=False):
    """Sépare le texte lisible du tableau structuré éventuel.

    Renvoie (texte, table|None).

    couper_enumeration=True (interfaces graphiques) : si le texte commence par
    le titre du tableau, on ne garde QUE le titre — le tableau affiché remplace
    l'énumération « poste : montant | poste : montant | … ».
    """
    if not reponse or _MARQ_DEBUT_NU not in reponse:
        return (reponse, None)
    try:
        debut = reponse.index(_MARQ_DEBUT_NU)
        fin = reponse.index(MARQ_FIN, debut)
        table = json.loads(reponse[debut + len(_MARQ_DEBUT_NU):fin])
    except (ValueError, json.JSONDecodeError):
        return (reponse, None)

    texte = reponse[:debut].rstrip()
    if couper_enumeration:
        titre = table.get("titre")
        if titre and texte.startswith(titre):
            texte = titre
    return (texte, table)


def montant(val, mesure="montant_rembourse"):
    """Formate une valeur pour la phrase de réponse, en français.

    'nombre_actes' -> « 1 234 actes » ; sinon Md€ / M€ / €.
    """
    val = val or 0
    if mesure == "nombre_actes":
        return f"{val:,.0f} actes".replace(",", " ")
    if abs(val) >= 1e9:
        return f"{val/1e9:.2f} Md€".replace(".", ",")
    if abs(val) >= 1e6:
        return f"{val/1e6:.2f} M€".replace(".", ",")
    return f"{val:,.2f} €".replace(",", " ").replace(".", ",")


def format_valeur(val, unite="€"):
    """Formate une valeur pour une cellule de tableau ou une étiquette de graphe."""
    val = float(val or 0)
    if unite == "actes":
        return f"{val:,.0f}".replace(",", " ")
    if abs(val) >= 1e9:
        return f"{val/1e9:,.2f} Md€".replace(",", " ").replace(".", ",")
    if abs(val) >= 1e6:
        return f"{val/1e6:,.2f} M€".replace(",", " ").replace(".", ",")
    return f"{val:,.0f} €".replace(",", " ")
