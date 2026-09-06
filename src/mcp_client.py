"""
src/mcp_client.py — Client MCP de DAMIA (transport streamable-http).

Se connecte au serveur MCP (local ou distant) et decouvre / appelle ses outils.
Le serveur (src/mcp_server.py) tourne en `streamable-http` : l'endpoint est
**/mcp**, et non /sse (l'ancien transport SSE n'est plus utilise).

L'URL est pilotee par DAMIA_MCP_URL (bascule local <-> Space HF sans toucher au
code) ; MCP_CLIENT_URL reste accepte pour compatibilite avec les anciens scripts :

    # Local (defaut) : serveur lance via  python src/mcp_server.py
    #   -> http://127.0.0.1:7860/mcp
    # Space Hugging Face
    #   PowerShell : $env:DAMIA_MCP_URL = "https://azoulim-damia-mcp.hf.space/mcp"
    #   bash       : export DAMIA_MCP_URL="https://azoulim-damia-mcp.hf.space/mcp"

MULTI-SOURCES
-------------
Le protocole MCP n'a d'interet que si plusieurs sources peuvent etre interrogees
par la meme voie. `RegistreMCP` tient N connexions et presente leurs outils sous
un nom QUALIFIE `source.outil` (ex. `damir.query_depenses`). Sans cette
qualification, deux serveurs exposant un `query_depenses` seraient indiscernables
au moment du routage.

Les sources se declarent dans DAMIA_MCP_SOURCES :

    DAMIA_MCP_SOURCES="damir=http://127.0.0.1:7860/mcp,medic=http://127.0.0.1:7861/mcp"

Sans cette variable, on retombe sur la source unique DAMIA_MCP_URL, nommee par
DAMIA_MCP_SOURCE_DEFAUT (« damir » par defaut).

============================================================================
 SECURITE — VERIFICATION TLS
 Derriere le proxy d'entreprise VYV (inspection TLS, certificat auto-signe dans
 la chaine -> CERTIFICATE_VERIFY_FAILED), la verification doit etre desactivee.
 C'est une RUSTINE DE DEMO : elle accepte tout certificat, donc elle est
 vulnerable a une interception. Elle n'est active que si MCP_TLS_VERIFY=0 est
 pose explicitement ; par defaut la verification est ACTIVE.
 A durcir proprement : fournir le certificat racine VYV via SSL_CERT_FILE ou
 installer pip-system-certs.
============================================================================
"""
import asyncio
import logging
import os
import threading

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger(__name__)

URL_DEFAUT = "http://127.0.0.1:7860/mcp"
SOURCE_DEFAUT = os.environ.get("DAMIA_MCP_SOURCE_DEFAUT", "damir")
SEPARATEUR = "."          # damir.query_depenses
_TIMEOUT_CONNEXION = int(os.environ.get("MCP_CONNECT_TIMEOUT", "60"))
_TIMEOUT_APPEL = int(os.environ.get("MCP_CALL_TIMEOUT", "120"))


def _verifier_tls():
    """Verification TLS ACTIVE par defaut. Poser MCP_TLS_VERIFY=0 uniquement
    derriere un proxy d'inspection TLS, en connaissance de cause (note ci-dessus).

    Lue a chaque usage : config/config.py peuple os.environ depuis le .env, et
    rien ne garantit qu'il soit importe avant ce module."""
    return os.environ.get("MCP_TLS_VERIFY", "1") != "0"


def _url_configuree():
    """DAMIA_MCP_URL, sinon MCP_CLIENT_URL (heritage), sinon le defaut local."""
    return (os.environ.get("DAMIA_MCP_URL")
            or os.environ.get("MCP_CLIENT_URL")
            or URL_DEFAUT)


def _factory_httpx(headers=None, timeout=None, auth=None):
    """Réplique la factory MCP par défaut (follow_redirects=True) en pilotant
    la vérification TLS via _verifier_tls()."""
    kwargs = {"follow_redirects": True, "verify": _verifier_tls()}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


