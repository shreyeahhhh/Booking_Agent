import { useState } from "react";

import { submitLocationLink } from "./api";
import type { TurnResponse } from "./api";

/** The "paste an exact address instead" affordance next to the From/To
 * rows. Voice stays the primary way to answer -- this is a narrow,
 * opt-in escape hatch for the one case speech genuinely cannot do well:
 * an exact point, verbatim, with none of the STT-mishearing risk a spoken
 * locality name carries. Submitting reuses the same TurnResponse shape a
 * voice turn produces (api/routes.py's `submit_location_link` mirrors
 * `/turn`'s own response), so the caller applies the result identically
 * either way -- see App.tsx's `applyTurn`.
 */

type LocationLinkInputProps = {
  field: "pickup" | "drop";
  sessionId: string | null;
  onResult: (response: TurnResponse) => void;
  onError: (message: string) => void;
};

export default function LocationLinkInput({ field, sessionId, onResult, onError }: LocationLinkInputProps) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        className="location-link__toggle"
        onClick={() => setOpen(true)}
        aria-label={`Paste a Google Maps link for the ${field === "pickup" ? "pickup" : "drop-off"}`}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
          <circle cx="12" cy="10" r="3" />
        </svg>
        Paste a map link
      </button>
    );
  }

  const submit = () => {
    if (!sessionId || !url.trim() || submitting) return;
    setSubmitting(true);
    submitLocationLink(sessionId, field, url.trim())
      .then((response) => {
        onResult(response);
        setUrl("");
        setOpen(false);
      })
      .catch(() => onError("Couldn't reach the booking service. Try again."))
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="location-link">
      <input
        type="url"
        inputMode="url"
        className="location-link__input"
        placeholder="Paste a Google Maps link"
        value={url}
        autoFocus
        disabled={submitting}
        onChange={(event) => setUrl(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") submit();
          if (event.key === "Escape") setOpen(false);
        }}
      />
      <button
        type="button"
        className="location-link__submit"
        onClick={submit}
        disabled={submitting || !url.trim()}
      >
        {submitting ? "…" : "Set"}
      </button>
      <button
        type="button"
        className="location-link__cancel"
        onClick={() => setOpen(false)}
        aria-label="Cancel"
        disabled={submitting}
      >
        ✕
      </button>
    </div>
  );
}
