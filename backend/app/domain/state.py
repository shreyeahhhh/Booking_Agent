"""The booking state schema.

This module defines what the agent knows and how it knows it. It is the single
source of truth described in docs/architecture.md: the language model never
holds this state, it only proposes changes to it (see Patch / ExtractionResult
below), and every change flows through domain.reducer before it is accepted.

Every model here is frozen. The reducer never mutates a BookingState in place --
it builds a new one with `model_copy(update=...)`. Freezing turns an accidental
`state.pickup = x` somewhere else in the codebase into a hard TypeError instead
of a silent state-corruption bug. (Frozen only stops attribute *rebinding* --
list fields are still ordinary mutable lists, so reducer code is disciplined to
build new lists rather than call `.append()` on one held by an existing state.)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

# Classic typing.Generic, not the PEP 695 `class Field[T]:` syntax, even though
# design.md's illustrative snippet uses the latter. PEP 695 needs Python 3.12+;
# this project commits to 3.11+ (pyproject.toml), and a generics style that
# only works on the exact interpreter installed here is a landmine for
# whatever Python version Phase 4's host happens to run.
T = TypeVar("T")


def _frozen_model() -> ConfigDict:
    return ConfigDict(frozen=True)


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class FieldStatus(StrEnum):
    """How a value in the booking state came to be there.

    This is the load-bearing distinction in the whole design: a value can be
    *present* and still not count as usable. AMBIGUOUS is what lets "Kochi"
    get challenged as a drop point instead of silently accepted as one.
    """

    EMPTY = "empty"  # never mentioned
    INFERRED = "inferred"  # derived by code, awaiting user confirmation
    PROVIDED = "provided"  # the user stated it directly
    AMBIGUOUS = "ambiguous"  # stated, but not specific enough to act on
    CONFIRMED = "confirmed"  # the user explicitly affirmed it


class BookingType(StrEnum):
    HOUSE_SHIFTING = "house_shifting"
    SINGLE_ITEM = "single_item"
    BULK_GOODS = "bulk_goods"
    PARCEL = "parcel"


class GoodsCategory(StrEnum):
    FURNITURE = "furniture"
    APPLIANCES = "appliances"
    HOUSEHOLD_MIXED = "household_mixed"
    COMMERCIAL_GOODS = "commercial_goods"
    PARCEL_DOCUMENTS = "parcel_documents"
    OTHER = "other"


class TimeWindow(StrEnum):
    """Bands tile the day exactly, 06:00-24:00, with no gaps: see normalizers.py."""

    MORNING = "morning"  # 06:00-12:00
    AFTERNOON = "afternoon"  # 12:00-16:00
    EVENING = "evening"  # 16:00-20:00
    NIGHT = "night"  # 20:00-24:00


class VehicleType(StrEnum):
    TWO_WHEELER = "two_wheeler"
    THREE_WHEELER = "three_wheeler"
    TATA_ACE = "tata_ace"
    PICKUP_8FT = "pickup_8ft"
    TEMPO_14FT = "tempo_14ft"


class SizeHint(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class AmbiguityReason(StrEnum):
    CITY_LEVEL_ONLY = "city_level_only"
    VAGUE_LOCATION = "vague_location"
    RELATIVE_DATE = "relative_date"
    VAGUE_TIME = "vague_time"
    VAGUE_QUANTITY = "vague_quantity"
    UNKNOWN_ITEM = "unknown_item"
    CONFLICTING = "conflicting"


class PatchOp(StrEnum):
    SET = "set"
    CORRECT = "correct"
    APPEND = "append"
    REMOVE = "remove"
    CLEAR = "clear"


class Intent(StrEnum):
    PROVIDE_INFO = "provide_info"
    CORRECTION = "correction"
    CONFIRM = "confirm"
    REJECT = "reject"
    QUESTION = "question"
    OFF_TOPIC = "off_topic"
    UNCLEAR = "unclear"


# --------------------------------------------------------------------------
# Provenance records
# --------------------------------------------------------------------------


class Revision(BaseModel):
    """One prior value a field held, kept when a correction replaces it.

    This is what lets the UI render "Kakkanad (was: Kochi - corrected at
    turn 6)" -- see docs/architecture.md, "Corrections".
    """

    model_config = _frozen_model()

    value: object | None
    status: FieldStatus
    evidence: str | None
    turn: int | None


class Assumption(BaseModel):
    """Recorded when a clarification is abandoned after too many attempts.

    An agent that states what it guessed is more trustworthy than one that
    guesses silently -- see docs/design.md SS3.4.
    """

    model_config = _frozen_model()

    field: str
    note: str
    turn: int


class Conflict(BaseModel):
    """A patch tried to overwrite a CONFIRMED field and was rejected.

    Kept on the state itself (not just logged) so the policy can find it by
    reading state alone -- see docs/architecture.md, "Preventing redundant
    questions": the policy never scans history.
    """

    model_config = _frozen_model()

    field: str
    existing_value: object | None
    attempted_value: object | None
    turn: int


# --------------------------------------------------------------------------
# The field wrapper
# --------------------------------------------------------------------------


class Field(BaseModel, Generic[T]):
    """A single piece of booking information, plus how it was learned.

    `Field` is also the name of Pydantic's own field-configuration helper
    (`pydantic.Field(...)`), so that function is imported here as
    `PydanticField` to avoid shadowing this class.
    """

    model_config = _frozen_model()

    value: T | None = None
    status: FieldStatus = FieldStatus.EMPTY
    confidence: float = 0.0
    evidence: str | None = None
    turn: int | None = None
    clarify_attempts: int = 0
    ambiguity: AmbiguityReason | None = None
    revisions: list[Revision] = PydanticField(default_factory=list)

    @property
    def is_settled(self) -> bool:
        """False for EMPTY and AMBIGUOUS -- the two statuses that count as unfilled."""
        return self.status not in (FieldStatus.EMPTY, FieldStatus.AMBIGUOUS)


def empty_field() -> Field[T]:
    """An EMPTY Field with no value -- the default state of every slot."""
    return Field[T]()


# --------------------------------------------------------------------------
# Booking structure
# --------------------------------------------------------------------------


class Address(BaseModel):
    model_config = _frozen_model()

    raw_text: Field[str] = PydanticField(default_factory=empty_field)
    locality: Field[str] = PydanticField(default_factory=empty_field)
    city: Field[str] = PydanticField(default_factory=empty_field)
    landmark: Field[str] = PydanticField(default_factory=empty_field)
    floor: Field[int] = PydanticField(default_factory=empty_field)
    has_lift: Field[bool] = PydanticField(default_factory=empty_field)


class Item(BaseModel):
    """A single thing being moved.

    Deliberately not wrapped in Field[T]: items are managed as a collection
    (append / remove / correct-by-name), not as a single slot the policy asks
    about once. `evidence` and `turn` are kept anyway so an individual item's
    origin stays auditable, matching the rest of the state's provenance story.
    """

    model_config = _frozen_model()

    name: str
    quantity: int = 1
    size_hint: SizeHint | None = None
    evidence: str | None = None
    turn: int | None = None


class Schedule(BaseModel):
    model_config = _frozen_model()

    date: Field[str] = PydanticField(default_factory=empty_field)  # ISO "YYYY-MM-DD"
    time_window: Field[TimeWindow] = PydanticField(default_factory=empty_field)
    exact_time: Field[str] = PydanticField(default_factory=empty_field)  # "HH:MM"
    is_asap: Field[bool] = PydanticField(default_factory=empty_field)


class Goods(BaseModel):
    model_config = _frozen_model()

    category: Field[GoodsCategory] = PydanticField(default_factory=empty_field)
    items: list[Item] = PydanticField(default_factory=list)
    load_hint: Field[SizeHint] = PydanticField(default_factory=empty_field)


class Service(BaseModel):
    model_config = _frozen_model()

    vehicle_type: Field[VehicleType] = PydanticField(default_factory=empty_field)
    helpers_required: Field[int] = PydanticField(default_factory=empty_field)
    needs_packing: Field[bool] = PydanticField(default_factory=empty_field)
    needs_disassembly: Field[bool] = PydanticField(default_factory=empty_field)


class Note(BaseModel):
    model_config = _frozen_model()

    text: str
    turn: int | None = None


class BookingState(BaseModel):
    """The whole booking, and the single source of truth for the conversation."""

    model_config = _frozen_model()

    turn: int = 0
    booking_type: Field[BookingType] = PydanticField(default_factory=empty_field)
    pickup: Address = PydanticField(default_factory=Address)
    drop: Address = PydanticField(default_factory=Address)
    schedule: Schedule = PydanticField(default_factory=Schedule)
    goods: Goods = PydanticField(default_factory=Goods)
    service: Service = PydanticField(default_factory=Service)
    notes: list[Note] = PydanticField(default_factory=list)
    assumptions: list[Assumption] = PydanticField(default_factory=list)
    conflicts: list[Conflict] = PydanticField(default_factory=list)


# --------------------------------------------------------------------------
# The vocabulary of change
# --------------------------------------------------------------------------
#
# Patch and ExtractionResult describe what *any* extractor -- an LLM in
# phase 2, or a hand-written test in phase 1 -- must produce to change the
# state. They live here, not in app/llm, because they describe the state's
# own vocabulary of change, not anything about how Groq's API works. That
# keeps the dependency direction one-way: app/llm will import these from
# app/domain; app/domain never imports anything from app/llm.


class Patch(BaseModel):
    """One proposed change to a single field, produced by an extractor.

    See docs/design.md SS4.2 for the wire format this mirrors.
    """

    model_config = _frozen_model()

    op: PatchOp
    field: str  # dotted path, e.g. "pickup.locality", or "goods.items" for the list
    value: object | None = None
    previous_value: object | None = None
    confidence: float = 1.0
    evidence: str | None = None
    needs_normalization: bool = False
    ambiguity: AmbiguityReason | None = None


class ExtractionResult(BaseModel):
    """What one utterance is understood to mean."""

    model_config = _frozen_model()

    intent: Intent
    patches: list[Patch] = PydanticField(default_factory=list)
    unresolved_mentions: list[str] = PydanticField(default_factory=list)
    suggested_reply: str | None = None
