# Relay

**Book a delivery or moving truck by talking — no app, no forms, no menus.**

Relay is a voice assistant for booking intra-city moving and delivery jobs, in the
style of services like Porter. You describe your move the way you'd describe it to a
person on the phone. Relay only asks about whatever you haven't already told it, catches
and fixes misunderstandings, and reads the whole booking back to you before anything is
confirmed.

### 🔗 [Try Relay live](https://porter-booking-agent.onrender.com/)

Built as a one-week take-home technical assessment.

---

## What Relay does

- **You talk, it listens.** Tap the mic and describe your move naturally — *"I need to
  move a sofa and a fridge from Koramangala to Whitefield tomorrow evening."*
- **It only asks what's still missing.** If you already said the pickup floor, Relay
  won't ask again. Nothing gets asked twice.
- **It asks instead of guessing.** If something you say is unclear, Relay asks a
  follow-up rather than assuming — a booking is never filled in with a guess.
- **You can change your mind mid-conversation.** *"Actually, make it Saturday"* is
  understood as a correction to what you already said, not a brand-new detail.
- **Not sure of the exact address?** Paste a Google Maps link instead of saying it out
  loud, and Relay uses that exact pinned location.
- **It reads everything back before booking.** A clear on-screen summary shows exactly
  what Relay understood, so nothing is confirmed by accident.
- **It stays on topic.** Relay only helps with the booking in front of it — it won't
  solve a maths problem, write code, or wander off into small talk unrelated to the job.

## How it works, in one paragraph

Relay splits the work in two. An AI model only ever *understands what you said* — it
turns your sentence into a list of facts ("pickup: Koramangala", "item: sofa"). A
separate, plain, predictable set of rules — ordinary code, not AI — decides what those
facts mean, what's still missing, what to ask next, and when the booking is complete.
That split is deliberate: it means Relay can't invent a detail you never said, can't
forget something you mentioned three turns ago, and can't quietly decide on its own that
a booking is finished. Every part of the actual booking record is something you
genuinely said.

Curious how that's actually built? The rest of this document, and
[`docs/architecture.md`](docs/architecture.md), cover the engineering in full.

---

## For developers

### The idea in one sentence

**The LLM is a sensor, not a controller.** It converts a single utterance into proposed,
evidence-backed changes to a typed booking state. Validation, state ownership,
completeness checking, question selection, flow control and the final summary are all
deterministic application code — never the model.

### Why this design

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

This also makes Relay cheap and fast: **at most two LLM calls per turn (a small scope
check, then extraction), frequently zero**, and every user-facing sentence comes from a
template whose audio is cached.

Full detail in [`docs/architecture.md`](docs/architecture.md).

### Tech stack

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript + Vite |
| Voice capture | `MediaRecorder` + `AnalyserNode` silence detection |
| Speech-to-text | Groq `whisper-large-v3-turbo` |
| LLM (extraction + scope guard) | Groq `openai/gpt-oss-120b` / `gpt-oss-20b` (strict structured outputs) |
| Text-to-speech | Cartesia `sonic-latest`, with a pre-synthesised cache |
| Backend | FastAPI + Pydantic v2 |
| State | In-memory session store behind a swappable interface |
| Hosting | Single service on Render — FastAPI serves the built frontend bundle |

