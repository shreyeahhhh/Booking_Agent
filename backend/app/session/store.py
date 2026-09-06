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

`created_at`/`last_active` exist for phase 4.5's TTL sweep to consume later
-- not implemented here, since sweeping unbounded memory growth is that
step's job, not this one's. Storing the timestamps now means 4.5 can add a
sweep without restructuring this module.
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


def create() -> tuple[str, Session]:
    """A brand-new session, greeting phase, nothing known yet."""
    session_id = uuid.uuid4().hex
    session = Session(conversation=start_conversation())
    _sessions[session_id] = session
    return session_id, session


def get(session_id: str) -> Session | None:
    return _sessions.get(session_id)


def save(session_id: str, session: Session) -> None:
    """The one place any turn's outcome is persisted, including a
    fastpath.MetaCommand.RESTART reset -- api/routes.py builds the reset
    `Session(conversation=machine.start())` itself and saves it through
    here like any other turn, rather than this module offering a second,
    parallel way to write the same dict entry."""
    _sessions[session_id] = replace(session, last_active=datetime.now())
