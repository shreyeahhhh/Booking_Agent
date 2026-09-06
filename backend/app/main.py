"""FastAPI entrypoint.

In production this single service also serves the built frontend bundle, so the
whole app lives behind one origin and one HTTPS certificate. That removes CORS
from the picture entirely and means there is exactly one URL to deploy and share.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from groq import AsyncGroq

from app.api.routes import router
from app.config import get_settings

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.is_configured:
        log.warning(
            "GROQ_API_KEY is not set. Speech and extraction are unavailable; "
            "the deterministic core still works. Copy .env.example to .env to fix."
        )
    # One client for the process lifetime, not one per request: AsyncGroq
    # holds a connection pool, and every /turn call already makes up to
    # three separate Groq calls (STT, maybe the LLM, TTS) -- there is no
    # reason to pay connection setup on each one. None when unconfigured,
    # matching the rest of this app's "starts fine without a key, fails
    # clearly only where a key is actually needed" contract (config.py).
    app.state.groq_client = (
        AsyncGroq(api_key=settings.groq_api_key) if settings.is_configured else None
    )
    try:
        yield
    finally:
        if app.state.groq_client is not None:
            await app.state.groq_client.close()


app = FastAPI(title="Voice Booking Agent", version="0.1.0", lifespan=lifespan)

# API first, so /api/* is never shadowed by the static mount below.
app.include_router(router, prefix="/api")

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    log.info("No frontend build at %s - serving the API only.", FRONTEND_DIST)
