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

from app.api.routes import router
from app.config import get_settings

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.is_configured:
        log.warning(
            "GROQ_API_KEY is not set. Speech and extraction are unavailable; "
            "the deterministic core still works. Copy .env.example to .env to fix."
        )
    yield


app = FastAPI(title="Voice Booking Agent", version="0.1.0", lifespan=lifespan)

# API first, so /api/* is never shadowed by the static mount below.
app.include_router(router, prefix="/api")

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    log.info("No frontend build at %s - serving the API only.", FRONTEND_DIST)
