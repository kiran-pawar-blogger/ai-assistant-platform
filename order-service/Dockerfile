FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Start with Skywalking Python agent (sw-python) wrapping uvicorn.
# Agent config comes from SW_AGENT_* env vars set in the K8s manifest.
# Falls back gracefully if SW OAP is unreachable.
CMD ["sw-python", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
