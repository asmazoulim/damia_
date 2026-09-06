# Image de la Space Hugging Face : serveur MCP seul (pas d'interface, pas de LLM).
FROM python:3.11-slim
WORKDIR /app

# Seules les dependances du serveur : image legere, build rapide et reproductible.
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY config/ ./config/
COPY src/ ./src/
COPY data/ ./data/

# HF Spaces expose le port 7860 ; l'endpoint MCP est /mcp (transport streamable-http).
ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=7860 \
    MCP_TRANSPORT=streamable-http \
    PYTHONUNBUFFERED=1
EXPOSE 7860

CMD ["python", "src/mcp_server.py"]
