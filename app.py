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
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = e

    def _fonction_reponse(question):
        return ("[MODE DEMO UI — moteur non branché]\n"
                f"Question reçue : {question}")

@st.cache_resource
def _init():
    from src.mcp_client import get_mcp_client
    from src.backends import get_backend
    get_backend()
    get_mcp_client()
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
    if not reponse or _MARQ_DEBUT not in reponse:
        return (reponse, None)
    try:
        debut = reponse.index(_MARQ_DEBUT)
        fin = reponse.index(_MARQ_FIN, debut)
        table = json.loads(reponse[debut + len(_MARQ_DEBUT):fin])
        texte = reponse[:debut].rstrip()
        titre = table.get("titre")
        if titre and texte.startswith(titre):
            texte = titre
        return (texte, table)
    except (ValueError, json.JSONDecodeError):
        return (reponse, None)

VIOLET_VYV = "#472583"
GRIS_VYV = "#878786"
COULEURS_PALETTE = ["#472583", "#6342A6", "#8365CC", "#A58BF0", "#C8B5FF", "#EBE0FF"]

def _format_valeur(v, unite):
    if unite == "actes":
        return f"{v:,.0f}".replace(",", " ")
    if abs(v) >= 1e9:
        return f"{v/1e9:,.2f} Md€".replace(",", " ").replace(".", ",")
    if abs(v) >= 1e6:
        return f"{v/1e6:,.2f} M€".replace(",", " ").replace(".", ",")
    return f"{v:,.0f} €".replace(",", " ")

def construire_graphique(table, type_graphique="Barres", max_lignes=12):
    """Génère un graphique selon le choix de l'utilisateur (Barres, Lignes, Camembert)"""
    lignes = table.get("lignes") or []
    if not lignes:
        return None
    colonnes = table.get("colonnes") or ["", ""]
    unite = table.get("unite") or "€"
    titre = table.get("titre") or ""

    est_temporel = str(colonnes[0]).lower().startswith("ann")
    
    # Tri des données
    donnees = list(lignes) if est_temporel else sorted(lignes, key=lambda r: r[1], reverse=True)
    donnees = donnees[:max_lignes]
    labels = [str(r[0]) for r in donnees]
    valeurs = [float(r[1] or 0) for r in donnees]

    hauteur = max(3.0, 0.45 * len(labels) + 1.5)
    fig, ax = plt.subplots(figsize=(9, hauteur), dpi=150)

    # 1. Mode Ligne (Évolutions)
    if type_graphique == "Lignes":
        ax.plot(labels, valeurs, color=VIOLET_VYV, marker='o', linewidth=2.5, markersize=8)
        ax.fill_between(labels, valeurs, alpha=0.1, color=VIOLET_VYV)
        ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
        for i, v in enumerate(valeurs):
            ax.text(i, v * 1.05, _format_valeur(v, unite), ha="center", va="bottom", fontsize=8.5, color=VIOLET_VYV, fontweight="bold")
        ax.set_ylim(0, max(valeurs) * 1.25 if valeurs else 1)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#d9d9d9")
        ax.spines["left"].set_color("#d9d9d9")

    # 2. Mode Camembert (Répartitions)
    elif type_graphique == "Camembert":
        wedges, texts, autotexts = ax.pie(
            valeurs, labels=labels, autopct='%1.1f%%', startangle=90, 
            colors=COULEURS_PALETTE[:len(valeurs)],
            textprops=dict(color=GRIS_VYV, fontsize=9)
        )
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
        ax.axis('equal')  # Assure un cercle parfait

    # 3. Mode Barres (Défaut)
    else:
        if est_temporel:
            ax.bar(labels, valeurs, color=VIOLET_VYV, width=0.5)
            ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
            for i, v in enumerate(valeurs):
                ax.text(i, v, " " + _format_valeur(v, unite), ha="center", va="bottom", fontsize=8.5, color=GRIS_VYV)
            ax.set_ylim(0, max(valeurs) * 1.2 if valeurs else 1)
            ax.get_yaxis().set_visible(False)
            for s in ("top", "right", "left"):
                ax.spines[s].set_visible(False)
        else:
            y = list(range(len(labels)))
            ax.barh(y, valeurs, color=VIOLET_VYV, height=0.55)
            ax.set_yticks(y)
            ax.set_yticklabels(labels, fontsize=9)
            ax.invert_yaxis()
            ax.tick_params(axis="both", labelsize=9, colors=GRIS_VYV)
            for i, v in enumerate(valeurs):
                ax.text(v, i, " " + _format_valeur(v, unite), va="center", fontsize=8.5, color=GRIS_VYV)
            ax.set_xlim(0, max(valeurs) * 1.3 if valeurs else 1)
            ax.get_xaxis().set_visible(False)
            for s in ("top", "right", "bottom"):
                ax.spines[s].set_visible(False)
            ax.spines["left"].set_color("#d9d9d9")

    if titre:
        ax.set_title(titre, fontsize=12, color=VIOLET_VYV, fontweight="bold", loc="center", pad=20)
    
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()

