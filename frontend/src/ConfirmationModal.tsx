import { useEffect } from "react";

import type { BookingStateShape } from "./api";
import {
  displayValueOrNull,
  formatDateLong,
  formatFloor,
  formatItems,
  prettify,
} from "./format";

type Row = { label: string; value: string };
type Section = { title: string; rows: Row[] };

function addressSection(title: string, address: BookingStateShape["pickup"]): Section {
  const rows: Row[] = [];
  const locality = displayValueOrNull(address.locality, (v) => v);
  if (locality) rows.push({ label: "Location", value: locality });
  const floor = displayValueOrNull(address.floor, formatFloor);
  if (floor) rows.push({ label: "Floor", value: floor });
  const lift = displayValueOrNull(address.has_lift, (v) => (v ? "Yes" : "No"));
  if (lift) rows.push({ label: "Lift", value: lift });
  return { title, rows };
}

function scheduleSection(schedule: BookingStateShape["schedule"]): Section {
  const rows: Row[] = [];
  if (schedule.is_asap.value) {
    rows.push({ label: "When", value: "As soon as possible" });
    return { title: "Schedule", rows };
  }
  const date = displayValueOrNull(schedule.date, formatDateLong);
  if (date) rows.push({ label: "Date", value: date });
  const time = displayValueOrNull(schedule.time_window, prettify);
  if (time) rows.push({ label: "Time", value: time });
  const exact = displayValueOrNull(schedule.exact_time, (v) => v);
  if (exact) rows.push({ label: "Exact time", value: exact });
  return { title: "Schedule", rows };
}

function serviceSection(
  goods: BookingStateShape["goods"],
  service: BookingStateShape["service"],
): Section {
  const rows: Row[] = [];
  const items = formatItems(goods.items);
  if (items) rows.push({ label: "Items", value: items });
  const vehicle = displayValueOrNull(service.vehicle_type, prettify);
  if (vehicle) rows.push({ label: "Truck", value: vehicle });
  const helpers = displayValueOrNull(service.helpers_required, (v) => String(v));
  if (helpers) rows.push({ label: "Helpers", value: helpers });
  const packing = displayValueOrNull(service.needs_packing, (v) => (v ? "Yes" : "No"));
  if (packing) rows.push({ label: "Packing help", value: packing });
  const disassembly = displayValueOrNull(service.needs_disassembly, (v) => (v ? "Yes" : "No"));
  if (disassembly) rows.push({ label: "Disassembly", value: disassembly });
  return { title: "The move", rows };
}

function sectionsFrom(state: BookingStateShape): Section[] {
  return [
    addressSection("Pickup", state.pickup),
    addressSection("Drop-off", state.drop),
    scheduleSection(state.schedule),
    serviceSection(state.goods, state.service),
  ].filter((section) => section.rows.length > 0);
}

type ConfirmationModalProps = {
  state: BookingStateShape;
  onClose: () => void;
};

export default function ConfirmationModal({ state, onClose }: ConfirmationModalProps) {
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

        <button type="button" className="pill-button pill-button--accent modal-card__done" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