class MCPClient:
    """Client MCP synchrone.

    Le protocole MCP est asynchrone, mais Streamlit et FastAPI (en mode `def`)
    appellent depuis du code synchrone. On heberge donc une boucle asyncio dans
    un thread dedie, et on lui soumet les coroutines. La session reste ouverte
    entre deux appels : reconnecter a chaque question couterait un aller-retour
    d'initialisation complet.
    """

    def __init__(self, url=None):
        self.url = url or _url_configuree()
        if not _verifier_tls() and self.url.startswith("https"):
            log.warning("Verification TLS desactivee (MCP_TLS_VERIFY=0) sur %s", self.url)

        self._erreur = None
        self._session = None
        self._tools = []
        self._pret = threading.Event()

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._faire_tourner_boucle,
                                        name="mcp-client-loop", daemon=True)
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._connecter(), self._loop)

        if not self._pret.wait(timeout=_TIMEOUT_CONNEXION):
            raise RuntimeError(
                f"Le serveur MCP n'a pas répondu à temps sur {self.url} "
                f"(délai {_TIMEOUT_CONNEXION}s). Vérifiez qu'il est en ligne "
                f"(local : `python src/mcp_server.py` ; distant : la Space HF est-elle "
                f"'Running' ?) et que l'URL se termine bien par /mcp.")
        if self._erreur:
            raise self._erreur

        # Filet independant de la plomberie asynchrone : un serveur injoignable ne
        # remonte pas toujours d'exception exploitable (anyio annule la tache, et
        # initialize() peut revenir sans rien signaler). On aurait alors une session
        # zombie, presente mais sans outils, qui echouerait a la premiere question.
        # Verifier le RESULTAT plutot que le chemin d'erreur couvre tous les cas.
        if not self._tools:
            raise RuntimeError(
                f"Aucun outil exposé par {self.url} — serveur injoignable, "
                f"endpoint incorrect (attendu : /mcp), ou serveur sans outils.")

    def _faire_tourner_boucle(self):
        asyncio.set_event_loop(self._loop)
        # Sans ce gestionnaire, une erreur de transport survenant apres coup dans
        # la boucle s'imprime en trace brute sur stderr, au milieu de l'interface.
        # On la route vers les logs, ou elle est exploitable.
        self._loop.set_exception_handler(
            lambda boucle, ctx: log.debug("Boucle MCP (%s) : %s", self.url,
                                          ctx.get("message") or ctx.get("exception")))
        self._loop.run_forever()

    async def _connecter(self):
        try:
            await asyncio.wait_for(self._ouvrir_session(), timeout=_TIMEOUT_CONNEXION)
        except asyncio.TimeoutError:
            self._erreur = TimeoutError(
                f"Delai depasse ({_TIMEOUT_CONNEXION}s) a la connexion sur {self.url}.")
        except BaseException as e:
            # BaseException et non Exception : anyio signale un echec de transport
            # par une CancelledError, qui n'herite PAS d'Exception — avec un simple
            # `except Exception`, l'echec passait inapercu et laissait une session
            # zombie. Mais une annulation dans NOTRE boucle de fond n'est pas une
            # annulation de l'appelant : la relayer telle quelle traverserait tous
            # ses `except Exception` et tuerait le processus. On la traduit donc en
            # echec de connexion, qui est ce qu'elle signifie vraiment ici.
            self._erreur = e if isinstance(e, Exception) else RuntimeError(
                f"Connexion à {self.url} interrompue ({type(e).__name__}) — "
                f"serveur injoignable ou endpoint incorrect (attendu : /mcp).")
        finally:
            self._pret.set()

    async def _ouvrir_session(self):
        self._transport = streamablehttp_client(self.url, httpx_client_factory=_factory_httpx)
        read, write, _ = await self._transport.__aenter__()
        self._contexte_session = ClientSession(read, write)
        self._session = await self._contexte_session.__aenter__()
        await self._session.initialize()
        self._tools = (await self._session.list_tools()).tools
        log.info("Connecte a %s — %d outils", self.url, len(self._tools))

    def list_tools(self):
        """Outils découverts via MCP (objets avec .name, .description, .inputSchema)."""
        return self._tools

    def call_tool(self, nom, params=None):
        """Appelle un outil via MCP et renvoie le texte concaténé du résultat."""
        if self._session is None:
            raise RuntimeError(f"Session MCP non établie ({self.url}).")
        futur = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(nom, arguments=params or {}), self._loop)
        res = futur.result(timeout=_TIMEOUT_APPEL)
        return "\n".join(bloc.text for bloc in res.content if hasattr(bloc, "text"))


# ---------------------------------------------------------------------------
# Registre multi-sources
# ---------------------------------------------------------------------------
class OutilQualifie:
    """Outil MCP portant sa source. Expose la meme surface qu'un outil MCP brut
    (`name`, `description`, `inputSchema`) pour que l'orchestrateur n'ait rien a
    savoir du registre — mais `name` est le nom QUALIFIE `source.outil`."""

    __slots__ = ("source", "nom_court", "name", "description", "inputSchema")

    def __init__(self, source, outil):
        self.source = source
        self.nom_court = outil.name
        self.name = f"{source}{SEPARATEUR}{outil.name}"
        self.description = outil.description
        self.inputSchema = outil.inputSchema

    def __repr__(self):
        return f"<OutilQualifie {self.name}>"