Two vendors, two keys (Groq for STT + the LLM, Cartesia for TTS — see
MASTER_PLAN.md for why TTS moved off Groq's own Orpheus), both **held
server-side only**. No credential ever reaches the browser.

### Repository layout

```
backend/
  app/
    domain/         deterministic core - state, reducer, completeness, policy
                    (must not import from llm/ or services/)
    conversation/   state machine, response templates, fast-path classifier
    llm/            Groq extractor + scope guard + prompts + structured output schema
    services/       STT, TTS and map-link clients
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

### Documentation

| Document | Contents |
|---|---|
| [`MASTER_PLAN.md`](MASTER_PLAN.md) | Build order, phase by phase, with acceptance criteria and a risk register |
| [`docs/architecture.md`](docs/architecture.md) | System design, turn loop, when the LLM is and is not called, rejected alternatives |
| [`docs/design.md`](docs/design.md) | Booking schema, requirement classes, LLM contract and prompt, conversation design |
| [`docs/test-plan.md`](docs/test-plan.md) | Four test layers and the full conversation scenario matrix |

### Setup

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

### Environment variables

All configuration lives in [`.env.example`](.env.example) — copy it to `.env` and edit.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | — | Speech-to-text, extraction and the scope guard. Get one at [console.groq.com/keys](https://console.groq.com/keys). |
| `CARTESIA_API_KEY` | **Yes** | — | Text-to-speech. Get one (free, no card) at [play.cartesia.ai/keys](https://play.cartesia.ai/keys). Missing this degrades to the browser's own `speechSynthesis` rather than failing the turn. |
| `GROQ_LLM_MODEL` | No | `openai/gpt-oss-120b` | Extraction model |
| `SCOPE_GUARD_MODEL` | No | `openai/gpt-oss-20b` | Keeps Relay from answering anything unrelated to the booking — a separate, cheaper model on its own quota |
| `GROQ_STT_MODEL` | No | `whisper-large-v3-turbo` | Speech-to-text |
| `CARTESIA_TTS_MODEL` | No | `sonic-latest` | Text-to-speech |
| `CARTESIA_TTS_VOICE_ID` | No | `db6b0ed5-d5d3-463d-ae85-518a07d3c2b4` ("Skylar") | Cartesia voice |
| `SESSION_TTL_SECONDS` | No | `3600` | Idle session expiry |
| `MAX_CLARIFY_ATTEMPTS` | No | `2` | Clarifications per field before an assumption is recorded |

Both keys are read server-side only. The browser never calls a vendor API, so no
credential is ever shipped to the client.

Relay **starts without either key** — the deterministic core and its test suite run
with no network access at all, and a missing `CARTESIA_API_KEY` specifically degrades to
the browser's own speech synthesis rather than blocking anything. `/api/health` reports
whether each key is configured (`llm_configured`, `tts_configured`), without revealing
either one.

### Running locally

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

### Tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

The suite runs without an API key. Tests that call Groq live are marked `llm` and can be
excluded with `-m "not llm"`. See [`docs/test-plan.md`](docs/test-plan.md).

```bash
cd backend && .venv/Scripts/python -m ruff check .
cd frontend && npm run typecheck
```

### Deployment

**Live URL:** [porter-booking-agent.onrender.com](https://porter-booking-agent.onrender.com/)
— deployed on Render, built from the `Dockerfile` at the repo root. Verified live, not
just assumed: `/api/health` returns `llm_configured: true` and `tts_configured: true`,
the real page (title, built JS/CSS bundles) loads correctly, and both asset bundles
serve `200`.

A single-stage [`Dockerfile`](Dockerfile) at the repo root builds the frontend and
serves it, plus the API, from one FastAPI process on one origin — the same
single-service shape `app/main.py` and this README's "production shape locally"
section above already run in dev. It works on any host that deploys from a
Dockerfile (Render, Railway, Fly.io, Google Cloud Run, a plain VPS, ...); this
project uses Render. Verified locally before deploying too: a fresh, isolated
virtualenv installing only `requirements.txt`, then serving the real built
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

Not yet done: microphone access on the deployed HTTPS origin has not been personally
verified in a fresh browser profile yet. Cold-start behaviour (Render's free tier spins
down after 15 minutes idle) has a UI state for it (`isSlowStart` in `App.tsx`) but has
not yet been directly observed against a real cold instance. Session TTL sweeping
(`SESSION_TTL_SECONDS`) is implemented but likewise only exercised by its unit tests so
far, not a real multi-day-idle deployment.

### Assumptions and limitations

Tracked as they are made rather than reconstructed at the end.

- **Intra-city focus.** The brief's example ("Koramangala to Whitefield") is two
  localities within one city. Inter-city bookings work but are the secondary path.
- **No contact capture.** Name and phone are out of scope: capturing digits over voice is
  a speech-recognition accuracy problem, not a conversation-design one, and would add
  failure modes while demonstrating nothing the brief tests.
- **Geocoding is opt-in, not automatic.** A spoken locality is still captured as plain
  text with no validation that the place exists — but you can paste a Google Maps link
  for an exact pickup/drop point instead, which resolves to real coordinates and, where
  available, a reverse-geocoded name (via Nominatim/OpenStreetMap). Voice input alone
  still has no geocoding behind it.
- **Browser: built and tested against Chrome.** `MediaRecorder`/`getUserMedia` support
  and behaviour differs across browsers; Relay targets Chrome specifically and has not
  been verified on Firefox or Safari. A fresh-profile mic-permission check on the live
  deployed URL is still outstanding — see Deployment above.
- **No pricing.** Producing a fare would require a rate card this project does not have.
- **Sessions are in-memory.** A server restart loses in-flight conversations. This is a
  deliberate trade for a single-session demo; the store sits behind an interface that a
  database could implement.
- **English only.**

### Future improvements

- Streaming STT with partial transcripts, for barge-in and lower perceived latency
- Locality validation against a real place database, to replace the heuristic
  city-versus-locality ambiguity check
- Persistent sessions and a booking history
- Multilingual support, which matters for the actual Indian market Relay models
