/** Shared value formatters -- used by both the always-visible live state
 * panel (App.tsx) and the final booking confirmation modal
 * (ConfirmationModal.tsx), so a corrected value reads identically wherever
 * it appears rather than drifting into two slightly different renderings.
 */

import type { FieldValue } from "./api";

export function prettify(raw: string | null): string {
  if (!raw) return "";
  return raw
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Weekday + short date, e.g. "Mon, 7 Sep" -- for the always-visible panel,
 * where space is tight. */
export function formatDate(iso: string | null): string {
  if (!iso) return "";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/** Full weekday + date, e.g. "Monday, 7 September" -- for the confirmation
 * modal, where this is the one place written precision matters more than
 * brevity (mirrors the backend's own format_date_short/format_date_full
 * split in conversation/templates.py). */
export function formatDateLong(iso: string | null): string {
  if (!iso) return "";
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

export function formatFloor(floor: number | null): string {
  if (floor === null) return "";
  if (floor === 0) return "Ground floor";
  const suffix = floor % 10 === 1 && floor !== 11 ? "st" : floor % 10 === 2 && floor !== 12 ? "nd" : floor % 10 === 3 && floor !== 13 ? "rd" : "th";
  return `${floor}${suffix} floor`;
}

export function formatItems(items: Array<{ name: string; quantity: number }>): string {
  return items
    .map((item) => (item.quantity > 1 ? `${item.quantity} ${item.name}` : item.name))
    .join(", ");
}

/** A field's current value, formatted, plus " (was: <previous>)" when
 * `revisions` shows it was corrected -- MASTER_PLAN.md step 3.6. `revisions`
 * is append-only (domain/reducer.py's `_write_scalar`), so the *last* entry
 * is the value immediately before the current one, not the first-ever
 * value. Both the current and previous value go through the same `format`
 * function, so a corrected vehicle type reads "Tata Ace (was: Mini Truck)",
 * not a raw enum value next to a prettified one. Returns `placeholder` when
 * nothing has been said yet -- for the always-visible live panel, where an
 * empty field is still a row that needs to prompt for something. */
export function displayValue<T>(
  field: FieldValue<T>,
  format: (value: T) => string,
  placeholder: string,
): string {
  if (field.value === null) return placeholder;
  const current = format(field.value);
  const previous = field.revisions.at(-1);
  if (!previous || previous.value === null) return current;
  return `${current} (was: ${format(previous.value)})`;
}

/** Same correction-aware formatting as `displayValue`, but returns `null`
 * for an empty field instead of a placeholder -- for the confirmation
 * modal, where a field nobody ever mentioned (e.g. no lift question ever
 * came up) should simply not appear as a row at all, rather than showing
 * as if it were still an open question. */
export function displayValueOrNull<T>(
  field: FieldValue<T>,
  format: (value: T) => string,
): string | null {
  if (field.value === null) return null;
  return displayValue(field, format, "");
}
