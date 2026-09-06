import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { BookingStateShape, TurnResponse } from "./api";
import { createSession, postTurn } from "./api";
import { speak } from "./audio";
import { useRecorder } from "./useRecorder";

type Turn = { speaker: "you" | "relay"; text: string };

type Row = { label: string; value: string; filled: boolean };

// A free-tier host that spins down when idle can take tens of seconds to
// serve the very first request -- MASTER_PLAN.md step 4.3. Below this, a
// bare "Connecting…" reads as normal page-load latency; past it, silence
// reads as broken, so the message switches to say plainly what is likely
// happening instead of leaving the user guessing.
const COLD_START_HINT_MS = 4_000;

const MIME_EXTENSIONS: Record<string, string> = {
  "audio/webm": "webm",
  "audio/ogg": "ogg",
  "audio/wav": "wav",
  "audio/mp4": "m4a",
};

function extensionFor(mimeType: string): string {
  const base = mimeType.split(";")[0].trim();
  return MIME_EXTENSIONS[base] ?? "webm";
}

function prettify(raw: string | null): string {
  if (!raw) return "";
  return raw
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function formatItems(items: BookingStateShape["goods"]["items"]): string {
  return items
    .map((item) => (item.quantity > 1 ? `${item.quantity} ${item.name}` : item.name))
    .join(", ");
}

function rowsFrom(state: BookingStateShape | null): Row[] {
  if (!state) {
    return [
      { label: "From", value: "Say where", filled: false },
      { label: "To", value: "Say where", filled: false },
      { label: "Stuff", value: "Say what", filled: false },
      { label: "When", value: "Say when", filled: false },
      { label: "Truck", value: "We'll pick", filled: false },
    ];
  }
  const when = [formatDate(state.schedule.date.value), state.schedule.time_window.value]
    .filter(Boolean)
    .join(" · ");
  const items = formatItems(state.goods.items);
  return [
    { label: "From", value: state.pickup.locality.value || "Say where", filled: !!state.pickup.locality.value },
    { label: "To", value: state.drop.locality.value || "Say where", filled: !!state.drop.locality.value },
    { label: "Stuff", value: items || "Say what", filled: items.length > 0 },
    { label: "When", value: when || "Say when", filled: when.length > 0 },
    {
      label: "Truck",
      value: prettify(state.service.vehicle_type.value) || "We'll pick",
      filled: !!state.service.vehicle_type.value,
    },
  ];
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [bookingState, setBookingState] = useState<BookingStateShape | null>(null);
  const [done, setDone] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isSlowStart, setIsSlowStart] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const sessionRequestedRef = useRef(false);

  const rows = useMemo(() => rowsFrom(bookingState), [bookingState]);
  const filledCount = rows.filter((r) => r.filled).length;

  const applyTurn = useCallback((response: TurnResponse | { agent_text: string }, userText?: string) => {
    setTurns((prev) => [
      ...prev,
      ...(userText ? [{ speaker: "you" as const, text: userText }] : []),
      { speaker: "relay" as const, text: response.agent_text },
    ]);
    if ("state" in response) {
      setBookingState(response.state);
      setDone(response.done);
    }
  }, []);

  useEffect(() => {
    // Guards against React 19 StrictMode's dev-only mount -> cleanup ->
    // mount cycle, which would otherwise create two sessions per page load
    // (harmless in production, where StrictMode does not do this, but
    // wasteful and confusing to debug in dev). Deliberately NOT paired with
    // a `cancelled`-on-cleanup flag the way a fetch-on-prop-change effect
    // normally would be: that pattern actively breaks this one-time,
    // ref-guarded call. StrictMode's simulated cleanup still runs once
    // between the two invocations regardless of the ref guard, so a
    // `cancelled` flag set by that cleanup would silently discard the
    // response from the one real request the guard correctly allowed
    // through -- caught by the greeting never appearing in a real browser
    // even though the network tab showed a correct 200 response.
    if (sessionRequestedRef.current) return;
    sessionRequestedRef.current = true;

    // A free host that spun down while idle can take tens of seconds to
    // wake for this very first request -- see COLD_START_HINT_MS above.
    // Cleared in both branches below, so a fast, ordinary response never
    // shows the hint at all.
    const coldStartTimer = window.setTimeout(() => setIsSlowStart(true), COLD_START_HINT_MS);

    createSession()
      .then((session) => {
        window.clearTimeout(coldStartTimer);
        sessionIdRef.current = session.session_id;
        setSessionId(session.session_id);
        applyTurn(session);
        void speak(session.audio_chunks, session.tts_fallback, session.agent_text);
      })
      .catch(() => {
        window.clearTimeout(coldStartTimer);
        setApiError("Could not reach the booking service. Refresh to try again.");
      });
  }, [applyTurn]);

  const handleRecordingComplete = useCallback(
    (audio: Blob, mimeType: string) => {
      const currentSessionId = sessionIdRef.current;
      if (!currentSessionId) return;
      postTurn(currentSessionId, audio, `clip.${extensionFor(mimeType)}`)
        .then((response) => {
          applyTurn(response, response.user_text || undefined);
          void speak(response.audio_chunks, response.tts_fallback, response.agent_text);
        })
        .catch(() => setApiError("That turn didn't go through. Try again."));
    },
    [applyTurn],
  );

  const { status, error: recorderError, start, stop } = useRecorder(handleRecordingComplete);

  const micLabel =
    status === "recording" ? "Listening…" : status === "processing" ? "Thinking…" : "Tap to talk";
  const micSublabel = recorderError ?? (done ? "Booking confirmed" : "No app. No forms.");
  const visualizerActive = status === "recording";

  const handleMicClick = () => {
    if (status === "idle" || status === "error") void start();
    else if (status === "recording") stop();
  };

  const last = turns[turns.length - 1];

  return (
    <div className="site">
      <header className="header">
        <div className="header__brand">
          <span className="dot" />
          <span className="header__name">Relay</span>
        </div>
        <a href="#try" className="pill-button">
          Talk to it
        </a>
      </header>

      <section className="hero">
        <div className="hero__eyebrow">
          <span className="eyebrow-dot" />
          Book a truck by talking
        </div>

        <h1 className="hero__title">
          Just
          <br />
          say it.
        </h1>

        <div className="hero__controls">
          <button type="button" className={`mic mic--${status}`} onClick={handleMicClick}>
            <span className="mic__button">
              <span className="mic__ring" />
              <span className="mic__ring mic__ring--delay" />
              <svg
                width="34"
                height="34"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#F1ECE3"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ position: "relative" }}
              >
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
              </svg>
            </span>
            <span>
              <span className="mic__label">{micLabel}</span>
              <span className="mic__sublabel">{micSublabel}</span>
            </span>
          </button>

          <div className={`visualizer ${visualizerActive ? "visualizer--active" : ""}`}>
            {[0, 0.12, 0.24, 0.36, 0.18, 0.3, 0.06, 0.42].map((delay, i) => (
              <span
                key={i}
                className="visualizer__bar"
                style={{
                  animationDelay: `${delay}s`,
                  background: i === 2 ? "var(--orange)" : i === 5 ? "var(--lime)" : "var(--ink)",
                }}
              />
            ))}
          </div>
        </div>
      </section>

      <section id="try" className="try">
        <div className="try__grid">
          <div className="panel panel--dark">
            <div className="panel__meta">
              <span className="panel__meta-dot" />
              {last ? last.speaker : "relay"}
            </div>
            <p className="panel__line">
              {last ? last.text : "Say hi, and tell me about your move."}
              <span className="cursor">|</span>
            </p>
            <div className="progress">
              <div className="progress__fill" style={{ width: `${(filledCount / rows.length) * 100}%` }} />
            </div>
          </div>

          <div className="panel panel--light">
            <div className="panel__header">
              <span className="panel__title">Your booking</span>
              <span className="panel__hint">
                {filledCount} of {rows.length}
              </span>
            </div>
            {rows.map((row) => (
              <div className="row" key={row.label}>
                <span
                  className="row__dot"
                  style={{ background: row.filled ? "var(--orange)" : "rgba(17,17,16,.14)" }}
                />
                <span className="row__label">{row.label}</span>
                <span
                  className="row__value"
                  style={{ color: row.filled ? "var(--ink)" : "rgba(17,17,16,.34)" }}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="marquee">
        <div className="marquee__track">
          {[0, 1].map((copy) => (
            <div className="marquee__group" key={copy}>
              <span>Booked in 40 seconds</span>
              <span className="marquee__star">✳</span>
              <span>Zero forms</span>
              <span className="marquee__star">✳</span>
              <span>&ldquo;shift my flat this Sunday&rdquo;</span>
              <span className="marquee__star">✳</span>
              <span>It never asks twice</span>
              <span className="marquee__star">✳</span>
              <span>&ldquo;actually, make it Marathahalli&rdquo;</span>
              <span className="marquee__star">✳</span>
              <span>Change your mind mid-sentence</span>
              <span className="marquee__star">✳</span>
              <span>&ldquo;a bed, a fridge and ten boxes&rdquo;</span>
              <span className="marquee__star">✳</span>
              <span>Nothing to download</span>
              <span className="marquee__star">✳</span>
              <span>&ldquo;third floor, there&apos;s a lift&rdquo;</span>
              <span className="marquee__star">✳</span>
              <span>It reads the booking back</span>
              <span className="marquee__star">✳</span>
            </div>
          ))}
        </div>
      </div>

      <section className="steps">
        <div className="steps__grid">
          <div>
            <div className="step__number">01</div>
            <div className="step__title">Talk.</div>
            <p className="step__body">Say the move the way you&apos;d say it to a friend.</p>
          </div>
          <div>
            <div className="step__number">02</div>
            <div className="step__title">It fills the form.</div>
            <p className="step__body">Pickup, drop, stuff, timing, truck size. All of it.</p>
          </div>
          <div>
            <div className="step__number">03</div>
            <div className="step__title">You say yes.</div>
            <p className="step__body">It reads the booking back before anything is confirmed.</p>
          </div>
        </div>
      </section>

      <section className="features">
        <div className="features__list">
          <div className="feature">
            <h2 className="feature__title">Never asks twice</h2>
            <p className="feature__body">
              It remembers every detail you&apos;ve already given. No loops, no starting over.
            </p>
          </div>
          <div className="feature">
            <h2 className="feature__title">Change your mind</h2>
            <p className="feature__body">
              Say &ldquo;actually, make it Marathahalli&rdquo; and it just updates. Mid-sentence is fine.
            </p>
          </div>
          <div className="feature">
            <h2 className="feature__title">One breath is enough</h2>
            <p className="feature__body">Everything at once or a bit at a time. Any order works.</p>
          </div>
          <div className="feature">
            <h2 className="feature__title">Nothing invented</h2>
            <p className="feature__body">
              If it isn&apos;t sure, it asks. Your booking is only what you actually said.
            </p>
          </div>
        </div>
      </section>

      <section className="cta">
        <div className="cta__panel">
          <h2 className="cta__title">
            Say it once.
            <br />
            <span className="cta__title-accent">Book it.</span>
          </h2>
          <div className="cta__actions">
            <a href="#try" className="pill-button pill-button--accent">
              Start talking
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14" />
                <path d="m12 5 7 7-7 7" />
              </svg>
            </a>
          </div>
        </div>
      </section>

      <section className="tech">
        <div className="tech__row">
          <span className="tech__row-label">Under the hood</span>
          <span>Whisper · GPT-OSS-120B structured output · Orpheus TTS</span>
          <span>FastAPI + Pydantic v2</span>
        </div>
      </section>

      <footer className="footer">
        <div className="footer__top">
          <div className="footer__brand">
            <span className="dot" />
            <span className="footer__name">Relay</span>
          </div>
          {(apiError || (!sessionId && !apiError)) && (
            <span className="footer__error mono">
              {apiError ?? (isSlowStart ? "Waking up the server, hang tight…" : "Connecting…")}
            </span>
          )}
        </div>
        <div className="footer__wordmark">Just say it</div>
      </footer>
    </div>
  );
}
