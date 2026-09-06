"""
tests/test_registre.py — Tests du registre MCP multi-sources.

Aucun serveur n'est lance : on injecte de faux clients. Ce qui est teste ici,
c'est la LOGIQUE DE ROUTAGE entre sources (qualification des noms, resolution,
ambiguite, panne partielle) — pas le transport, deja couvert par
tests/test_client.py contre un vrai serveur.

    python -m pytest tests/test_registre.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import mcp_client
from src.mcp_client import RegistreMCP, _lire_sources


class FauxOutil:
    def __init__(self, name, description="desc", schema=None):
        self.name = name
        self.description = description
        self.inputSchema = schema or {"properties": {"annee": {}}}


class FauxClient:
    """Client MCP factice : enregistre les appels au lieu de les emettre."""

    def __init__(self, noms, panne=False):
        if panne:
            raise RuntimeError("serveur injoignable")
        self._outils = [FauxOutil(n) for n in noms]
        self.appels = []

    def list_tools(self):
        return self._outils

    def call_tool(self, nom, params=None):
        self.appels.append((nom, params))
        return f"resultat:{nom}"


def construire(monkeypatch, sources_outils, pannes=()):
    """Construit un RegistreMCP peuple de FauxClient.

    sources_outils : {nom_source: [noms d'outils]}. Le nom de source sert aussi
    d'URL — le transport n'est pas ce qu'on teste ici."""
    def fabrique(url):
        return FauxClient(sources_outils[url], panne=url in pannes)
    monkeypatch.setattr(mcp_client, "MCPClient", fabrique)
    return RegistreMCP(sources={nom: nom for nom in sources_outils})


# ---------------------------------------------------------------------------
class TestLectureSources:
    def test_source_unique_par_defaut(self, monkeypatch):
        monkeypatch.delenv("DAMIA_MCP_SOURCES", raising=False)
        monkeypatch.setenv("DAMIA_MCP_URL", "http://x/mcp")
        assert _lire_sources() == {mcp_client.SOURCE_DEFAUT: "http://x/mcp"}

    def test_plusieurs_sources(self, monkeypatch):
        monkeypatch.setenv("DAMIA_MCP_SOURCES",
                           "damir=http://a/mcp, medic=http://b/mcp")
        assert _lire_sources() == {"damir": "http://a/mcp", "medic": "http://b/mcp"}

    def test_url_seule_prend_le_nom_par_defaut(self, monkeypatch):
        monkeypatch.setenv("DAMIA_MCP_SOURCES", "http://seul/mcp")
        assert _lire_sources() == {mcp_client.SOURCE_DEFAUT: "http://seul/mcp"}

    def test_nom_de_source_avec_separateur_refuse(self, monkeypatch):
        monkeypatch.setenv("DAMIA_MCP_SOURCES", "a.b=http://x/mcp")
        with pytest.raises(ValueError, match="invalide"):
            _lire_sources()


# ---------------------------------------------------------------------------
class TestQualification:
    def test_les_noms_sont_prefixes(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses", "top_postes"]})
        assert r.noms_valides() == {"damir.query_depenses", "damir.top_postes"}

    def test_outil_qualifie_preserve_la_surface_mcp(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"]})
        outil = r.list_tools()[0]
        # L'orchestrateur lit .name / .description / .inputSchema sans rien savoir
        # du registre : la qualification doit etre transparente pour lui.
        assert outil.name == "damir.query_depenses"
        assert outil.nom_court == "query_depenses" and outil.source == "damir"
        assert outil.description and "properties" in outil.inputSchema

    def test_collision_entre_sources(self, monkeypatch):
        """Le cas qui motive tout le namespacing : deux sources, meme outil."""
        r = construire(monkeypatch, {"damir": ["query_depenses"],
                                     "medic": ["query_depenses"]})
        assert r.noms_valides() == {"damir.query_depenses", "medic.query_depenses"}


# ---------------------------------------------------------------------------
class TestResolution:
    def test_nom_qualifie(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"]})
        assert r.resoudre("damir.query_depenses") == ("damir", "query_depenses")

    def test_nom_court_tolere_si_non_ambigu(self, monkeypatch):
        """Le modele omet parfois le prefixe : on accepte tant que c'est decidable."""
        r = construire(monkeypatch, {"damir": ["query_depenses"]})
        assert r.resoudre("query_depenses") == ("damir", "query_depenses")

    def test_nom_court_ambigu_est_refuse(self, monkeypatch):
        """Refuser vaut mieux que choisir une source au hasard : le chiffre
        renvoye serait juste, mais issu de la mauvaise base."""
        r = construire(monkeypatch, {"damir": ["query_depenses"],
                                     "medic": ["query_depenses"]})
        with pytest.raises(KeyError, match="plusieurs sources"):
            r.resoudre("query_depenses")

    def test_outil_inconnu(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"]})
        with pytest.raises(KeyError, match="introuvable"):
            r.resoudre("outil_invente")

    def test_source_inconnue_dans_un_nom_qualifie(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"]})
        with pytest.raises(KeyError):
            r.resoudre("medic.query_depenses")


# ---------------------------------------------------------------------------
class TestDispatch:
    def test_appel_route_vers_la_bonne_source(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"],
                                     "medic": ["query_depenses"]})
        r.call_tool("medic.query_depenses", {"annee": 2023})
        assert r.clients["damir"].appels == []
        assert r.clients["medic"].appels == [("query_depenses", {"annee": 2023})]

    def test_le_nom_court_est_transmis_au_serveur(self, monkeypatch):
        """Le prefixe est une convention du client : le serveur ne le connait pas."""
        r = construire(monkeypatch, {"damir": ["top_postes"]})
        r.call_tool("damir.top_postes", {})
        assert r.clients["damir"].appels[0][0] == "top_postes"

    def test_trouver_renvoie_le_nom_qualifie(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["get_dictionnaire"]})
        assert r.trouver("get_dictionnaire") == "damir.get_dictionnaire"
        assert r.trouver("inexistant") is None


# ---------------------------------------------------------------------------
class TestPannePartielle:
    def test_une_source_morte_n_empeche_pas_les_autres(self, monkeypatch):
        r = construire(monkeypatch, {"damir": ["query_depenses"],
                                     "medic": ["query_depenses"]},
                       pannes=("medic",))
        assert list(r.clients) == ["damir"]
        assert "medic" in r.erreurs
        assert r.noms_valides() == {"damir.query_depenses"}

    def test_la_collision_disparait_si_la_source_est_morte(self, monkeypatch):
        """Consequence utile : le nom court redevient resolvable."""
        r = construire(monkeypatch, {"damir": ["query_depenses"],
                                     "medic": ["query_depenses"]},
                       pannes=("medic",))
        assert r.resoudre("query_depenses") == ("damir", "query_depenses")

    def test_aucune_source_joignable_leve(self, monkeypatch):
        with pytest.raises(RuntimeError, match="Aucune source MCP joignable"):
            construire(monkeypatch, {"damir": ["x"]}, pannes=("damir",))


class TestSessionZombie:
    """Non-regression : un serveur injoignable ne levait PAS d'exception
    exploitable (anyio annule la tache par une CancelledError, qui n'herite pas
    d'Exception). On obtenait un client « connecte » sans aucun outil, qui
    echouait seulement a la premiere question posee.

    Le garde-fou verifie le RESULTAT (des outils ont-ils ete decouverts ?) et non
    le chemin d'erreur : il couvre donc tous les modes d'echec, connus ou non."""

    def test_client_sans_outils_est_refuse(self, monkeypatch):
        from src.mcp_client import MCPClient

        async def connexion_muette(self):
            self._pret.set()      # « pret », mais _tools reste vide

        monkeypatch.setattr(MCPClient, "_connecter", connexion_muette)
        with pytest.raises(RuntimeError, match="Aucun outil exposé"):
            MCPClient("http://127.0.0.1:1/mcp")

    def test_le_message_oriente_vers_la_cause_probable(self, monkeypatch):
        from src.mcp_client import MCPClient

        async def connexion_muette(self):
            self._pret.set()

        monkeypatch.setattr(MCPClient, "_connecter", connexion_muette)
        with pytest.raises(RuntimeError) as info:
            MCPClient("http://127.0.0.1:1/sse")
        assert "/mcp" in str(info.value)   # l'erreur historique la plus frequente
