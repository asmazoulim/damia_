"""
app_streamlit.py — Interface UX (chat) pour la démo MCP DAMIA.

Lancement :
    cd mcp_damia
    pip install streamlit          # si pas déjà fait
    streamlit run app_streamlit.py
"""
import sys
import io
import os
import matplotlib
import streamlit as st
import json
import pandas as pd
import matplotlib.pyplot as plt

matplotlib.use("Agg")


# rendre le dossier src/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


try:
    from src.assistant import poser_question as _fonction_reponse   
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:                       # noqa: BLE001
    _IMPORT_OK = False
    _IMPORT_ERR = e

    def _fonction_reponse(question):         # repli pour tester l'UI sans le moteur
        return ("[MODE DEMO UI — moteur non branché]\n"
                f"Question reçue : {question}")


@st.cache_resource
def _init():
    from src.mcp_client import get_mcp_client
    from src.backends import get_backend
    get_backend()
    get_mcp_client()   # force le chargement unique
    return True
_init()

# ------------------------------------------------------------------ #
#  ADAPTE LE FORMAT DE RETOUR                                        #
# ------------------------------------------------------------------ #
def interroger(question):
    res = _fonction_reponse(question)

    if isinstance(res, str):
        return {"reponse": res, "outil": None, "parametres": None, "hors_perimetre": False}

    if isinstance(res, dict):
        return {
            "reponse": res.get("reponse") or res.get("answer") or res.get("texte") or str(res),
            "outil": res.get("outil") or res.get("tool") or res.get("outil_appele"),
            "parametres": res.get("parametres") or res.get("params") or res.get("arguments"),
            "hors_perimetre": bool(res.get("hors_perimetre") or res.get("refus")),
        }

    if isinstance(res, (list, tuple)):
        reponse = res[0] if len(res) > 0 else ""
        outil = res[1] if len(res) > 1 else None
        params = res[2] if len(res) > 2 else None
        return {"reponse": reponse, "outil": outil, "parametres": params, "hors_perimetre": False}

    return {"reponse": str(res), "outil": None, "parametres": None, "hors_perimetre": False}

_MARQ_DEBUT = "<!--DAMIA_TABLE:"
_MARQ_FIN = "-->"
 
 
def extraire_table(reponse):
    """Sépare le texte lisible du tableau structuré éventuel.
    Si un tableau est présent, on ne garde que le titre (pas la liste à rallonge)."""
    if not reponse or _MARQ_DEBUT not in reponse:
        return (reponse, None)
    try:
        debut = reponse.index(_MARQ_DEBUT)
        fin = reponse.index(_MARQ_FIN, debut)
        table = json.loads(reponse[debut + len(_MARQ_DEBUT):fin])
        texte = reponse[:debut].rstrip()
        titre = table.get("titre")
        if titre and texte.startswith(titre):
            texte = titre          # on coupe l'énumération, le tableau la remplace
        return (texte, table)
    except (ValueError, json.JSONDecodeError):
        return (reponse, None)
 
 

VIOLET_VYV = "#472583"
GRIS_VYV = "#878786"
 
 
def _format_valeur(v, unite):
    """Formate une valeur au format français."""
    if unite == "actes":
        return f"{v:,.0f}".replace(",", " ")
    if abs(v) >= 1e9:
        return f"{v/1e9:,.2f} Md€".replace(",", " ").replace(".", ",")
    if abs(v) >= 1e6:
        return f"{v/1e6:,.2f} M€".replace(",", " ").replace(".", ",")
    return f"{v:,.0f} €".replace(",", " ")
 
 
def construire_graphique(table, max_lignes=12):
    """Graphique aux couleurs VYV : barres HORIZONTALES triées (répartitions),
    ou colonnes chronologiques (séries temporelles).
    Renvoie les octets PNG — l'affichage et l'export sont identiques."""
    lignes = table.get("lignes") or []
    if not lignes:
        return None
    colonnes = table.get("colonnes") or ["", ""]
    unite = table.get("unite") or "€"
    titre = table.get("titre") or ""
 
    est_temporel = str(colonnes[0]).lower().startswith("ann")
    donnees = list(lignes) if est_temporel else sorted(lignes, key=lambda r: r[1], reverse=True)
    donnees = donnees[:max_lignes]
    labels = [str(r[0]) for r in donnees]
    valeurs = [float(r[1] or 0) for r in donnees]
 
    hauteur = max(2.4, 0.42 * len(labels) + 1.1)
    fig, ax = plt.subplots(figsize=(8.6, hauteur), dpi=150)
 
    if est_temporel:
        ax.bar(labels, valeurs, color=VIOLET_VYV, width=0.62)
        ax.tick_params(axis="both", labelsize=8, colors=GRIS_VYV)
        for i, v in enumerate(valeurs):
            ax.text(i, v, " " + _format_valeur(v, unite), ha="center", va="bottom",
                    fontsize=7.5, color=GRIS_VYV)
        ax.set_ylim(0, max(valeurs) * 1.16 if valeurs else 1)
        ax.get_yaxis().set_visible(False)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
    else:
        y = list(range(len(labels)))
        ax.barh(y, valeurs, color=VIOLET_VYV, height=0.62)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8.5)
        ax.invert_yaxis()                      # la plus grande valeur EN HAUT
        ax.tick_params(axis="both", labelsize=8, colors=GRIS_VYV)
        for i, v in enumerate(valeurs):
            ax.text(v, i, " " + _format_valeur(v, unite), va="center",
                    fontsize=7.5, color=GRIS_VYV)
        ax.set_xlim(0, max(valeurs) * 1.22 if valeurs else 1)
        ax.get_xaxis().set_visible(False)
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color("#d9d9d9")
 
    if titre:
        ax.set_title(titre, fontsize=10.5, color=VIOLET_VYV, fontweight="bold",
                     loc="left", pad=12)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()
 
 
