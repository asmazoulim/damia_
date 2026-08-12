FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HF Spaces expose le port 7860
ENV MCP_HOST=0.0.0.0 MCP_PORT=7860
EXPOSE 7860

CMD ["python", "src/mcp_server.py"]
