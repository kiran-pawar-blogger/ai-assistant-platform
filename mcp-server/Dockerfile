FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY init_collections.py .

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

# Port 8080 = FastMCP streamable-http (/mcp)
# Port 8081 = sidecar HTTP server (/health, /metrics)
EXPOSE 8080 8081

CMD ["python", "main.py"]
