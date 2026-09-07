"""HTTP routes.

Kept thin on purpose: routes orchestrate, they do not decide. All booking logic
lives in app/domain and app/conversation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from typing import Literal

import groq
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from groq import AsyncGroq
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.conversation import fastpath, machine, orchestrator, templates
from app.conversation.fastpath import MetaCommand
from app.domain.specs import get_field
from app.domain.state import ExtractionResult, FieldStatus, Intent, Patch, PatchOp
from app.llm import scope_guard
from app.llm.extractor import Exchange
from app.services import maps, stt, tts
from app.session import store
from app.session.store import Session

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
    llm_model: str
    tts_configured: bool


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus a configuration hint.

    Reports whether each key is present without ever revealing it, which makes
    "is the deployment actually wired up?" answerable from a browser -- for both
    credentials this app now holds, not just Groq's. `tts_configured: false` is
    not a broken deploy the way `llm_configured: false` would be: the browser
    speechSynthesis fallback already covers it, just with a more robotic voice.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        llm_configured=settings.is_configured,
        llm_model=settings.groq_llm_model,
        tts_configured=settings.cartesia_is_configured,
    )


# --------------------------------------------------------------------------
# The Groq and Cartesia clients -- one each per process (app/main.py's
# lifespan), not one per request. get_groq_client turns "not configured" into
# a clear, specific error instead of an AttributeError three calls deep, since
# Groq (STT + the LLM) is core to every turn. get_cartesia_client never
# raises: an absent Cartesia key degrades a turn's *voice*, not its
# correctness, so services/tts.synthesize() is the layer that decides what a
# None client means, not this dependency.
# --------------------------------------------------------------------------


def get_groq_client(request: Request) -> AsyncGroq:
    client = request.app.state.groq_client
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="GROQ_API_KEY is not configured -- speech and extraction are unavailable.",
        )
    return client


def get_cartesia_client(request: Request) -> httpx.AsyncClient | None:
    return request.app.state.cartesia_client


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The generic client for resolving a pasted Google Maps link
    (services/maps.py) -- always present, unlike the two above, since
    following a redirect needs no credential and so has no "unconfigured"
    state to report."""
    return request.app.state.http_client


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
    cartesia_client: httpx.AsyncClient | None = Depends(get_cartesia_client),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> SessionResponse:
    session_id, session = store.create(ttl_seconds=settings.session_ttl_seconds)
    store.save(session_id, replace(session, last_question=templates.GREETING))

    audio_chunks = await _synthesize(cartesia_client, settings, templates.GREETING)
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
    cartesia_client: httpx.AsyncClient | None = Depends(get_cartesia_client),  # noqa: B008
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
    outcome = await _process_turn(client, cartesia_client, settings, session, audio_bytes, filename)
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
# POST /session/{id}/location -- an exact pickup/drop point via a pasted
# Google Maps link, instead of saying a locality out loud.
# --------------------------------------------------------------------------


class LocationLinkRequest(BaseModel):
    field: Literal["pickup", "drop"]
    url: str


@router.post("/session/{session_id}/location", response_model=TurnResponse)
async def submit_location_link(
    session_id: str,
    body: LocationLinkRequest,
    cartesia_client: httpx.AsyncClient | None = Depends(get_cartesia_client),  # noqa: B008
    http_client: httpx.AsyncClient = Depends(get_http_client),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> TurnResponse:
    session = store.get(session_id, ttl_seconds=settings.session_ttl_seconds)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown session_id -- call POST /session to start a new conversation.",
        )

    outcome = await _process_location_link(http_client, cartesia_client, settings, session, body)
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


