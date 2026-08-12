--
title: Assistant MCP Open DAMIR
emoji: 💊
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Serveur MCP Open DAMIR (model-agnostic)

Serveur MCP exposant les dépenses de l'Assurance Maladie (Open DAMIR) en HTTP.
N'importe quel client MCP compatible peut s'y connecter avec son propre modèle.

Endpoint : `/mcp` (transport streamable-http).
Aucun modèle embarqué — le client apporte son IA.
