import { useEffect } from "react";

import type { BookingStateShape } from "./api";
import { sectionsFrom } from "./bookingSections";

type ConfirmationModalProps = {
  state: BookingStateShape;
  onClose: () => void;
  onStartNewBooking: () => void;
};

export default function ConfirmationModal({ state, onClose, onStartNewBooking }: ConfirmationModalProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const sections = sectionsFrom(state);
  const notes = state.notes.map((note) => note.text);
  const assumptions = state.assumptions;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="modal-card__close" onClick={onClose} aria-label="Close">
          ✕
        </button>

        <div className="modal-card__badge">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--cream)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </div>

        <h2 id="confirmation-title" className="modal-card__title">
          Booked. That&apos;s it.
        </h2>
        <p className="modal-card__subtitle">Here&apos;s everything, exactly as you said it.</p>

        <div className="modal-card__sections">
          {sections.map((section, i) => (
            <div
              className="modal-section"
              key={section.title}
              style={{ animationDelay: `${0.12 + i * 0.07}s` }}
            >
              <div className="modal-section__title">{section.title}</div>
              {section.rows.map((row) => (
                <div className="modal-row" key={row.label}>
                  <span className="modal-row__label">{row.label}</span>
                  <span className="modal-row__value">{row.value}</span>
                </div>
              ))}
            </div>
          ))}

          {notes.length > 0 && (
            <div
              className="modal-section"
              style={{ animationDelay: `${0.12 + sections.length * 0.07}s` }}
            >
              <div className="modal-section__title">Notes</div>
              {notes.map((text, i) => (
                <div className="modal-row" key={i}>
                  <span className="modal-row__value modal-row__value--full">{text}</span>
                </div>
              ))}
            </div>
          )}

          {assumptions.length > 0 && (
            <div
              className="modal-section modal-section--assumed"
              style={{ animationDelay: `${0.12 + (sections.length + 1) * 0.07}s` }}
            >
              <div className="modal-section__title">Assumed, not confirmed</div>
              {assumptions.map((assumption, i) => (
                <div className="modal-row" key={i}>
                  <span className="modal-row__value modal-row__value--full">{assumption.note}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="modal-card__actions">
          <button type="button" className="pill-button modal-card__new-booking" onClick={onStartNewBooking}>
            Book another move
          </button>
          <button type="button" className="pill-button pill-button--accent modal-card__done" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