def afficher_table(table, cle):
    if not table or not table.get("lignes"):
        return
    df = pd.DataFrame(table["lignes"], columns=table["colonnes"])
    unite = table.get("unite") or "€"
    col_val = table["colonnes"][1]

    df_aff = df.copy()
    df_aff[col_val] = df_aff[col_val].map(lambda v: _format_valeur(v, unite))
    st.dataframe(df_aff, use_container_width=True, hide_index=True)

    voir_graph = st.toggle("📊 Générer un graphique", key=f"gr_{cle}")
    png = None
    if voir_graph:
        # Sélecteur dynamique de type de graphique
        type_g = st.radio("Type de visualisation :", ["Barres", "Lignes", "Camembert"], horizontal=True, key=f"type_g_{cle}")
        png = construire_graphique(table, type_graphique=type_g)
        if png:
            st.image(png, use_container_width=True)

    c1, c2 = st.columns(2)
    csv = df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    c1.download_button("⬇️ Télécharger les données (CSV)", csv, file_name=f"damia_{cle}.csv", mime="text/csv", use_container_width=True, key=f"csv_{cle}")
    if png is None:
        png = construire_graphique(table, type_graphique="Barres")
    if png:
        c2.download_button("🖼️ Exporter le graphique (PNG)", png, file_name=f"damia_{cle}.png", mime="image/png", use_container_width=True, key=f"png_{cle}")

# ================================================================================
#  INTERFACE UX : Mode "Full App" sans bords
# ================================================================================

