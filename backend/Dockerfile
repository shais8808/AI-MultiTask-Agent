# =============================================================
# Productivity Agent — Backend Dockerfile
# Multi-stage-free, slim image tuned for a FastAPI + SQLite service.
# =============================================================
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr —
# important for real-time `docker logs` visibility of agent run logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (separate layer) so code changes don't
# invalidate the pip install cache on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY app ./app

# Directories for SQLite DB file and log output — created here so the
# container doesn't fail if the host doesn't mount volumes for them.
RUN mkdir -p /app/logs /app/data

# Run as a non-root user — least-privilege principle for container security.
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

# Default DB path inside the container; override via DATABASE_URL env var
# at `docker run` time if mounting a persistent volume elsewhere.
ENV DATABASE_URL=sqlite:////app/data/productivity_agent.db \
    LOG_FILE=/app/logs/agent_runs.log

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
