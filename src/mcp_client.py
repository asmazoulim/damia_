"""
src/mcp_client.py — Client MCP persistant a interface SYNCHRONE.

Role : c'est LUI qui rend la voie 2 reelle. assistant.py ne touche plus a tools.py ;
il decouvre et appelle les outils UNIQUEMENT via le protocole MCP, en parlant a
mcp_server.py via HTTP (Server-Sent Events).
"""
import sys
import asyncio
import threading
import os

from mcp import ClientSession
from mcp.client.sse import sse_client

class MCPClient:
    def __init__(self, url=None):
        # On pointe vers l'URL du serveur déployé (ou localhost par défaut)
        #self._url = url or os.environ.get("MCP_CLIENT_URL", "http://127.0.0.1:7860/mcp")
        self._url = url or os.environ.get("MCP_CLIENT_URL", "http://127.0.0.1:7860/sse")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        self._erreur = None
        self._session = None
        self._tools = []
        asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        
        # On réduit le timeout, si le serveur HTTP n'est pas up, ça plante vite
        if not self._ready.wait(timeout=10):
            raise RuntimeError(f"Le serveur MCP n'a pas répondu à temps sur {self._url}. Vérifiez qu'il est bien lancé dans un autre terminal.")
        if self._erreur:
            raise self._erreur

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        try:
            # Remplacement de stdio_client par sse_client
            self._cm_sse = sse_client(self._url)
            read, write = await self._cm_sse.__aenter__()
            self._cm_session = ClientSession(read, write)
            self._session = await self._cm_session.__aenter__()
            await self._session.initialize()
            res = await self._session.list_tools()
            self._tools = res.tools
        except Exception as e:
            self._erreur = e
        finally:
            self._ready.set()

    def list_tools(self):
        """Renvoie la liste des outils decouverts via MCP (objets avec .name, .description, .inputSchema)."""
        return self._tools

    def call_tool(self, nom, params):
        """Appelle un outil via MCP et renvoie le texte concatene du resultat."""
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(nom, arguments=params or {}), self._loop)
        res = fut.result(timeout=120)
        return "\n".join(b.text for b in res.content if hasattr(b, "text"))


# Singleton : un seul client par processus (cache naturel pour Streamlit)
_CLIENT = None
def get_mcp_client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = MCPClient()
    return _CLIENT


if __name__ == "__main__":
    c = get_mcp_client()
    print("Outils decouverts :", [t.name for t in c.list_tools()])