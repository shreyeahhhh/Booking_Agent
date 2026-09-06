/** Mirrors app/api/routes.py's Pydantic response models exactly -- see
 * SessionResponse / TurnResponse there. Calling by relative path only
 * (never a hardcoded origin) is deliberate: see docs/architecture.md's
 * CORS/HTTPS note for why that single choice matters for both.
 */

export type SessionResponse = {
  session_id: string;
  agent_text: string;
  audio_chunks: string[];
  tts_fallback: boolean;
};

/** One Field[T] as BookingState.model_dump(mode="json") serialises it.
 * Only the keys this UI actually reads are typed -- the real shape carries
 * confidence/evidence/turn/etc. too, which the live state panel does not
 * need. */
export type FieldValue<T> = {
  value: T | null;
  status: "empty" | "provided" | "inferred" | "ambiguous" | "confirmed";
};

export type BookingStateShape = {
  pickup: { locality: FieldValue<string> };
  drop: { locality: FieldValue<string> };
  goods: { items: Array<{ name: string; quantity: number }> };
  schedule: { date: FieldValue<string>; time_window: FieldValue<string> };
  service: { vehicle_type: FieldValue<string> };
};

export type TurnResponse = {
  user_text: string;
  agent_text: string;
  audio_chunks: string[];
  tts_fallback: boolean;
  phase: string;
  done: boolean;
  state: BookingStateShape;
};

async function parseOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
  }
  return response.json() as Promise<T>;
}

export function createSession(): Promise<SessionResponse> {
  return fetch("/api/session", { method: "POST" }).then(parseOrThrow<SessionResponse>);
}

export function postTurn(sessionId: string, audio: Blob, filename: string): Promise<TurnResponse> {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("audio", audio, filename);
  return fetch("/api/turn", { method: "POST", body: form }).then(parseOrThrow<TurnResponse>);
}
