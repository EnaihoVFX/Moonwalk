# Moonwalk Cloud Orchestrator — Dockerfile for Google Cloud Run
# This image contains ONLY the "Brain" (LLM logic + cloud tools).
# macOS tools (osascript, pyobjc, etc.) are NOT included.

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cloud-only subset)
COPY backend/requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt

# Copy the modular backend packages
COPY backend/__init__.py .
COPY backend/agent/ agent/
COPY backend/providers/ providers/
COPY backend/tools/ tools/
COPY backend/multi_agent/ multi_agent/
COPY backend/servers/cloud_server.py .
COPY backend/.env .

# Cloud Run uses PORT env var (default 8080)
ENV PORT=8080

EXPOSE 8080

CMD ["python", "cloud_server.py"]