def afficher_table(table, cle):
    """Affiche un tableau exportable + un graphique exportable."""
    if not table or not table.get("lignes"):
        return
    df = pd.DataFrame(table["lignes"], columns=table["colonnes"])
    unite = table.get("unite") or "€"
    col_val = table["colonnes"][1]
 
    # affichage formaté ; l'export CSV garde les valeurs brutes
    df_aff = df.copy()
    df_aff[col_val] = df_aff[col_val].map(lambda v: _format_valeur(v, unite))
    st.dataframe(df_aff, use_container_width=True, hide_index=True)
 
    voir_graph = st.toggle("📊 Afficher le graphique", key=f"gr_{cle}")
    png = None
    if voir_graph:
        png = construire_graphique(table)
        if png:
            st.image(png, use_container_width=True)
 
    c1, c2 = st.columns(2)
    csv = df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    c1.download_button("⬇️ Données (CSV)", csv, file_name=f"damia_{cle}.csv",
                       mime="text/csv", use_container_width=True, key=f"csv_{cle}")
    if png is None:
        png = construire_graphique(table)          # généré même si non affiché
    if png:
        c2.download_button("🖼️ Graphique (PNG)", png, file_name=f"damia_{cle}.png",
                           mime="image/png", use_container_width=True, key=f"png_{cle}")
 
# ================================================================================
#  INTERFACE STREAMLIT
# ================================================================================

VIOLET = "#5B2D82"
BLEU = "#1E3A6E"
TURQUOISE = "#00857C"

st.set_page_config(page_title="Assistant Open DAMIR — IA souveraine",
                   page_icon="🩺", layout="centered")

st.markdown(f"""
<style>
  .bandeau {{
     background: linear-gradient(90deg, {VIOLET} 0%, {BLEU} 100%);
     padding: 20px 25px; border-radius: 12px; margin-bottom: 15px;
     box-shadow: 0 4px 6px rgba(0,0,0,0.1);
  }}
  .bandeau h1 {{ color:#ffffff; margin:0; font-size:1.4rem; font-weight:600; letter-spacing: 0.5px; }}
  .bandeau p {{ color:#E6E1F0; margin:8px 0 0; font-size:0.95rem; }}
  .pastille {{
     display:inline-block; background:{TURQUOISE}; color:#ffffff;
     padding:4px 12px; border-radius:20px; font-size:0.8rem; font-weight: bold; margin-right:8px;
  }}
</style>
<div class="bandeau">
  <h1>Assistant Open DAMIR — Démonstrateur MCP</h1>
  <p><span class="pastille">Model-Agnostic</span><span class="pastille">Zéro Hallucination Numérique</span>
     Aucune donnée ne quitte le serveur métier. Le LLM orchestre, le code exécute la requête SQL.</p>
</div>
""", unsafe_allow_html=True)


# --- Dictionnaire de correspondance UI <-> Variables de backends.py ---
OPTIONS_MODELES = {
    "Ollama (Local)": "ollama",
    "Google Gemini (API)": "gemini",
    "Anthropic Claude (API)": "api",
    "Transformers (Local GPU)": "transformers",
    "OpenAI-compatible (Groq, Mistral…)": "openai"
}

# Identifier l'index du modèle actuel pour le sélecteur
backend_actuel = os.environ.get("DAMIA_BACKEND", "ollama").lower()
noms_ui = list(OPTIONS_MODELES.keys())
index_defaut = 0
for i, (nom_ui, val_env) in enumerate(OPTIONS_MODELES.items()):
    if val_env == backend_actuel:
        index_defaut = i
        break

