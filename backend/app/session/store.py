"""In-memory conversation sessions -- MASTER_PLAN.md step 3.4.

One process, one dict. This project is a single-instance demo deployment
(docs/architecture.md: one service, one origin, no database -- see the
README's assumptions and limitations), so a database or an external cache
would be solving a scaling problem this deployment does not have.

`Session` carries everything a `/turn` call needs to pick up a conversation
where the last one left off, without re-deriving anything from a rendered
question's text:

- `conversation` -- the booking and phase (`conversation.machine`).
- `decision` -- exactly what `TurnResult.decision` was after the last turn,
  so `conversation.fastpath.classify()` on the *next* utterance knows what
  question is actually pending, the same value the caller already had
  rather than something reconstructed from `last_question`'s prose.
- `last_question` -- the last thing the agent actually said, verbatim, for
  the extractor's own `LAST_QUESTION` field and for replaying on a
  `fastpath.MetaCommand.REPEAT`.
- `recent_turns` -- bounded history for the extractor's `RECENT_TURNS`.

`created_at`/`last_active` back phase 4.5's TTL sweep: `ttl_seconds` is an
optional parameter on `create()`/`get()`, not read from `app.config`
directly, for the same DI reason `api/routes.py`'s own `Settings` dependency
is testable rather than hardcoded (step 3.4's finding: a route reaching for
`get_settings()` itself, instead of receiving it, was exactly what let a
stale on-disk TTS cache silently invalidate a test). Every existing caller
that omits `ttl_seconds` gets the pre-4.5 behaviour (sessions live forever)
unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.conversation.machine import ConversationState
from app.conversation.machine import start as start_conversation
from app.domain.policy import SlotDecision
from app.llm.extractor import Exchange


@dataclass(frozen=True)
class Session:
    conversation: ConversationState
    decision: SlotDecision | None = None
    last_question: str | None = None
    recent_turns: tuple[Exchange, ...] = ()
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)


_sessions: dict[str, Session] = {}


def _is_expired(session: Session, *, ttl_seconds: int, now: datetime) -> bool:
    return (now - session.last_active).total_seconds() > ttl_seconds


def _sweep_expired(ttl_seconds: int) -> None:
    now = datetime.now()
    expired = [
        session_id
        for session_id, session in _sessions.items()
        if _is_expired(session, ttl_seconds=ttl_seconds, now=now)
    ]
    for session_id in expired:
        del _sessions[session_id]


def create(*, ttl_seconds: int | None = None) -> tuple[str, Session]:
    """A brand-new session, greeting phase, nothing known yet.

    Sweeps every other idle-expired session first when `ttl_seconds` is
    given. New-session creation is the one call site guaranteed to run once
    per distinct visitor -- a single visitor's own later turns only ever
    update their *own* existing entry via `save()`, never add a new key --
    so it is exactly the point at which "how many stale entries have piled
    up" actually matters, without needing a separate background task and
    its own lifecycle to manage.
    """
    if ttl_seconds is not None:
        _sweep_expired(ttl_seconds)
    session_id = uuid.uuid4().hex
    session = Session(conversation=start_conversation())
    _sessions[session_id] = session
    return session_id, session


def get(session_id: str, *, ttl_seconds: int | None = None) -> Session | None:
    """None for an unknown id -- and, when `ttl_seconds` is given, also for
    one that has since gone idle past it. Expired is made observably
    identical to never-existed here (rather than a distinct "expired"
    response) specifically so `api/routes.py`'s existing 404 branch on
    `store.get(...) is None` already covers both, with nothing new for
    callers to handle. Deletes the expired entry on the way out, so a
    session that is only ever looked up again (never recreated) does not
    have to wait for some other visitor's `create()` to sweep it.
    """
    session = _sessions.get(session_id)
    if session is None:
        return None
    now = datetime.now()
    if ttl_seconds is not None and _is_expired(session, ttl_seconds=ttl_seconds, now=now):
        del _sessions[session_id]
        return None
    return session


def save(session_id: str, session: Session) -> None:
    """The one place any turn's outcome is persisted, including a
    fastpath.MetaCommand.RESTART reset -- api/routes.py builds the reset
    `Session(conversation=machine.start())` itself and saves it through
    here like any other turn, rather than this module offering a second,
    parallel way to write the same dict entry."""
    _sessions[session_id] = replace(session, last_active=datetime.now())
