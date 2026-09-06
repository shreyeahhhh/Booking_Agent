# Porter-style Voice Booking Agent

A voice agent that holds a natural conversation to gather intra-city moving and
transportation requirements, asks only for what it is actually missing, handles
corrections and ambiguity, and produces a structured booking summary for the user
to review and confirm.

> **Status: in development.** The deterministic core (Phase 1), the text conversation loop
> (Phase 2), the full voice backend and UI including live correction history (Phase 3),
> and deployment resilience — rate-limit backoff, a session TTL sweep, a cold-start UI
> state, and a verified single-service `Dockerfile` (Phase 4) — are built. Still open:
> actually deploying to a live host (the image is ready; picking and clicking through a
> specific one is the remaining step) and Phase 5's evaluation harness. See
> [`MASTER_PLAN.md`](MASTER_PLAN.md) for the exact state of every step.

## The idea in one sentence

**The LLM is a sensor, not a controller.** It converts a single utterance into proposed,
evidence-backed changes to a typed booking state. Validation, state ownership,
completeness checking, question selection, flow control and the final summary are all
deterministic application code.

The consequence: the agent cannot hallucinate a booking detail into the record, cannot
forget something it was told, cannot ask the same question twice, and cannot decide on
its own that the booking is finished.

## Why this design

A voice booking agent is a slot-filling problem wearing a conversational costume. The
tempting approach — replay the whole transcript to a model each turn and ask for JSON —
demos well and then falls apart around turn six, on corrections, and on partial answers.

So the model does the one thing it is genuinely better at than code (understanding
messy human language) and nothing else:

| The LLM handles | The application handles |
|---|---|
| Understanding natural input | Maintaining the booking state |
| Extracting several details from one utterance | Determining what is missing |
| Recognising corrections | Choosing the next question |
| Flagging ambiguous language | Preventing duplicate questions |
| Information arriving in any order | Validating and normalising values |
| | Applying confirmed corrections |
| | Final confirmation and summary |
| | Conversation flow and state transitions |

This also makes the agent cheap and fast: **at most one LLM call per turn, frequently
zero**, and every user-facing sentence comes from a template whose audio is cached.

