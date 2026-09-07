/** Grouped, human-readable rows built from the full BookingStateShape --
 * shared by ConfirmationModal (the terminal "booked" screen) and ReviewCard
 * (the mid-conversation "is this all correct?" check), so the two screens
 * that both summarise the whole booking cannot drift into two different
 * readings of the same state the way App.tsx's live panel and the old
 * inline confirmation text once could.
 */

import type { BookingStateShape } from "./api";
import { displayValueOrNull, formatDateLong, formatFloor, formatItems, prettify, withHeardAs } from "./format";

export type Row = { label: string; value: string };
export type Section = { title: string; rows: Row[] };

function addressSection(title: string, address: BookingStateShape["pickup"]): Section {
  const rows: Row[] = [];
  // Deliberately not displayValueOrNull: a corrected locality is either a
  // spoken mishearing fix (withHeardAs below already shows that divergence)
  // or a map-pin overwrite of a stale/wrong earlier value -- in neither
  // case does re-surfacing the discarded old value as "(was: ...)" add
  // anything, and for a map pin specifically the old value was often
  // exactly the kind of hallucinated place name this app exists to correct.
  const locality = address.locality.value;
  if (locality) rows.push({ label: "Location", value: withHeardAs(locality, address.raw_text.value) });
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

export function sectionsFrom(state: BookingStateShape): Section[] {
  return [
    addressSection("Pickup", state.pickup),
    addressSection("Drop-off", state.drop),
    scheduleSection(state.schedule),
    serviceSection(state.goods, state.service),
  ].filter((section) => section.rows.length > 0);
}
