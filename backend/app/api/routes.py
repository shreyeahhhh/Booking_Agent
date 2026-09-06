"""HTTP routes.

Kept thin on purpose: routes orchestrate, they do not decide. All booking logic
lives in app/domain and app/conversation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace

import groq
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from groq import AsyncGroq
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.conversation import fastpath, machine, orchestrator, templates
from app.conversation.fastpath import MetaCommand
from app.llm.extractor import Exchange
from app.services import stt, tts
from app.session import store
from app.session.store import Session

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


# --------------------------------------------------------------------------
# The Groq client -- one per process (app/main.py's lifespan), not one per
# request. This dependency just fetches it and turns "not configured" into a
# clear, specific error instead of an AttributeError three calls deep.
# --------------------------------------------------------------------------


def get_groq_client(request: Request) -> AsyncGroq:
    client = request.app.state.groq_client
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured -- speech and extraction are unavailable.",
        )
    return client


# --------------------------------------------------------------------------
# POST /session -- create a conversation and speak the opening greeting.
# --------------------------------------------------------------------------


class SessionResponse(BaseModel):
    session_id: str
    agent_text: str
    audio_chunks: list[str]  # base64-encoded WAV, empty if TTS is unavailable
    tts_fallback: bool


@router.post("/session", response_model=SessionResponse)
async def create_session(
    client: AsyncGroq = Depends(get_groq_client),  # noqa: B008 -- FastAPI's own DI idiom
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> SessionResponse:
    session_id, session = store.create(ttl_seconds=settings.session_ttl_seconds)
    store.save(session_id, replace(session, last_question=templates.GREETING))

    audio_chunks = await _synthesize(client, settings, templates.GREETING)
    return SessionResponse(
        session_id=session_id,
        agent_text=templates.GREETING,
        audio_chunks=_encode(audio_chunks),
        tts_fallback=audio_chunks is None,
    )


# --------------------------------------------------------------------------
# POST /turn -- one utterance in, one response out.
# --------------------------------------------------------------------------


class TurnResponse(BaseModel):
    user_text: str
    agent_text: str
    audio_chunks: list[str]
    tts_fallback: bool
    phase: str
    done: bool
    state: dict[str, object]


@router.post("/turn", response_model=TurnResponse)
async def turn(
    session_id: str = Form(...),  # noqa: B008
    audio: UploadFile = File(...),  # noqa: B008
    client: AsyncGroq = Depends(get_groq_client),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> TurnResponse:
    session = store.get(session_id, ttl_seconds=settings.session_ttl_seconds)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown session_id -- call POST /session to start a new conversation.",
        )

    audio_bytes = await audio.read()
    filename = audio.filename or "audio.webm"
    outcome = await _process_turn(client, settings, session, audio_bytes, filename)
    store.save(session_id, outcome.session)

    booking = outcome.session.conversation.booking
    return TurnResponse(
        user_text=outcome.user_text,
        agent_text=outcome.agent_text,
        audio_chunks=_encode(outcome.audio_chunks),
        tts_fallback=outcome.audio_chunks is None,
        phase=outcome.session.conversation.phase.value,
        done=outcome.session.conversation.phase == machine.Phase.COMPLETE,
        state=booking.model_dump(mode="json"),
    )


# --------------------------------------------------------------------------
# The actual pipeline: STT -> fastpath/extract -> reduce -> policy -> template
# -> TTS. A plain function, not the endpoint itself, so it is directly
# unit-testable with a mocked client -- no UploadFile or TestClient needed.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _TurnOutcome:
    session: Session
    user_text: str
    agent_text: str
    audio_chunks: list[bytes] | None


async def _synthesize(client: AsyncGroq, settings: Settings, text: str) -> list[bytes] | None:
    return await tts.synthesize(
        client,
        model=settings.groq_tts_model,
        voice=settings.groq_tts_voice,
        text=text,
        cache_dir=settings.tts_cache_dir,
    )


def _encode(chunks: list[bytes] | None) -> list[str]:
    if chunks is None:
        return []
    return [base64.b64encode(chunk).decode("ascii") for chunk in chunks]


def _noise_reprompt(last_question: str | None) -> str:
    apology = "Sorry, I didn't catch that."
    return f"{apology} {last_question}" if last_question else apology


async def _process_turn(
    client: AsyncGroq,
    settings: Settings,
    session: Session,
    audio_bytes: bytes,
    filename: str,
) -> _TurnOutcome:
    try:
        text = await stt.transcribe(
            client, model=settings.groq_stt_model, audio=audio_bytes, filename=filename
        )
    except groq.BadRequestError:
        # services/stt.py deliberately lets a structural 400 (malformed or
        # unprocessable audio -- confirmed live: real MediaRecorder output
        # from a stream that never carried an actual signal, e.g. a muted or
        # disconnected mic, produced exactly this) propagate rather than
        # retrying it -- retrying an identical malformed request cannot
        # help. But propagating is not the same as crashing the request:
        # nothing above this point ever caught it before, so it reached
        # FastAPI's default handler as a raw 500 instead of this turn's
        # existing, already-tested "no usable speech" path. There genuinely
        # is no usable speech in an unprocessable file, so folding it into
        # the same text-is-None branch below is not a new fallback -- it is
        # this exact case fitting the contract that branch already exists
        # for.
        text = None

    if text is None or stt.is_noise(text):
        # No LLM call, no state change: nothing was understood this turn,
        # so there is nothing for the reducer or policy to do -- just ask
        # again. See services/stt.py and docs/architecture.md's "When the
        # LLM is NOT called".
        agent_text = _noise_reprompt(session.last_question)
        audio_chunks = await _synthesize(client, settings, agent_text)
        return _TurnOutcome(session, text or "", agent_text, audio_chunks)

    fp_result = fastpath.classify(text, phase=session.conversation.phase, decision=session.decision)

    if fp_result is not None and fp_result.meta_command == MetaCommand.REPEAT:
        # Also no state change: replay exactly what was last said, verbatim.
        agent_text = session.last_question or templates.GREETING
        audio_chunks = await _synthesize(client, settings, agent_text)
        return _TurnOutcome(session, text, agent_text, audio_chunks)

    if fp_result is not None and fp_result.meta_command == MetaCommand.RESTART:
        fresh = Session(conversation=machine.start())
        session = replace(fresh, last_question=templates.GREETING)
        audio_chunks = await _synthesize(client, settings, templates.GREETING)
        return _TurnOutcome(session, text, templates.GREETING, audio_chunks)

    if fp_result is not None and fp_result.extraction is not None:
        # A confident fast-path match: zero LLM calls for this turn.
        outcome = orchestrator.finish_turn(
            fp_result.extraction,
            session.conversation,
            max_clarify_attempts=settings.max_clarify_attempts,
        )
    else:
        outcome = await orchestrator.process_utterance(
            client,
            model=settings.groq_llm_model,
            conversation=session.conversation,
            last_question=session.last_question,
            recent_turns=list(session.recent_turns),
            utterance=text,
            max_clarify_attempts=settings.max_clarify_attempts,
        )

    exchange = (Exchange("user", text), Exchange("agent", outcome.response_text))
    turns = (*session.recent_turns, *exchange)
    session = replace(
        session,
        conversation=outcome.conversation,
        decision=outcome.decision,
        last_question=outcome.response_text,
        recent_turns=turns[-orchestrator.MAX_RECENT_TURNS :],
    )
    audio_chunks = await _synthesize(client, settings, outcome.response_text)
    return _TurnOutcome(session, text, outcome.response_text, audio_chunks)
