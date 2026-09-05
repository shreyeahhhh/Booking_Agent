"""Text REPL harness -- MASTER_PLAN.md step 2.7.

Not a pytest file (pytest only collects test_*.py / *_test.py, so this is
never picked up by a test run) -- a manual demo/debug tool that wires
everything from phase 2 together for the first time against the real Groq
API: extractor (2.1-2.3) -> phase machine (2.5) -> templates (2.4) or
summary (2.6), driven by actually typing, not synthetic patches in a test.

Run from backend/, as a module so `app.*` imports resolve correctly:

    python -m tests.repl

(`python tests/repl.py` would put tests/ on sys.path instead of backend/,
and `import app...` would fail.)
"""

from __future__ import annotations

import asyncio

from groq import AsyncGroq

from app.config import get_settings
from app.conversation import machine, summary, templates
from app.conversation.machine import Phase
from app.domain.state import Intent
from app.llm.extractor import Exchange, extract

_MAX_RECENT_TURNS = 8  # 4 exchanges (user+agent pairs), matching the extractor prompt's
# own "RECENT_TURNS: the last 4 exchanges" -- see llm/prompts/extractor.md.


def _uses_suggested_reply(extraction) -> bool:
    """The phase 2.4 escape hatch: for a turn templates.py has no slot-based
    question for anyway (a side question, something off-topic, or something
    unparseable), use what the extractor already suggested instead of
    forcing a normal next-question through the machinery. Living here,
    not in machine.py or templates.py, is deliberate -- see MASTER_PLAN.md
    step 2.5's note on why this needs no extra plumbing in either module.
    """
    return bool(extraction.suggested_reply) and extraction.intent in (
        Intent.QUESTION,
        Intent.OFF_TOPIC,
        Intent.UNCLEAR,
    )


def _closing_line(phase: Phase) -> str:
    if phase == Phase.REVIEW:
        return "Is this all correct?"
    if phase == Phase.COMPLETE:
        return "Booking confirmed. Thank you!"
    return ""


async def main() -> None:
    settings = get_settings()
    if not settings.is_configured:
        print("GROQ_API_KEY is not set -- copy .env.example to .env and add your key.")
        return

    print("Voice Booking Agent -- text mode. Type 'quit' to exit.\n")
    print(f"(model: {settings.groq_llm_model}, live calls to Groq on every turn)\n")

    conversation = machine.start()
    recent_turns: list[Exchange] = []
    last_question: str | None = None

    async with AsyncGroq(api_key=settings.groq_api_key) as client:
        while True:
            try:
                utterance = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n(exiting)")
                return
            if utterance.lower() in ("quit", "exit"):
                return
            if not utterance:
                continue

            extraction = await extract(
                client,
                model=settings.groq_llm_model,
                state=conversation.booking,
                last_question=last_question,
                recent_turns=recent_turns[-_MAX_RECENT_TURNS:],
                utterance=utterance,
            )
            result = machine.advance(conversation, extraction)
            conversation = result.conversation

            if _uses_suggested_reply(extraction):
                response = extraction.suggested_reply
            elif result.decision is not None:
                response = templates.compose_turn_response(
                    extraction.patches, conversation.booking, result.decision
                )
            else:
                summary_text = summary.render_summary(conversation.booking)
                response = f"{summary_text}\n\n{_closing_line(conversation.phase)}"

            print(f"\nAgent: {response}\n")

            recent_turns.append(Exchange("user", utterance))
            recent_turns.append(Exchange("agent", response))
            last_question = response

            print(f"[phase: {conversation.phase.value}]")
            filled = summary.build_summary(conversation.booking)
            if filled:
                print("--- state so far ---")
                print(summary.format_summary_text(filled))
            print()

            if conversation.phase == Phase.COMPLETE:
                return


if __name__ == "__main__":
    asyncio.run(main())
