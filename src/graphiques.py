"""
src/graphiques.py — Rendu des graphiques (matplotlib, charte VYV).

Isolé de l'interface : la fonction ne connaît ni Streamlit ni FastAPI, elle prend
un tableau structuré (produit par src/formatage.bloc_table) et rend des octets PNG.
Le même PNG sert à l'affichage et à l'export — ce qui est vu est ce qui est
téléchargé.
"""
import io

import matplotlib

matplotlib.use("Agg")          # backend sans fenetre : obligatoire cote serveur

import matplotlib.pyplot as plt  # noqa: E402  (doit suivre matplotlib.use)

from src.formatage import format_valeur

VIOLET_VYV = "#472583"
GRIS_VYV = "#878786"
GRIS_AXE = "#d9d9d9"
PALETTE = ["#472583", "#6342A6", "#8365CC", "#A58BF0", "#C8B5FF", "#EBE0FF"]

TYPES = ("Barres", "Lignes", "Camembert")
MAX_LIGNES_DEFAUT = 12


def _preparer(table, max_lignes):
    """Extrait (labels, valeurs, est_temporel, unite, titre) du tableau structuré.

    Une série temporelle garde l'ordre chronologique ; toute autre ventilation
    est triée par valeur décroissante (la plus grosse barre en premier)."""
    lignes = table.get("lignes") or []
    if not lignes:
        return None
    colonnes = table.get("colonnes") or ["", ""]
    est_temporel = str(colonnes[0]).lower().startswith("ann")

    donnees = list(lignes) if est_temporel else sorted(lignes, key=lambda r: r[1], reverse=True)
    donnees = donnees[:max_lignes]
    return ([str(r[0]) for r in donnees],
            [float(r[1] or 0) for r in donnees],
            est_temporel,
            table.get("unite") or "€",
            table.get("titre") or "")


def _masquer_bordures(ax, cotes):
    for cote in cotes:
        ax.spines[cote].set_visible(False)


def _tracer_lignes(ax, labels, valeurs, unite):
    ax.plot(labels, valeurs, color=VIOLET_VYV, marker="o", linewidth=2.5, markersize=8)
    ax.fill_between(labels, valeurs, alpha=0.1, color=VIOLET_VYV)
    ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
    for i, v in enumerate(valeurs):
        ax.text(i, v * 1.05, format_valeur(v, unite), ha="center", va="bottom",
                fontsize=8.5, color=VIOLET_VYV, fontweight="bold")
    ax.set_ylim(0, max(valeurs) * 1.25 if valeurs else 1)
    _masquer_bordures(ax, ("top", "right"))
    ax.spines["bottom"].set_color(GRIS_AXE)
    ax.spines["left"].set_color(GRIS_AXE)


def _tracer_camembert(ax, labels, valeurs):
    _, _, pourcentages = ax.pie(
        valeurs, labels=labels, autopct="%1.1f%%", startangle=90,
        colors=(PALETTE * (len(valeurs) // len(PALETTE) + 1))[:len(valeurs)],
        textprops=dict(color=GRIS_VYV, fontsize=9))
    for texte in pourcentages:
        texte.set_color("white")
        texte.set_fontweight("bold")
    ax.axis("equal")           # cercle parfait, pas une ellipse


def _tracer_colonnes(ax, labels, valeurs, unite):
    """Série temporelle : colonnes verticales, ordre chronologique."""
    ax.bar(labels, valeurs, color=VIOLET_VYV, width=0.5)
    ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
    for i, v in enumerate(valeurs):
        ax.text(i, v, " " + format_valeur(v, unite), ha="center", va="bottom",
                fontsize=8.5, color=GRIS_VYV)
    ax.set_ylim(0, max(valeurs) * 1.2 if valeurs else 1)
    ax.get_yaxis().set_visible(False)
    _masquer_bordures(ax, ("top", "right", "left"))


def _tracer_barres(ax, labels, valeurs, unite):
    """Ventilation : barres horizontales, la plus grande valeur en haut."""
    y = list(range(len(labels)))
    ax.barh(y, valeurs, color=VIOLET_VYV, height=0.55)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
    for i, v in enumerate(valeurs):
        ax.text(v, i, " " + format_valeur(v, unite), va="center",
                fontsize=8.5, color=GRIS_VYV)
    ax.set_xlim(0, max(valeurs) * 1.3 if valeurs else 1)
    ax.get_xaxis().set_visible(False)
    _masquer_bordures(ax, ("top", "right", "bottom"))
    ax.spines["left"].set_color(GRIS_AXE)


def construire_graphique(table, type_graphique="Barres", max_lignes=MAX_LIGNES_DEFAUT):
    """Rend le tableau en PNG (octets), ou None si le tableau est vide."""
    prepare = _preparer(table, max_lignes)
    if prepare is None:
        return None
    labels, valeurs, est_temporel, unite, titre = prepare

    hauteur = max(3.0, 0.45 * len(labels) + 1.5)
    fig, ax = plt.subplots(figsize=(9, hauteur), dpi=150)
    try:
        if type_graphique == "Lignes":
            _tracer_lignes(ax, labels, valeurs, unite)
        elif type_graphique == "Camembert":
            _tracer_camembert(ax, labels, valeurs)
        elif est_temporel:
            _tracer_colonnes(ax, labels, valeurs, unite)
        else:
            _tracer_barres(ax, labels, valeurs, unite)

        if titre:
            ax.set_title(titre, fontsize=12, color=VIOLET_VYV, fontweight="bold",
                         loc="center", pad=20)
        fig.tight_layout()
        tampon = io.BytesIO()
        fig.savefig(tampon, format="png", bbox_inches="tight", facecolor="white")
        return tampon.getvalue()
    finally:
        plt.close(fig)     # sans cela, les figures s'accumulent a chaque question
