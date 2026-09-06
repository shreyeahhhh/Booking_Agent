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

export type FieldStatus = "empty" | "provided" | "inferred" | "ambiguous" | "confirmed";

/** One domain.state.Revision as JSON: a value a field held before a
 * correction replaced it. `evidence`/`turn` are carried for completeness
 * (mirroring the backend model exactly) even though the live state panel's
 * "(was: X)" rendering only needs `value`. */
export type Revision<T> = {
  value: T | null;
  status: FieldStatus;
  evidence: string | null;
  turn: number | null;
};

/** One Field[T] as BookingState.model_dump(mode="json") serialises it.
 * Only the keys this UI actually reads are typed -- the real shape also
 * carries confidence/clarify_attempts/ambiguity, which the live state panel
 * does not need. `revisions` is what lets a correction render as
 * "Kakkanad (was: Kochi)" instead of silently overwriting the old value --
 * see MASTER_PLAN.md step 3.6. */
export type FieldValue<T> = {
  value: T | null;
  status: FieldStatus;
  revisions: Array<Revision<T>>;
};

/** app/domain/state.Address as JSON. */
export type AddressShape = {
  locality: FieldValue<string>;
  floor: FieldValue<number>;
  has_lift: FieldValue<boolean>;
};

/** app/domain/state.Item as JSON -- a plain object, not Field[T]-wrapped:
 * the backend manages the item list as a collection (append/remove/correct
 * by name), not a single slot, so there is no single "was: X" to show. */
export type ItemShape = { name: string; quantity: number };

/** app/domain/state.Note as JSON. */
export type NoteShape = { text: string; turn: number | null };

/** app/domain/state.Assumption as JSON -- recorded when a clarification is
 * abandoned after too many attempts (domain/policy.py's
 * _give_up_on_exhausted). Surfacing these in the confirmation modal is the
 * same "nothing invented silently" promise this project's copy already
 * makes, applied to the one summary screen where it matters most. */
export type AssumptionShape = { field: string; note: string; turn: number };

/** The full app/domain/state.BookingState as JSON -- every field the
 * confirmation modal shows, not just the five the always-visible live
 * panel condenses to while a booking is still in progress. */
export type BookingStateShape = {
  pickup: AddressShape;
  drop: AddressShape;
  goods: { items: ItemShape[] };
  schedule: {
    date: FieldValue<string>;
    time_window: FieldValue<string>;
    exact_time: FieldValue<string>;
    is_asap: FieldValue<boolean>;
  };
  service: {
    vehicle_type: FieldValue<string>;
    helpers_required: FieldValue<number>;
    needs_packing: FieldValue<boolean>;
    needs_disassembly: FieldValue<boolean>;
  };
  notes: NoteShape[];
  assumptions: AssumptionShape[];
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
