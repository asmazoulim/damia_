"""
app.py — Interface Streamlit (chat) de la démo MCP DAMIA.

Elle ne contient AUCUNE logique métier : elle appelle src.assistant.poser_question,
sépare le texte du tableau structuré, et rend tableau / graphique / exports.
Le formatage des valeurs vit dans src/formatage.py et les graphiques dans
src/graphiques.py — les mêmes que ceux servis par api.py, pour que les deux
interfaces affichent strictement la même chose.

Lancement :
    streamlit run app.py
"""
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.assistant import poser_question
from src.formatage import extraire_table, format_valeur
from src.graphiques import TYPES as TYPES_GRAPHIQUE, construire_graphique

# ================================================================================
#  Configuration de la page — doit precede tout autre appel Streamlit
# ================================================================================
st.set_page_config(page_title="Assistant Open DAMIR", page_icon="🩺",
                   layout="wide", initial_sidebar_state="collapsed")

VIOLET_VYV = "#472583"

OPTIONS_MODELES = {
    "Ollama (Local)": "ollama",
    "Google Gemini (API)": "gemini",
    "Anthropic Claude (API)": "api",
    "Transformers (Local GPU)": "transformers",
    "OpenAI-compatible (Groq, Mistral…)": "openai",
}

QUESTIONS_DEMO = [
    "Quel est le montant remboursé par l'AM en 2023 ?",
    "Quels sont les 5 postes qui coûtent le plus cher en 2023 ?",
    "Compare les dépenses dentaires entre 2022 et 2024",
    "Répartition du coût de la chirurgie esthétique ?",
]


@st.cache_resource
def _demarrer_moteur():
    """Charge le backend LLM et ouvre la connexion MCP une seule fois.

    @st.cache_resource : Streamlit rejoue le script a chaque interaction ; sans
    ce cache, chaque clic relancerait une connexion MCP et un chargement de modele.
    Renvoie (ok, message_erreur) plutot que de lever : l'interface doit rester
    affichable meme si le serveur MCP est eteint, pour pouvoir le dire."""
    from src.backends import get_backend
    from src.mcp_client import get_registre
    try:
        get_backend()
        registre = get_registre()
        # Une source injoignable n'empeche pas de demarrer : on le signale.
        if registre.erreurs:
            detail = " ; ".join(f"{n} : {e}" for n, e in registre.erreurs.items())
            return True, f"Sources MCP indisponibles — {detail}"
        return True, None
    except Exception as e:
        return False, str(e)


# ================================================================================
#  Rendu d'une reponse
# ================================================================================
def afficher_table(table, cle):
    """Tableau + graphique optionnel + exports CSV/PNG.

    `cle` rend uniques les widgets Streamlit : sans elle, deux réponses d'une
    même conversation se disputeraient le même identifiant."""
    if not table or not table.get("lignes"):
        return

    df = pd.DataFrame(table["lignes"], columns=table["colonnes"])
    unite = table.get("unite") or "€"
    colonne_valeur = table["colonnes"][1]

    # Affichage formate ; l'export CSV conserve les valeurs brutes, exploitables
    # dans un tableur.
    df_affiche = df.copy()
    df_affiche[colonne_valeur] = df_affiche[colonne_valeur].map(
        lambda v: format_valeur(v, unite))
    st.dataframe(df_affiche, use_container_width=True, hide_index=True)

    png = None
    if st.toggle("📊 Générer un graphique", key=f"gr_{cle}"):
        type_graphique = st.radio("Type de visualisation :", list(TYPES_GRAPHIQUE),
                                  horizontal=True, key=f"type_g_{cle}")
        png = construire_graphique(table, type_graphique=type_graphique)
        if png:
            st.image(png, use_container_width=True)

    col_csv, col_png = st.columns(2)
    csv = df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    col_csv.download_button("⬇️ Télécharger les données (CSV)", csv,
                            file_name=f"damia_{cle}.csv", mime="text/csv",
                            use_container_width=True, key=f"csv_{cle}")

    if png is None:            # export possible meme si le graphique n'est pas affiche
        png = construire_graphique(table, type_graphique="Barres")
    if png:
        col_png.download_button("🖼️ Exporter le graphique (PNG)", png,
                                file_name=f"damia_{cle}.png", mime="image/png",
                                use_container_width=True, key=f"png_{cle}")


def afficher_log(detail):
    """Panneau de transparence : quel outil MCP a été appelé, avec quels paramètres.
    C'est la preuve visible que le chiffre vient de la base, pas du modèle."""
    if not (detail.get("outil") or detail.get("hors_perimetre")):
        return
    with st.expander("🛠️ Log d'exécution technique"):
        if detail.get("outil"):
            st.code(f"Appel outil : {detail['outil']}")
        if detail.get("parametres"):
            st.json(detail["parametres"])
        if detail.get("hors_perimetre"):
            st.info("Demande hors périmètre — refus volontaire, aucune valeur inventée.")


def afficher_reponse(contenu, detail, cle):
    texte, table = extraire_table(contenu, couper_enumeration=True)
    st.markdown(texte)
    if table:
        afficher_table(table, cle=cle)
    if detail:
        afficher_log(detail)


