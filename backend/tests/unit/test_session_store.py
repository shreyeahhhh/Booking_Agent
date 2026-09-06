"""The in-memory session store -- MASTER_PLAN.md step 3.4."""

from dataclasses import replace as dc_replace

from app.conversation.machine import ConversationState, Phase
from app.session import store


def test_create_returns_a_fresh_greeting_session():
    session_id, session = store.create()
    assert session.conversation.phase == Phase.GREETING
    assert session.decision is None
    assert session.last_question is None
    assert session.recent_turns == ()
    assert store.get(session_id) is session


def test_two_sessions_get_different_ids():
    id1, _ = store.create()
    id2, _ = store.create()
    assert id1 != id2


def test_get_returns_none_for_an_unknown_id():
    assert store.get("does-not-exist") is None


def test_save_replaces_the_stored_session():
    session_id, session = store.create()
    moved_on = dc_replace(session, last_question="Which city are you moving from?")
    store.save(session_id, moved_on)
    assert store.get(session_id) is not session
    assert store.get(session_id).last_question == "Which city are you moving from?"


def test_save_bumps_last_active():
    session_id, session = store.create()
    store.save(session_id, session)
    assert store.get(session_id).last_active >= session.last_active


def test_a_restart_reset_saved_through_save_keeps_the_same_id():
    """api/routes.py builds a fresh Session(conversation=machine.start())
    for fastpath.MetaCommand.RESTART and persists it through save() like
    any other turn's outcome -- there is no separate restart() entry point
    to keep in sync with that."""
    session_id, session = store.create()
    reviewing = ConversationState(booking=session.conversation.booking, phase=Phase.REVIEW)
    store.save(session_id, dc_replace(session, conversation=reviewing))
    assert store.get(session_id).conversation.phase == Phase.REVIEW

    from app.conversation.machine import start as start_conversation

    store.save(session_id, store.Session(conversation=start_conversation()))
    assert store.get(session_id).conversation.phase == Phase.GREETING
