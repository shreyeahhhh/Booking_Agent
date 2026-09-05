import { useEffect, useState } from "react";

/** Mirrors HealthResponse in backend/app/api/routes.py. */
type Health = {
  status: string;
  llm_configured: boolean;
  llm_model: string;
};

/**
 * Phase 0 shell. Its only job is to prove the frontend can reach the backend
 * through the dev proxy, and to show at a glance whether the API key is wired
 * up -- the single most common reason a deployment looks broken.
 *
 * The voice interface replaces this in phase 3.
 */
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch("/api/health")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Health>;
      })
      .then((data) => {
        if (!cancelled) setHealth(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <header>
        <h1>Voice Booking Agent</h1>
        <p className="sub">
          Porter-style requirement gathering. The LLM is a sensor, not a controller.
        </p>
      </header>

      <section className="card">
        <h2>Backend</h2>

        {error && <p className="bad">Cannot reach the API — {error}</p>}
        {!error && !health && <p className="muted">Checking…</p>}

        {health && (
          <dl>
            <dt>Status</dt>
            <dd className="good">{health.status}</dd>

            <dt>LLM model</dt>
            <dd className="mono">{health.llm_model}</dd>

            <dt>API key</dt>
            <dd className={health.llm_configured ? "good" : "bad"}>
              {health.llm_configured ? "configured" : "not configured"}
            </dd>
          </dl>
        )}
      </section>

      <footer className="muted">
        Phase 0 scaffold — the voice interface arrives in phase 3.
      </footer>
    </main>
  );
}
