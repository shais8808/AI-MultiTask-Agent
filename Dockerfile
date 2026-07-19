# =============================================================
# Productivity Agent — Unified Dockerfile for single-container hosting
# (e.g. Hugging Face Spaces' Docker SDK).
#
# Builds the React frontend, then copies the compiled output into the
# FastAPI backend's static-files directory so ONE process serves both
# the UI and the API — required because Spaces runs a single container
# with a single exposed port, and because this app's session/approval
# state is an in-memory singleton that must live in exactly one process
# (see backend/app/services/memory_service.py and approval_service.py).
# =============================================================

# --- Stage 1: build the React frontend ---
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend, serving the built frontend ---
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# The built frontend lands here — app/main.py mounts it if present.
COPY --from=frontend-build /frontend/dist ./app/static_frontend

RUN mkdir -p /app/logs /app/data

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

# Hugging Face Spaces expects the app to listen on port 7860 by default.
ENV DATABASE_URL=sqlite:////app/data/productivity_agent.db \
    LOG_FILE=/app/logs/agent_runs.log \
    PORT=7860

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"7860\")}/health')" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
