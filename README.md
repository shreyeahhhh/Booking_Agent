# Porter-style Voice Booking Agent

A voice agent that holds a natural conversation to gather intra-city moving and
transportation requirements, asks only for what it is actually missing, handles
corrections and ambiguity, and produces a structured booking summary for the user
to review and confirm.

> **Status: in development.** Live demo URL and setup instructions land before submission.

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
| Text-to-speech | Groq `canopylabs/orpheus-v1-english`, with a pre-synthesised cache |
| Backend | FastAPI + Pydantic v2 |
| State | In-memory session store behind a swappable interface |
| Hosting | Single service — FastAPI serves the built frontend bundle |

One vendor for STT, LLM and TTS means **one API key**, held server-side only. No
credential ever reaches the browser.

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
  architecture.md   design, trade-offs and rejected alternatives
```

## Setup

_To be written once the backend runs end to end._

## Environment variables

See [`.env.example`](.env.example). A single `GROQ_API_KEY` covers speech-to-text,
the LLM and text-to-speech.

## Running locally

_To be written._

## Deployment

_To be written._

## Assumptions and limitations

_To be written — tracked as they are made, not reconstructed at the end._

## Future improvements

_To be written._