# On force layout="wide" pour casser l'effet bloc étroit
st.set_page_config(page_title="Assistant Open DAMIR", page_icon="🩺", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
  /* Disparition totale de la sidebar et des paddings par défaut */
  [data-testid="collapsedControl"] { display: none; }
  #MainMenu, header[data-testid="stHeader"], footer { visibility: hidden; }
  .block-container { padding: 1.5rem 5rem 6rem 5rem !important; max-width: 1200px; margin: 0 auto; }

  /* En-tête de marque style Databricks/SaaS */
  .damia-top {
    display:flex; align-items:center; justify-content:space-between; gap:16px;
    background: linear-gradient(135deg, #ffffff, #f9f8ff);
    border: 1px solid #E7E5F0; border-radius: 12px;
    padding: 20px 24px; margin-bottom: 30px;
    box-shadow: 0px 4px 15px rgba(0,0,0,0.02);
  }
  .damia-id { display:flex; align-items:center; gap:16px; }
  .damia-logo { width:48px; height:48px; border-radius:12px; background:#472583;
    display:flex; align-items:center; justify-content:center;
    color:#fff; font-weight:700; font-size:18px; flex-shrink:0; }
  .damia-name { font-size:20px; font-weight:700; color:#1f2330; line-height:1.2; }
  .damia-desc { font-size:13px; color:#6b6b78; line-height:1.4; margin-top:4px; }
  
  /* Boutons fluides */
  .stButton > button, .stDownloadButton > button {
    border-radius: 8px; border: 1px solid #D8D6E6; background: #fff;
    color: #4a4a57; font-size: 14px; font-weight: 500; padding: 10px 16px;
    transition: all .2s ease;
  }
  .stButton > button:hover, .stDownloadButton > button:hover {
    border-color: #472583; color: #472583; background: #f5f3ff;
  }

  /* Chat ergonomique */
  [data-testid="stChatMessage"] { background:transparent; padding:8px 0; border: none; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    flex-direction: row-reverse; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
    background: #472583; color: #fff; padding: 14px 20px; border-radius: 16px 16px 4px 16px; font-size: 15px; box-shadow: 0px 2px 10px rgba(71, 37, 131, 0.15);}
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"] {
    background: #fff; border: 1px solid #ECEBF3; padding: 16px 22px;
    border-radius: 16px 16px 16px 4px; font-size: 15px; box-shadow: 0px 2px 10px rgba(0,0,0,0.02);}

  [data-testid="stChatInput"] { border-radius: 24px; border: 1.5px solid #E7E5F0; padding: 4px; box-shadow: 0px 4px 20px rgba(0,0,0,0.05); }
  [data-testid="stChatInput"]:focus-within { border-color: #472583; }
</style>
<div class="damia-top">
  <div class="damia-id">
    <div class="damia-logo">DA</div>
    <div>
      <div class="damia-name">Open DAMIR Assistant</div>
      <div class="damia-desc">Outil d'intelligence analytique propulsé par LLM • Génération dynamique SQL & Dataviz</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# --- Configuration Technique relocalisée dans un expander (Plus de Sidebar !) ---
with st.expander("⚙️ Configuration du moteur d'IA", expanded=False):
    OPTIONS_MODELES = {
        "Ollama (Local)": "ollama",
        "Google Gemini (API)": "gemini",
        "Anthropic Claude (API)": "api",
        "Transformers (Local GPU)": "transformers",
        "OpenAI-compatible (Groq, Mistral…)": "openai"
    }

    backend_actuel = os.environ.get("DAMIA_BACKEND", "ollama").lower()
    noms_ui = list(OPTIONS_MODELES.keys())
    index_defaut = next((i for i, v in enumerate(OPTIONS_MODELES.values()) if v == backend_actuel), 0)

    col_conf1, col_conf2 = st.columns(2)
    choix_ui = col_conf1.selectbox("🤖 Choisir le moteur d'IA :", noms_ui, index=index_defaut)
    nouveau_backend = OPTIONS_MODELES[choix_ui]

    if nouveau_backend == "openai":
        url = col_conf1.text_input("URL de base", value=os.environ.get("DAMIA_BASE_URL", "https://api.groq.com/openai/v1"))
        cle = col_conf2.text_input("Clé API", value=os.environ.get("DAMIA_API_KEY", ""), type="password")
        modele = col_conf2.text_input("Modèle", value=os.environ.get("DAMIA_MODELE", "llama-3.3-70b-versatile"))
        os.environ["DAMIA_BASE_URL"] = url
        os.environ["DAMIA_API_KEY"] = cle
        os.environ["DAMIA_MODELE"] = modele

    besoin_bascule = (os.environ.get("DAMIA_BACKEND", "ollama").lower() != nouveau_backend or nouveau_backend == "openai")
    if besoin_bascule:
        os.environ["DAMIA_BACKEND"] = nouveau_backend
        if col_conf2.button("🔄 Appliquer ce moteur"):
            try:
                from src.backends import get_backend
                get_backend(force_reload=True)
                st.success(f"Moteur {choix_ui} activé avec succès.")
            except Exception as e:
                st.error(f"Erreur technique : {e}")

QUESTIONS_DEMO = [
    "Quel est le montant remboursé par l'AM en 2023 ?",
    "Quels sont les 5 postes qui coûtent le plus cher en 2023 ?",
    "Compare les dépenses dentaires entre 2022 et 2024",
    "Répartition du coût de la chirurgie esthétique ?"
]

question_cliquee = None

# --- Écran d'accueil dynamique ---
if "messages" not in st.session_state or len(st.session_state.messages) == 0:
    st.markdown("### Suggestions de requêtes")
    c1, c2, c3, c4 = st.columns(4)
    cols = [c1, c2, c3, c4]
    for i, q in enumerate(QUESTIONS_DEMO):
        if cols[i].button(q, use_container_width=True):
            question_cliquee = q
    st.markdown("<br><br>", unsafe_allow_html=True)

# --- Historique de conversation ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for i, msg in enumerate(st.session_state.messages):
    avatar = "👤" if msg["role"] == "user" else "✨"
    with st.chat_message(msg["role"], avatar=avatar):
        texte, table = extraire_table(msg["content"])
        st.markdown(texte)
        if table:
            afficher_table(table, cle=f"hist_{i}")
        if msg.get("detail"):
            with st.expander("🛠️ Log d'exécution technique"):
                d = msg["detail"]
                if d.get("outil"):
                    st.code(f"Appel outil : {d['outil']}")
                if d.get("parametres"):
                    st.json(d["parametres"])

def _traiter(question):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)
    with st.chat_message("assistant", avatar="✨"):
        with st.spinner("Analyse et requête SQL en cours d'exécution..."):
            res = interroger(question)
        texte, table = extraire_table(res["reponse"])
        st.markdown(texte)
        if table:
            afficher_table(table, cle=f"live_{len(st.session_state.messages)}")
        if res.get("outil") or res.get("hors_perimetre"):
            with st.expander("🛠️ Log d'exécution technique"):
                if res.get("outil"):
                    st.code(f"Appel outil : {res['outil']}")
                if res.get("parametres"):
                    st.json(res["parametres"])
    st.session_state.messages.append({"role": "assistant", "content": res["reponse"], "detail": res})

question_saisie = st.chat_input("Posez votre question en langage naturel...")

if question_cliquee:
    _traiter(question_cliquee)
elif question_saisie:
    _traiter(question_saisie)