# --- Barre latérale : contexte + transparence ---
with st.sidebar:
    st.subheader("Architecture de la démo")
    
    # Sélecteur de modèle interactif
    choix_ui = st.selectbox("🤖 Choisir le moteur d'IA :", noms_ui, index=index_defaut)
    nouveau_backend = OPTIONS_MODELES[choix_ui]

    # Champs supplémentaires pour le connecteur universel (Groq, Mistral, OpenAI…)
    if nouveau_backend == "openai":
        st.caption("Fournisseur compatible OpenAI (Groq, Mistral, OpenRouter…)")
        url = st.text_input("URL de base", value=os.environ.get("DAMIA_BASE_URL", "https://api.groq.com/openai/v1"))
        cle = st.text_input("Clé API", value=os.environ.get("DAMIA_API_KEY", ""), type="password")
        modele = st.text_input("Modèle", value=os.environ.get("DAMIA_MODELE", "llama-3.3-70b-versatile"))
        os.environ["DAMIA_BASE_URL"] = url
        os.environ["DAMIA_API_KEY"] = cle
        os.environ["DAMIA_MODELE"] = modele

    # Logique de bascule à chaud
    besoin_bascule = (
        os.environ.get("DAMIA_BACKEND", "ollama").lower() != nouveau_backend
        or nouveau_backend == "openai"   # le connecteur peut changer d'URL/modèle sans changer de backend
    )
    if besoin_bascule:
        os.environ["DAMIA_BACKEND"] = nouveau_backend
        if st.button("🔄 Appliquer ce moteur"):
            try:
                from src.backends import get_backend
                get_backend(force_reload=True)
                st.success(f"Bascule réussie sur {choix_ui} !")
            except Exception as e:
                st.error(f"Erreur lors du chargement : {e}")

    st.markdown(
        f"- **Protocole** : Standard MCP\n"
        f"- **Données** : Open DAMIR 2022-2025 (DuckDB)\n"
        f"- **Garde-fou** : Dictionnaire métier data-driven"
    )
    st.divider()
    st.caption("Le panneau « 🛠️ Détail technique » sous chaque réponse prouve que le chiffre vient de la base de données, et non des poids du modèle.")

# --- Questions de démo (mise à jour avec Top N et Glossaire) ---
QUESTIONS_DEMO = [
    "Quel est le montant remboursé par l'AM en 2023 ?",
    "Quels sont les 5 postes qui coûtent le plus cher en 2023 ?",
    "Que veut dire C2S ?",
    "Compare les dépenses dentaires entre 2022 et 2024",
    "Combien a coûté la chirurgie esthétique ?",
]

# --- Questions de démo (masquées par défaut pour épurer l'UI) ---
question_cliquee = None
with st.expander("💡 Suggestions de questions pour la démonstration", expanded=False):
    cols = st.columns(2)
    for i, q in enumerate(QUESTIONS_DEMO):
        if cols[i % 2].button(q, key=f"demo_{i}", use_container_width=True):
            question_cliquee = q

# --- Écran d'accueil (affiché uniquement si le chat est vide) ---
if "messages" not in st.session_state or len(st.session_state.messages) == 0:
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("Périmètre des données embarquées")
    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.metric(label="Période couverte", value="2022 - 2025")
    kpi2.metric(label="Volume de faits", value="~11,8 Millions", delta="Agréments CNAM")
    kpi3.metric(label="Prestations classées", value="1 569", delta="Dictionnaire actif")
    st.markdown("<br><hr>", unsafe_allow_html=True)
    
# --- Historique de conversation ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for i, msg in enumerate(st.session_state.messages):
    avatar = "👤" if msg["role"] == "user" else "⚕️"
    with st.chat_message(msg["role"], avatar=avatar):
        texte, table = extraire_table(msg["content"])
        st.markdown(texte)
        if table:
            afficher_table(table, cle=f"hist_{i}")
        if msg.get("detail"):
            with st.expander("Détail technique"):
                d = msg["detail"]
                if d.get("outil"):
                    st.markdown(f"**Outil appelé :** `{d['outil']}`")
                if d.get("parametres"):
                    st.markdown("**Paramètres :**")
                    st.json(d["parametres"])
                if d.get("hors_perimetre"):
                    st.info("Demande hors périmètre — refus volontaire, aucune valeur inventée.")

def _traiter(question):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)
    with st.chat_message("assistant", avatar="⚕️"):
        with st.spinner("Le modèle choisit un outil, le code interroge la base…"):
            res = interroger(question)
        texte, table = extraire_table(res["reponse"])
        st.markdown(texte)
        if table:
            afficher_table(table, cle=f"live_{len(st.session_state.messages)}")
        if res.get("outil") or res.get("hors_perimetre"):
            with st.expander("Détail technique"):
                if res.get("outil"):
                    st.markdown(f"**Outil appelé :** `{res['outil']}`")
                if res.get("parametres"):
                    st.markdown("**Paramètres :**")
                    st.json(res["parametres"])
                if res.get("hors_perimetre"):
                    st.info("Demande hors périmètre — refus volontaire, aucune valeur inventée.")
    st.session_state.messages.append(
        {"role": "assistant", "content": res["reponse"], "detail": res})

# --- Entrées : bouton de démo OU champ de saisie ---
question_saisie = st.chat_input("Posez votre question sur les données Open DAMIR…")

if question_cliquee:
    _traiter(question_cliquee)
elif question_saisie:
    _traiter(question_saisie)