async def _synthesize(
    cartesia_client: httpx.AsyncClient | None, settings: Settings, text: str
) -> list[bytes] | None:
    return await tts.synthesize(
        cartesia_client,
        model=settings.cartesia_tts_model,
        voice_id=settings.cartesia_tts_voice_id,
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


def _connection_failure_reprompt(last_question: str | None) -> str:
    """Distinct from _noise_reprompt on purpose. "Didn't catch that" is
    honest when there was genuinely no usable speech (silence, noise,
    unprocessable audio) -- it is actively misleading when the real cause is
    services/stt.py's retry budget being exhausted against a connection
    error, which says nothing about what the user actually said. Confirmed
    live: a real STT outage produced exactly the generic noise message,
    reading as "you mumbled" for a failure that had nothing to do with the
    user's speech at all."""
    apology = "Sorry, I'm having trouble connecting right now -- could you try again?"
    return f"{apology} {last_question}" if last_question else apology


# A fixed, non-LLM-generated redirect -- deliberately not routed through
# templates.py's usual per-field variety, and never spoken by the model
# itself (llm/scope_guard.py never produces text, only allowed/intent): a
# guardrail's own refusal message should be exactly as predictable as the
# guardrail itself, not one more thing an adversarial or merely persistent
# user could talk the model into rephrasing.
_SCOPE_REJECTION_MESSAGE = "I can help with your delivery booking. What would you like to do?"


async def _process_turn(
    client: AsyncGroq,
    cartesia_client: httpx.AsyncClient | None,
    settings: Settings,
    session: Session,
    audio_bytes: bytes,
    filename: str,
) -> _TurnOutcome:
    connection_failed = False
    try:
        text = await stt.transcribe(
            client, model=settings.groq_stt_model, audio=audio_bytes, filename=filename
        )
        # services/stt.py returns None only after its own retry budget is
        # exhausted against a connection/rate-limit/server error -- distinct
        # from the BadRequestError case below, which means the audio itself
        # was unusable, not that the service was unreachable.
        connection_failed = text is None
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

    if connection_failed:
        agent_text = _connection_failure_reprompt(session.last_question)
        audio_chunks = await _synthesize(cartesia_client, settings, agent_text)
        return _TurnOutcome(session, "", agent_text, audio_chunks)

    if text is None or stt.is_noise(text):
        # No LLM call, no state change: nothing was understood this turn,
        # so there is nothing for the reducer or policy to do -- just ask
        # again. See services/stt.py and docs/architecture.md's "When the
        # LLM is NOT called".
        agent_text = _noise_reprompt(session.last_question)
        audio_chunks = await _synthesize(cartesia_client, settings, agent_text)
        return _TurnOutcome(session, text or "", agent_text, audio_chunks)

    fp_result = fastpath.classify(text, phase=session.conversation.phase, decision=session.decision)

    if fp_result is not None and fp_result.meta_command == MetaCommand.REPEAT:
        # Also no state change: replay exactly what was last said, verbatim.
        agent_text = session.last_question or templates.GREETING
        audio_chunks = await _synthesize(cartesia_client, settings, agent_text)
        return _TurnOutcome(session, text, agent_text, audio_chunks)

    if fp_result is not None and fp_result.meta_command == MetaCommand.RESTART:
        fresh = Session(conversation=machine.start())
        session = replace(fresh, last_question=templates.GREETING)
        audio_chunks = await _synthesize(cartesia_client, settings, templates.GREETING)
        return _TurnOutcome(session, text, templates.GREETING, audio_chunks)

    if fp_result is not None and fp_result.extraction is not None:
        # A confident fast-path match: zero LLM calls for this turn. Already
        # in scope by construction -- it is a direct answer to the agent's
        # own pending question -- so the scope guard below does not apply.
        outcome = orchestrator.finish_turn(
            fp_result.extraction,
            session.conversation,
            max_clarify_attempts=settings.max_clarify_attempts,
        )
    elif not (await scope_guard.classify(client, model=settings.scope_guard_model, utterance=text)).allowed:
        # Off-topic, or the guard itself failed closed (see scope_guard.py's
        # module docstring) -- no state change, no extractor call, same
        # "just speak a fixed reply" shape as the noise/connection-failure
        # branches above.
        audio_chunks = await _synthesize(cartesia_client, settings, _SCOPE_REJECTION_MESSAGE)
        return _TurnOutcome(session, text, _SCOPE_REJECTION_MESSAGE, audio_chunks)
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

    session = _advance_session(session, text, outcome)
    audio_chunks = await _synthesize(cartesia_client, settings, outcome.response_text)
    return _TurnOutcome(session, text, outcome.response_text, audio_chunks)


def _advance_session(session: Session, user_text: str, outcome: orchestrator.TurnOutcome) -> Session:
    """The bookkeeping any turn's outcome needs applied to the session,
    regardless of what produced it -- real speech, a fastpath.classify() hit,
    or a pasted map link (_process_location_link below) -- factored out so
    those callers cannot drift on it the way two independent copies would."""
    exchange = (Exchange("user", user_text), Exchange("agent", outcome.response_text))
    turns = (*session.recent_turns, *exchange)
    return replace(
        session,
        conversation=outcome.conversation,
        decision=outcome.decision,
        last_question=outcome.response_text,
        recent_turns=turns[-orchestrator.MAX_RECENT_TURNS :],
    )


# --------------------------------------------------------------------------
# A pasted Google Maps link's own path -- deterministic, no LLM call: see
# services/maps.py's module docstring for why parsing a URL is not a
# language-understanding problem.
# --------------------------------------------------------------------------

_LOCATION_LINK_PARSE_FAILURE = (
    "I couldn't read that as a location -- could you check the link, or just tell me the area?"
)
_LOCATION_LINK_USER_TEXT = "(pasted a Google Maps link)"


async def _process_location_link(
    http_client: httpx.AsyncClient,
    cartesia_client: httpx.AsyncClient | None,
    settings: Settings,
    session: Session,
    body: LocationLinkRequest,
) -> _TurnOutcome:
    """Mirrors _process_turn's fast-path branch: build an ExtractionResult
    by hand and hand it to orchestrator.finish_turn, so a dropped pin
    advances the conversation exactly like any other provided field would
    -- the next question, review, or completion, decided by the same code
    either way, not a special case bolted on beside it."""
    parsed = await maps.resolve_maps_link(http_client, body.url)
    if parsed is None:
        audio_chunks = await _synthesize(cartesia_client, settings, _LOCATION_LINK_PARSE_FAILURE)
        return _TurnOutcome(
            session, _LOCATION_LINK_USER_TEXT, _LOCATION_LINK_PARSE_FAILURE, audio_chunks
        )

    # Reverse-geocoded to a human-readable name where possible; falls back to
    # plain coordinates on any failure (rate limit, no address data for this
    # point) rather than blocking the turn on a non-essential lookup.
    resolved_name = await maps.reverse_geocode(http_client, parsed)
    value = resolved_name or parsed.as_locality_value()

    field_path = f"{body.field}.locality"
    current = get_field(session.conversation.booking, field_path)
    op = PatchOp.CORRECT if current.status != FieldStatus.EMPTY else PatchOp.SET
    patch = Patch(
        op=op,
        field=field_path,
        value=value,
        confidence=1.0,
        # No `evidence` here on purpose: evidence exists to show what
        # speech an interpreted value was normalised from (Rule 10's
        # "(heard as ...)" -- summary.py's _render_address, format.ts's
        # withHeardAs), which is genuinely useful for a spoken mishearing a
        # human can visually compare against. A pasted link has nothing
        # comparable to show -- "Kottayam (heard as
        # 'https://maps.app.goo.gl/...')" conveys no verification value, it
        # just clutters an already-exact answer. This is the *value* on the
        # patch reaching the reducer at full, uncaveated confidence, same as
        # any other confidently-stated field -- an exact GPS pin resolved to
        # a real place has nothing to hedge.
        evidence=None,
    )
    extraction = ExtractionResult(intent=Intent.PROVIDE_INFO, patches=[patch])

    outcome = orchestrator.finish_turn(
        extraction, session.conversation, max_clarify_attempts=settings.max_clarify_attempts
    )
    session = _advance_session(session, _LOCATION_LINK_USER_TEXT, outcome)
    audio_chunks = await _synthesize(cartesia_client, settings, outcome.response_text)
    return _TurnOutcome(session, _LOCATION_LINK_USER_TEXT, outcome.response_text, audio_chunks)
