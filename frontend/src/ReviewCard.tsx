import type { BookingStateShape } from "./api";
import { sectionsFrom } from "./bookingSections";

/** The mid-conversation "is this all correct?" check -- deliberately not
 * ConfirmationModal reused with different copy. That screen means the
 * booking is done; this one means the opposite (still listening, still
 * changeable), so it gets its own visual identity -- inline in the
 * transcript panel's own slot rather than a full-screen backdrop, so the
 * mic button stays reachable and talking over it still works exactly like
 * correcting any other turn. See styles.css's "review card" block for why
 * it is lime/pending-toned where the confirmation modal is orange/done-toned.
 */

type ReviewCardProps = {
  state: BookingStateShape;
  onClose: () => void;
};

export default function ReviewCard({ state, onClose }: ReviewCardProps) {
  const sections = sectionsFrom(state);
  const notes = state.notes.map((note) => note.text);
  const assumptions = state.assumptions;

  return (
    <div className="review-card" role="status" aria-live="polite">
      <button type="button" className="review-card__close" onClick={onClose} aria-label="Dismiss">
        ✕
      </button>

      <div className="review-card__eyebrow">
        <span className="review-card__eyebrow-dot" />
        Quick check
      </div>
      <p className="review-card__prompt">Sound right?</p>
      <p className="review-card__hint">
        Say &ldquo;yes&rdquo; to confirm, or just say what to change -- no need to tap anything.
      </p>

      <div className="review-card__sections">
        {sections.map((section, i) => (
          <div
            className="review-card__section"
            key={section.title}
            style={{ animationDelay: `${0.08 + i * 0.06}s` }}
          >
            <div className="review-card__section-title">{section.title}</div>
            {section.rows.map((row) => (
              <div className="review-card__row" key={row.label}>
                <span className="review-card__row-label">{row.label}</span>
                <span className="review-card__row-value">{row.value}</span>
              </div>
            ))}
          </div>
        ))}

        {notes.length > 0 && (
          <div
            className="review-card__section"
            style={{ animationDelay: `${0.08 + sections.length * 0.06}s` }}
          >
            <div className="review-card__section-title">Notes</div>
            {notes.map((text, i) => (
              <p className="review-card__note" key={i}>
                {text}
              </p>
            ))}
          </div>
        )}

        {assumptions.length > 0 && (
          <div
            className="review-card__section review-card__section--assumed"
            style={{ animationDelay: `${0.08 + (sections.length + 1) * 0.06}s` }}
          >
            <div className="review-card__section-title">Assumed, not confirmed</div>
            {assumptions.map((assumption, i) => (
              <p className="review-card__note" key={i}>
                {assumption.note}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
