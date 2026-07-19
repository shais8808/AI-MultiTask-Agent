"""
main.py
=======

Why this file exists
---------------------
This is the single entrypoint for the FastAPI application. It is
responsible for:

1. Wiring together configuration (`config.py`), database initialization
   (`database/connection.py`), logging setup, and CORS — everything the
   app needs before it can serve a single request.
2. Failing fast at startup if critical configuration is missing (e.g. no
   Gemini API key), rather than letting the agent crash confusingly on
   the first user message.
3. Registering all API routers (chat, tasks, notes, approvals, logs) —
   added incrementally as later phases build them out. Phase 2 only wires
   up the skeleton + a health check route so the server is runnable and
   testable before agent/tool logic exists.

How it interacts with the rest of the system
-----------------------------------------------
- Reads `Settings` from `config.py`.
- Calls `init_db()` from `database/connection.py` at startup to ensure
  tables exist.
- Will import and include routers from `agent/` (chat endpoint) and future
  `routes/` modules for tasks/notes/approvals/logs (Phase 6).
- Configures Python's root logger so every module's `logging.getLogger(__name__)`
  calls produce consistently formatted output, written to both console and
  `settings.log_file`.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database.connection import init_db

settings = get_settings()


def _configure_logging() -> None:
    """
    Configure the root logger once, at process startup.

    All modules across the app use `logging.getLogger(__name__)`, which
    inherits this configuration. Logs go to both stdout (for `docker logs` /
    local dev visibility) and a rotating file for later inspection.

    CRITICAL: this configuration must never log secret values (API keys).
    Individual loggers are responsible for not passing secrets into log
    calls — see `run_logger.py` for the enforcement point specific to
    agent execution logs.
    """
    log_dir = os.path.dirname(settings.log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(settings.log_file, encoding="utf-8"),
        ],
    )


_configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler — runs once at startup and once at shutdown.

    Startup responsibilities:
    - Validate that GEMINI_API_KEY is configured. If not, we log a clear
      CRITICAL error and refuse to start, rather than starting a server
      that will fail on every agent request (ERROR HANDLING: 'Missing API key').
    - Initialize the database schema.

    Shutdown responsibilities:
    - Log a clean shutdown message. (Connection pool disposal is handled
      automatically by SQLAlchemy's engine garbage collection; explicit
      `engine.dispose()` is called here for correctness.)
    """
    logger.info("Starting Productivity Agent backend...")

    if not settings.is_llm_configured():
        logger.critical(
            "LLM_PROVIDER=%s is selected but its required API key is not set. "
            "Copy .env.example to .env and provide a valid key for that provider.",
            settings.llm_provider,
        )
        raise RuntimeError(
            f"Missing required configuration for llm_provider={settings.llm_provider!r}. "
            "See .env.example for setup instructions."
        )

    try:
        init_db()
    except Exception:
        logger.critical("Database initialization failed at startup.", exc_info=True)
        raise

    logger.info("Startup complete. Provider=%s | Model=%s | MaxSteps=%s | MaxRetries=%s",
                settings.llm_provider, settings.active_model_name, settings.max_agent_steps, settings.max_retries)

    yield

    logger.info("Shutting down Productivity Agent backend...")
    from app.database.connection import engine
    engine.dispose()


app = FastAPI(
    title="Personal Productivity and Task Execution Agent",
    description=(
        "A production-grade AI agent (LangGraph + Gemini) that manages tasks "
        "and notes through tool-calling, human-in-the-loop approval, and "
        "persistent session state."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global fallback handler for any exception not caught by a more specific
    handler. Ensures the API never leaks a raw stack trace to the client
    (which could expose internals) while still logging the full trace
    server-side for debugging.

    This is the outermost safety net referenced in ERROR HANDLING — specific
    handlers for tool exceptions, LLM errors, and DB failures are added at
    the router/service level in later phases; this one guarantees the API
    never returns an unformatted 500.
    """
    logger.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. This has been logged.",
        },
    )


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """
    Basic liveness/readiness probe.

    Returns whether the API is up and whether the LLM is configured, so
    deployment tooling (Docker healthcheck, load balancers) and the React
    frontend's initial connection check can verify the backend is fully
    operational — not just that the process is running.
    """
    return {
        "status": "ok",
        "llm_configured": settings.is_llm_configured(),
        "provider": settings.llm_provider,
        "model": settings.active_model_name,
        "max_agent_steps": settings.max_agent_steps,
    }


# -----------------------------------------------------------------------
# Router registration
# -----------------------------------------------------------------------
from app.routes.chat import router as chat_router
from app.routes.tasks import router as tasks_router
from app.routes.notes import router as notes_router
from app.routes.approvals import router as approvals_router
from app.routes.logs import router as logs_router

app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
app.include_router(notes_router, prefix="/api/notes", tags=["notes"])
app.include_router(approvals_router, prefix="/api/approvals", tags=["approvals"])
app.include_router(logs_router, prefix="/api/logs", tags=["logs"])


# -----------------------------------------------------------------------
# Built frontend (single-container deployment, e.g. Hugging Face Spaces)
# -----------------------------------------------------------------------
# Only present when the Docker build has copied the compiled React app in
# (see the root-level Dockerfile). Absent in local dev, where the frontend
# is served separately by `npm run dev` via Vite's own dev server — so
# this mount is a no-op for the normal local workflow. Registered LAST so
# it never shadows the API routes or /health above.
_frontend_dist = os.path.join(os.path.dirname(__file__), "static_frontend")
if os.path.isdir(_frontend_dist):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
    logger.info("Serving built frontend from %s", _frontend_dist)