Full detail in [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript + Vite |
| Voice capture | `MediaRecorder` + `AnalyserNode` silence detection |
| Speech-to-text | Groq `whisper-large-v3-turbo` |
| LLM | Groq `openai/gpt-oss-120b` (strict structured outputs) |
| Text-to-speech | Cartesia `sonic-latest`, with a pre-synthesised cache |
| Backend | FastAPI + Pydantic v2 |
| State | In-memory session store behind a swappable interface |
| Hosting | Single service — FastAPI serves the built frontend bundle |

Two vendors, two keys (Groq for STT + the LLM, Cartesia for TTS — see
MASTER_PLAN.md for why TTS moved off Groq's own Orpheus), both **held
server-side only**. No credential ever reaches the browser.

## Repository layout

```
backend/
  app/
    domain/         deterministic core - state, reducer, completeness, policy
                    (must not import from llm/ or services/)
    conversation/   state machine, response templates, fast-path classifier
    llm/            Groq extractor + prompt + structured output schema
    services/       STT and TTS clients
    session/        in-memory session store
    api/            FastAPI routes
  tests/
    unit/           deterministic core, no LLM calls
    eval/           extraction quality eval set
    conversations/  end-to-end golden transcripts with recorded LLM responses
frontend/
  src/              React voice UI + live booking state panel
docs/
  architecture.md   system design, trade-offs and rejected alternatives
  design.md         booking schema, LLM contract, conversation design
  test-plan.md      test layers and the conversation scenario matrix
MASTER_PLAN.md      phase-by-phase build order
```

## Documentation

| Document | Contents |
|---|---|
| [`MASTER_PLAN.md`](MASTER_PLAN.md) | Build order, phase by phase, with acceptance criteria and a risk register |
| [`docs/architecture.md`](docs/architecture.md) | System design, turn loop, when the LLM is and is not called, rejected alternatives |
| [`docs/design.md`](docs/design.md) | Booking schema, requirement classes, LLM contract and prompt, conversation design |
| [`docs/test-plan.md`](docs/test-plan.md) | Four test layers and the full conversation scenario matrix |

## Setup

Requires **Python 3.11+** and **Node 20+**.

```bash
git clone https://github.com/shreyeahhhh/Booking_Agent.git
cd Booking_Agent
cp .env.example .env      # then paste your Groq key into it
```

Backend:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
```

Frontend:

```bash
cd frontend
npm install
```

## Environment variables

All configuration lives in [`.env.example`](.env.example) — copy it to `.env` and edit.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | — | Speech-to-text and the LLM. Get one at [console.groq.com/keys](https://console.groq.com/keys). |
| `CARTESIA_API_KEY` | **Yes** | — | Text-to-speech. Get one (free, no card) at [play.cartesia.ai/keys](https://play.cartesia.ai/keys). Missing this degrades to the browser's own `speechSynthesis` rather than failing the turn. |
| `GROQ_LLM_MODEL` | No | `openai/gpt-oss-120b` | Extraction model |
| `GROQ_STT_MODEL` | No | `whisper-large-v3-turbo` | Speech-to-text |
| `CARTESIA_TTS_MODEL` | No | `sonic-latest` | Text-to-speech |
| `CARTESIA_TTS_VOICE_ID` | No | `db6b0ed5-d5d3-463d-ae85-518a07d3c2b4` ("Skylar") | Cartesia voice |
| `SESSION_TTL_SECONDS` | No | `3600` | Idle session expiry |
| `MAX_CLARIFY_ATTEMPTS` | No | `2` | Clarifications per field before an assumption is recorded |

Both keys are read server-side only. The browser never calls a vendor API, so no
credential is ever shipped to the client.

The app **starts without either key** — the deterministic core and its test suite run
with no network access at all, and a missing `CARTESIA_API_KEY` specifically degrades to
the browser's own speech synthesis rather than blocking anything. `/api/health` reports
whether each key is configured (`llm_configured`, `tts_configured`), without revealing
either one.

## Running locally

Two terminals:

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api` to the backend, so
development is same-origin exactly as production is — there is no CORS configuration
anywhere in this project.

To run the production shape locally instead, build the frontend and let FastAPI serve it
from a single origin on port 8000:

```bash
cd frontend && npm run build
cd ../backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000
```

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

The suite runs without an API key. Tests that call Groq live are marked `llm` and can be
excluded with `-m "not llm"`. See [`docs/test-plan.md`](docs/test-plan.md).

```bash
cd backend && .venv/Scripts/python -m ruff check .
cd frontend && npm run typecheck
```

## Deployment

**Live URL:** _pending — the image is ready to deploy (below); this line gets the real
link once it's live on a host._

A single-stage [`Dockerfile`](Dockerfile) at the repo root builds the frontend and
serves it, plus the API, from one FastAPI process on one origin — the same
single-service shape `app/main.py` and this README's "production shape locally"
section above already run in dev. It works on any host that deploys from a
Dockerfile (Render, Railway, Fly.io, Google Cloud Run, a plain VPS, ...); pick
whichever is convenient. Verified locally (not just written and assumed): a fresh,
isolated virtualenv installing only `requirements.txt`, then serving the real built
`frontend/dist` through `uvicorn app.main:app` exactly as the image's `CMD` does,
correctly returned both `/api/health` and the app's `index.html`.

Whichever host is chosen:

1. Point it at this repository with `Dockerfile` at the repo root as the build source
   (no extra build command needed — the Dockerfile does the whole build).
2. **Set every variable from [`.env.example`](.env.example) on that host's own
   dashboard** — a local `.env` file is never read in production; this is the single
   most common way a deployed demo silently fails (see the risk register in
   [`MASTER_PLAN.md`](MASTER_PLAN.md)). `GROQ_API_KEY` is the only one that must be a
   real value; the rest can keep their defaults.
3. Most hosts inject their own `$PORT`; the Dockerfile already reads it
   (`ENV PORT=8000` as a fallback for a plain `docker run`).
4. After it deploys, open `https://<the-deployed-url>/api/health` directly and
   confirm `llm_configured: true` — checkable from the live URL itself, not just
   locally, precisely so a key set locally but forgotten on the host's dashboard is
   caught immediately rather than discovered mid-demo.
5. Open the deployed URL itself in a fresh browser profile and grant microphone
   access — `getUserMedia` requires HTTPS everywhere except `localhost`, so this is
   the first point this can be genuinely tested at all.

Not yet done: cold-start behaviour (a free-tier host that spins down when idle can
take tens of seconds to wake for the first request) has a UI state for it
(`isSlowStart` in `App.tsx`) but has not been observed against a real cold host yet;
session TTL sweeping (`SESSION_TTL_SECONDS`) is implemented but likewise only
exercised by its unit tests so far, not a real multi-day-idle deployment.

## Assumptions and limitations

Tracked as they are made rather than reconstructed at the end.

- **Intra-city focus.** The brief's example ("Koramangala to Whitefield") is two
  localities within one city. Inter-city bookings work but are the secondary path.
- **No contact capture.** Name and phone are out of scope: capturing digits over voice is
  a speech-recognition accuracy problem, not a conversation-design one, and would add
  failure modes while demonstrating nothing the brief tests.
- **No geocoding.** Localities are captured as text; there is no pincode or lat/long
  resolution, and no validation that a place actually exists.
- **No pricing.** Producing a fare would require a rate card this project does not have.
- **Sessions are in-memory.** A server restart loses in-flight conversations. This is a
  deliberate trade for a single-session demo; the store sits behind an interface that a
  database could implement.
- **English only.**

## Future improvements

- Streaming STT with partial transcripts, for barge-in and lower perceived latency
- Locality validation against a real place database, to replace the heuristic
  city-versus-locality ambiguity check
- Persistent sessions and a booking history
- Multilingual support, which matters for the actual Indian market this models
