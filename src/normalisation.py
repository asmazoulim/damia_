"""
src/normalisation.py — Normalisation et comparaison de texte, source unique.

Avant : trois copies quasi identiques de la meme fonction (`_sans_accent` dans
tools.py, `_norm` dans tools.py, `_sans_accent_hp` dans assistant.py), qui
divergeaient sur les tirets et les apostrophes. Un synonyme du dictionnaire
pouvait donc matcher dans un module et pas dans l'autre.

Ce module porte aussi la COMPARAISON par mot entier, utilisee pour router un
terme utilisateur vers une entree du dictionnaire. Le matching par sous-chaine
(`cle in texte`) qui existait avant produisait des faux positifs silencieux :
« prothese » matchait « audioprothese », donc une question sur l'audioprothese
renvoyait les chiffres du dentaire. Un mauvais routage est pire qu'un refus :
il produit un chiffre faux presente comme juste.
"""
import unicodedata

# Caracteres traites comme des separateurs de mots (tirets, apostrophes droites
# et typographiques, underscore) : « ile-de-france » == « ile de france ».
_SEPARATEURS = ("-", "'", "’", "_")

# En dessous de cette longueur, on ne coupe pas la marque du pluriel : « vis »,
# « os », « pas » ne doivent pas devenir « vi », « o », « pa ».
_LONGUEUR_MIN_RADICAL = 4


def sans_accent(s):
    """Minuscule, sans accent, séparateurs unifiés, espaces normalisés.

    'Île-de-France' -> 'ile de france'
    """
    s = "".join(c for c in unicodedata.normalize("NFD", str(s))
                if unicodedata.category(c) != "Mn").lower().strip()
    for ch in _SEPARATEURS:
        s = s.replace(ch, " ")
    return " ".join(s.split())


def radical(mot):
    """Retire la marque du pluriel français ('s' ou 'x' final).

    'dentaires' -> 'dentaire', 'verres' -> 'verre', 'os' -> 'os'.

    Ce n'est pas une lemmatisation : « temps » devient « temp ». C'est sans
    consequence, car la fonction est appliquee des DEUX cotes de toute
    comparaison — elle sert a faire coincider les formes, pas a dire le vrai.
    """
    if len(mot) >= _LONGUEUR_MIN_RADICAL and mot[-1] in ("s", "x"):
        return mot[:-1]
    return mot


def mots(texte):
    """Ensemble des mots normalisés d'un texte (sans radicalisation)."""
    return set(sans_accent(texte).split())


def tokens(texte):
    """Liste des mots normalisés ET radicalisés, dans l'ordre.

    'Prothèses dentaires' -> ['prothese', 'dentaire']
    """
    return [radical(m) for m in sans_accent(texte).split()]


def contient_expression(cle, texte):
    """Vrai si les mots de `cle` apparaissent CONSÉCUTIVEMENT dans `texte`,
    au pluriel près.

    C'est la comparaison utilisée pour le routage métier :
        contient_expression('dentaire', 'depenses dentaires')  -> True
        contient_expression('dentaire', 'dentifrice')          -> False
        contient_expression('prothese', 'audioprothese')       -> False
        contient_expression('soins dentaires', 'les soins dentaires') -> True
    """
    tc, tt = tokens(cle), tokens(texte)
    if not tc or len(tc) > len(tt):
        return False
    return any(tt[i:i + len(tc)] == tc for i in range(len(tt) - len(tc) + 1))


def match_mot_entier(terme, forme):
    """Correspondance BIDIRECTIONNELLE, pour la recherche au glossaire.

    Vrai si `terme` apparaît dans `forme`, ou l'inverse lorsque `forme` fait au
    moins 4 caractères. Le sens « forme dans terme » permet de retrouver une
    entrée courte à partir d'une formulation plus longue ; le seuil empêche
    qu'un fragment de 2-3 lettres ne matche n'importe quoi.
    """
    if not terme or not forme:
        return False
    return (contient_expression(terme, forme)
            or (len(forme) >= _LONGUEUR_MIN_RADICAL
                and contient_expression(forme, terme)))


def meilleur_match(cles, texte):
    """Renvoie la clé la PLUS SPÉCIFIQUE parmi `cles` qui matche `texte`, ou None.

    Spécificité = nombre de mots, puis longueur. Sans cet arbitrage, l'ordre de
    déclaration dans le dictionnaire déciderait du routage : pour « prothèses
    auditives », la clé générique « prothese » (découpage dentaire) l'emportait
    sur « prothèses auditives » (poste audioprothèse) au seul motif qu'elle
    était rencontrée en premier.
    """
    candidates = [c for c in cles if c and contient_expression(c, texte)]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (len(tokens(c)), len(c)))


def specificite(cle):
    """Score de spécificité d'une clé matchée : (nombre de mots, longueur).
    Sert à départager deux niveaux de granularité en concurrence."""
    return (len(tokens(cle)), len(cle)) if cle else (0, 0)
