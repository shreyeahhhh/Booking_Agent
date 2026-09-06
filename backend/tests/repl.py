"""Text REPL harness -- MASTER_PLAN.md step 2.7.

Not a pytest file (pytest only collects test_*.py / *_test.py, so this is
never picked up by a test run) -- a manual demo/debug tool that wires
everything from phase 2 together for the first time against the real Groq
API: extractor (2.1-2.3) -> phase machine (2.5) -> templates (2.4) or
summary (2.6), driven by actually typing, not synthetic patches in a test.

The "understand -> advance -> decide what to say" sequence itself now lives
in conversation/orchestrator.py (step 3.4), shared with the /turn endpoint
-- this file calls `process_utterance` on every turn, unconditionally,
by design: it exists to exercise real extraction quality against the live
API, not to demonstrate the fast-path savings the endpoint additionally
gets from conversation/fastpath.py.

Run from backend/, as a module so `app.*` imports resolve correctly:

    python -m tests.repl

(`python tests/repl.py` would put tests/ on sys.path instead of backend/,
and `import app...` would fail.)
"""

from __future__ import annotations

import asyncio

from groq import AsyncGroq

from app.config import get_settings
from app.conversation import machine, summary
from app.conversation.machine import Phase
from app.conversation.orchestrator import MAX_RECENT_TURNS, process_utterance
from app.llm.extractor import Exchange


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

            outcome = await process_utterance(
                client,
                model=settings.groq_llm_model,
                conversation=conversation,
                last_question=last_question,
                recent_turns=recent_turns[-MAX_RECENT_TURNS:],
                utterance=utterance,
            )
            conversation = outcome.conversation
            response = outcome.response_text

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