# ================================================================================
#  En-tete
# ================================================================================
st.markdown("""
<style>
  /* Sidebar et chrome Streamlit masques : rendu « application » plein cadre */
  [data-testid="collapsedControl"] { display: none; }
  #MainMenu, header[data-testid="stHeader"], footer { visibility: hidden; }
  .block-container { padding: 1.5rem 5rem 6rem 5rem !important; max-width: 1200px; margin: 0 auto; }

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

  .stButton > button, .stDownloadButton > button {
    border-radius: 8px; border: 1px solid #D8D6E6; background: #fff;
    color: #4a4a57; font-size: 14px; font-weight: 500; padding: 10px 16px;
    transition: all .2s ease;
  }
  .stButton > button:hover, .stDownloadButton > button:hover {
    border-color: #472583; color: #472583; background: #f5f3ff;
  }

  [data-testid="stChatMessage"] { background:transparent; padding:8px 0; border: none; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    flex-direction: row-reverse; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
    background: #472583; color: #fff; padding: 14px 20px; border-radius: 16px 16px 4px 16px;
    font-size: 15px; box-shadow: 0px 2px 10px rgba(71, 37, 131, 0.15); }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"] {
    background: #fff; border: 1px solid #ECEBF3; padding: 16px 22px;
    border-radius: 16px 16px 16px 4px; font-size: 15px; box-shadow: 0px 2px 10px rgba(0,0,0,0.02); }

  [data-testid="stChatInput"] { border-radius: 24px; border: 1.5px solid #E7E5F0;
    padding: 4px; box-shadow: 0px 4px 20px rgba(0,0,0,0.05); }
  [data-testid="stChatInput"]:focus-within { border-color: #472583; }
</style>
<div class="damia-top">
  <div class="damia-id">
    <div class="damia-logo">DA</div>
    <div>
      <div class="damia-name">Open DAMIR Assistant</div>
      <div class="damia-desc">Le modèle choisit l'outil · le code exécute la requête DuckDB · aucun chiffre inventé</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

_moteur_ok, _erreur_moteur = _demarrer_moteur()
if not _moteur_ok:
    st.error(f"Moteur indisponible : {_erreur_moteur}\n\n"
             f"Vérifiez que le serveur MCP tourne (`python src/mcp_server.py`) "
             f"et que DAMIA_MCP_URL pointe sur le bon endpoint (/mcp).")
elif _erreur_moteur:
    st.warning(_erreur_moteur)   # demarrage partiel : au moins une source est tombee

# ================================================================================
#  Configuration du moteur d'IA
# ================================================================================
with st.expander("⚙️ Configuration du moteur d'IA", expanded=False):
    backend_actuel = os.environ.get("DAMIA_BACKEND", "ollama").lower()
    index_defaut = next((i for i, v in enumerate(OPTIONS_MODELES.values())
                         if v == backend_actuel), 0)

    col_gauche, col_droite = st.columns(2)
    choix_ui = col_gauche.selectbox("🤖 Choisir le moteur d'IA :",
                                    list(OPTIONS_MODELES), index=index_defaut)
    nouveau_backend = OPTIONS_MODELES[choix_ui]

    if nouveau_backend == "openai":
        os.environ["DAMIA_BASE_URL"] = col_gauche.text_input(
            "URL de base", value=os.environ.get("DAMIA_BASE_URL",
                                                "https://api.groq.com/openai/v1"))
        os.environ["DAMIA_API_KEY"] = col_droite.text_input(
            "Clé API", value=os.environ.get("DAMIA_API_KEY", ""), type="password")
        os.environ["DAMIA_MODELE"] = col_droite.text_input(
            "Modèle", value=os.environ.get("DAMIA_MODELE", "openai/gpt-oss-120b"))

    # Le connecteur OpenAI-compatible peut changer d'URL ou de modele sans changer
    # de backend : on propose donc toujours de reappliquer dans ce cas.
    if backend_actuel != nouveau_backend or nouveau_backend == "openai":
        os.environ["DAMIA_BACKEND"] = nouveau_backend
        if col_droite.button("🔄 Appliquer ce moteur"):
            try:
                from src.backends import get_backend
                get_backend(force_reload=True)
                st.success(f"Moteur {choix_ui} activé.")
            except Exception as e:
                st.error(f"Erreur technique : {e}")

# ================================================================================
#  Conversation
# ================================================================================
if "messages" not in st.session_state:
    st.session_state.messages = []

question_cliquee = None
if not st.session_state.messages:
    st.markdown("### Suggestions de requêtes")
    for colonne, question in zip(st.columns(len(QUESTIONS_DEMO)), QUESTIONS_DEMO):
        if colonne.button(question, use_container_width=True):
            question_cliquee = question
    st.markdown("<br><br>", unsafe_allow_html=True)

for i, message in enumerate(st.session_state.messages):
    avatar = "👤" if message["role"] == "user" else "✨"
    with st.chat_message(message["role"], avatar=avatar):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            afficher_reponse(message["content"], message.get("detail"), cle=f"hist_{i}")


def traiter(question):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user", avatar="👤"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="✨"):
        with st.spinner("Analyse et requête SQL en cours d'exécution..."):
            try:
                res = poser_question(question)
            except Exception as e:
                res = {"reponse": f"Une erreur est survenue : {e}",
                       "outil": None, "parametres": None}
        afficher_reponse(res["reponse"], res, cle=f"live_{len(st.session_state.messages)}")

    st.session_state.messages.append(
        {"role": "assistant", "content": res["reponse"], "detail": res})


question_saisie = st.chat_input("Posez votre question en langage naturel...")
if question_cliquee:
    traiter(question_cliquee)
elif question_saisie:
    traiter(question_saisie)
