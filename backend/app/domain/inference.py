"""Guessing the vehicle, helper count, and goods category from the item list.

A user moving a sofa does not know what a Tata Ace is. Asking "what vehicle do
you want?" is a form; proposing "a Tata Ace should handle that - sound right?"
is an assistant -- see docs/design.md SS3.4. This module produces that guess.
The vehicle/helper guess never decides anything on its own: the reducer always
sets it to INFERRED, never CONFIRMED, so the user is always asked to check it.

`infer_category` exists for a sharper reason than convenience: it was added
after a live conversation test caught goods.category getting permanently
stuck. The extractor's own Rule 1 ("never infer a value the user did not
state") correctly refuses to guess "furniture" just because the user said
"sofa" -- categorising an item *is* a form of inference that rule is right to
forbid the model from doing. But almost no one naturally says the word
"furniture" out loud; they name the things they're moving. A category that can
only ever be filled by the user saying a category word gets stuck forever,
and everything gated on it (floor questions, packing) never engages. Moving
the classification into deterministic code sidesteps the rule entirely: this
is not the model inferring an unstated fact, it is code classifying a fact
the user already gave explicitly (the item name).

Both guesses are a plain keyword lookup, not a model. That is deliberate:
they have to be explainable in one sentence ("a sofa and three cupboards is
about six load units, which maps to a Tata Ace"; "a sofa is furniture"), and
a lookup table is the simplest thing that gives an honest, defensible answer
-- see MASTER_PLAN.md 1.4, "pure function, no AI".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.state import GoodsCategory, Item, VehicleType

# Rough load units per item, loosely modelled on real weight/bulk for a
# Porter-style intra-city move. Not a certified weight table -- a deliberately
# simple heuristic, documented as such in README's "Assumptions and
# limitations". Unrecognised items fall back to _DEFAULT_ITEM_LOAD.
_ITEM_LOAD_KEYWORDS: dict[str, int] = {
    "fridge": 4,
    "refrigerator": 4,
    "washing machine": 4,
    "almirah": 3,
    "wardrobe": 3,
    "bed": 3,
    "sofa": 3,
    "cupboard": 2,
    "dining table": 2,
    "table": 2,
    "desk": 2,
    "chair": 1,
    "box": 1,
    "carton": 1,
    "suitcase": 1,
    "document": 0,
    "parcel": 0,
}
_DEFAULT_ITEM_LOAD = 1

# Upper bound of total load units each vehicle comfortably handles, checked in
# order. The first tier whose ceiling is not exceeded is chosen.
#
# Calibrated against the two worked examples used throughout the docs
# (design.md SS3.4 and SS5.6): a bare "sofa" (load 3) and "1 sofa, 3 cupboards"
# (load 3 + 2*3 = 9) must both land on TATA_ACE, matching what the README and
# design doc already tell a reader to expect -- these are not arbitrary
# thresholds, they are fitted to the examples this project committed to.
_MAX_HELPERS = 4
_VEHICLE_TIERS: list[tuple[int, VehicleType]] = [
    (1, VehicleType.TWO_WHEELER),  # a single small parcel/box
    (2, VehicleType.THREE_WHEELER),  # a couple of light items
    (10, VehicleType.TATA_ACE),  # a sofa, or a small household load
    (20, VehicleType.PICKUP_8FT),  # several furniture pieces
    (10_000, VehicleType.TEMPO_14FT),  # full house-shifting scale
]


@dataclass(frozen=True)
class InferredService:
    vehicle_type: VehicleType
    helpers_required: int
    total_load: int


def _load_for(item: Item) -> int:
    name = item.name.lower()
    for keyword, load in _ITEM_LOAD_KEYWORDS.items():
        if keyword in name:
            return load * max(item.quantity, 1)
    return _DEFAULT_ITEM_LOAD * max(item.quantity, 1)


def infer_vehicle_and_helpers(items: list[Item]) -> InferredService | None:
    """Guess a vehicle and helper count from the item list.

    Returns None when there is nothing to infer from yet (no items) -- an
    empty guess would be worse than no guess, and domain/completeness.py only
    treats an INFERRED field as awaiting confirmation once it actually has one.
    """
    if not items:
        return None

    total_load = sum(_load_for(item) for item in items)

    vehicle = _VEHICLE_TIERS[-1][1]
    for ceiling, tier_vehicle in _VEHICLE_TIERS:
        if total_load <= ceiling:
            vehicle = tier_vehicle
            break

    # A heavy appliance justifies at least one helper even alone -- one fridge
    # is a two-person job regardless of how little else is moving. Otherwise
    # helpers scale with total load, capped at a realistic maximum: even a
    # full house-shifting job rarely brings more than a handful of loaders.
    has_heavy_appliance = any(
        load >= 4
        for item in items
        for keyword, load in _ITEM_LOAD_KEYWORDS.items()
        if keyword in item.name.lower()
    )
    base_helpers = 1 if total_load >= 3 else 0
    scaled_helpers = total_load // 4
    helpers = min(_MAX_HELPERS, max(base_helpers, scaled_helpers, 1 if has_heavy_appliance else 0))

    return InferredService(vehicle, helpers, total_load)


# A separate keyword table from _ITEM_LOAD_KEYWORDS on purpose: load and
# category answer different questions (how heavy vs. what kind), and an item
# can matter for one without cleanly fitting the other (e.g. "box" carries
# load but says nothing about category). commercial_goods is intentionally
# absent -- it describes the *nature* of a shipment (business inventory, for
# instance), not something a generic item name reliably signals; it stays a
# category a user can still state explicitly, just not one this function
# guesses at.
_FURNITURE_KEYWORDS = (
    "sofa",
    "bed",
    "wardrobe",
    "almirah",
    "cupboard",
    "table",
    "desk",
    "chair",
    "shelf",
    "bookshelf",
)
_APPLIANCE_KEYWORDS = (
    "fridge",
    "refrigerator",
    "washing machine",
    "air conditioner",
    "ac",
    "television",
    "tv",
    "microwave",
    "oven",
    "geyser",
    "water heater",
)
_PARCEL_KEYWORDS = ("document", "documents", "parcel", "envelope", "papers", "package", "courier")


def infer_category(items: list[Item]) -> GoodsCategory | None:
    """Classify the goods category from what is actually in the item list.

    Returns None with no items to classify, the same "nothing to guess from
    yet" contract as infer_vehicle_and_helpers.
    """
    if not items:
        return None

    has_furniture = any(
        keyword in item.name.lower() for item in items for keyword in _FURNITURE_KEYWORDS
    )
    has_appliance = any(
        keyword in item.name.lower() for item in items for keyword in _APPLIANCE_KEYWORDS
    )
    has_parcel = any(keyword in item.name.lower() for item in items for keyword in _PARCEL_KEYWORDS)

    if has_furniture and has_appliance:
        return GoodsCategory.HOUSEHOLD_MIXED
    if has_furniture:
        return GoodsCategory.FURNITURE
    if has_appliance:
        return GoodsCategory.APPLIANCES
    if has_parcel:
        return GoodsCategory.PARCEL_DOCUMENTS
    return GoodsCategory.OTHER
