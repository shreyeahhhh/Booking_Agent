"""HTTP routes.

Kept thin on purpose: routes orchestrate, they do not decide. All booking logic
lives in app/domain and app/conversation.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
    llm_model: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus a configuration hint.

    Reports whether a Groq key is present without ever revealing it, which makes
    "is the deployment actually wired up?" answerable from a browser.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        llm_configured=settings.is_configured,
        llm_model=settings.groq_llm_model,
    )