def _lire_sources():
    """Analyse DAMIA_MCP_SOURCES, sinon retombe sur la source unique.
    Renvoie {nom_source: url}, en preservant l'ordre de declaration."""
    brut = (os.environ.get("DAMIA_MCP_SOURCES") or "").strip()
    if not brut:
        return {SOURCE_DEFAUT: _url_configuree()}

    sources = {}
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        nom, _, url = morceau.partition("=")
        nom, url = nom.strip(), url.strip()
        if not url:                       # une URL seule : on la nomme par defaut
            nom, url = SOURCE_DEFAUT, nom
        if SEPARATEUR in nom:
            raise ValueError(f"Nom de source invalide « {nom} » : "
                             f"« {SEPARATEUR} » est reserve au nom qualifie.")
        sources[nom] = url
    return sources


class RegistreMCP:
    """Federation de N serveurs MCP derriere une interface unique.

    Tolerant aux pannes partielles : une source injoignable est signalee et mise
    de cote, les autres restent utilisables. Un assistant qui refuse de demarrer
    parce qu'une source sur trois est tombee serait plus fragile que le systeme
    qu'il interroge."""

    def __init__(self, sources=None):
        self.sources = sources or _lire_sources()
        self.clients = {}
        self.erreurs = {}
        for nom, url in self.sources.items():
            try:
                self.clients[nom] = MCPClient(url)
            except Exception as e:
                self.erreurs[nom] = str(e)
                log.error("Source MCP « %s » (%s) injoignable : %s", nom, url, e)

        if not self.clients:
            detail = " ; ".join(f"{n} : {e}" for n, e in self.erreurs.items())
            raise RuntimeError(f"Aucune source MCP joignable. {detail}")

        self._outils = [OutilQualifie(nom, o)
                        for nom, c in self.clients.items() for o in c.list_tools()]
        log.info("Registre MCP : %d source(s), %d outil(s) — %s",
                 len(self.clients), len(self._outils), list(self.clients))

    def list_tools(self):
        """Outils de toutes les sources, avec des noms qualifies `source.outil`."""
        return self._outils

    def noms_valides(self):
        return {o.name for o in self._outils}

    def resoudre(self, nom):
        """Traduit un nom d'outil en (source, nom_court).

        Accepte un nom qualifie (`damir.query_depenses`) ou un nom court
        (`query_depenses`) : le modele omet parfois le prefixe, et une source
        unique n'a pas besoin de le porter. Un nom court ambigu entre plusieurs
        sources est refuse plutot que resolu au hasard."""
        if SEPARATEUR in nom:
            source, _, court = nom.partition(SEPARATEUR)
            if source in self.clients:
                return (source, court)

        correspondances = [o for o in self._outils if o.nom_court == nom]
        if len(correspondances) == 1:
            return (correspondances[0].source, correspondances[0].nom_court)
        if len(correspondances) > 1:
            sources = ", ".join(sorted(o.source for o in correspondances))
            raise KeyError(f"L'outil « {nom} » existe dans plusieurs sources "
                           f"({sources}) : precisez « source{SEPARATEUR}{nom} ».")
        raise KeyError(f"Outil « {nom} » introuvable.")

    def call_tool(self, nom, params=None):
        source, court = self.resoudre(nom)
        return self.clients[source].call_tool(court, params)

    def trouver(self, nom_court):
        """Nom qualifié du premier outil portant ce nom court, ou None.
        Utilisé par les raccourcis de l'orchestrateur (ex. get_dictionnaire)."""
        return next((o.name for o in self._outils if o.nom_court == nom_court), None)


_CLIENT = None
_REGISTRE = None


def get_mcp_client():
    """Client MCP mono-source partagé (compatibilité : scripts et tests directs)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = MCPClient()
    return _CLIENT


def get_registre(force_reload=False):
    """Registre MCP partagé par tout le processus. Point d'entrée de l'orchestrateur."""
    global _REGISTRE
    if _REGISTRE is None or force_reload:
        _REGISTRE = RegistreMCP()
    return _REGISTRE


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    registre = get_registre()
    for nom, url in registre.sources.items():
        etat = "OK" if nom in registre.clients else f"ECHEC ({registre.erreurs[nom]})"
        print(f"  {nom:12} {url:45} {etat}")
    print("\nOutils découverts :")
    for outil in registre.list_tools():
        print(f"  {outil.name}")
