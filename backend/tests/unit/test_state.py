"""Field[T] provenance and JSON round-tripping -- MASTER_PLAN.md step 1.1."""

import pytest
from pydantic import ValidationError

from app.domain.state import (
    Address,
    BookingState,
    Field,
    FieldStatus,
    Revision,
)


def test_empty_field_is_not_settled():
    f = Field[str]()
    assert f.status == FieldStatus.EMPTY
    assert f.value is None
    assert f.is_settled is False


def test_ambiguous_field_is_not_settled():
    f = Field[str](value="Kochi", status=FieldStatus.AMBIGUOUS)
    assert f.is_settled is False


def test_provided_inferred_confirmed_are_settled():
    for status in (FieldStatus.PROVIDED, FieldStatus.INFERRED, FieldStatus.CONFIRMED):
        assert Field[str](value="x", status=status).is_settled is True


def test_field_is_frozen():
    f = Field[str](value="Koramangala")
    with pytest.raises(ValidationError):
        f.value = "Whitefield"


def test_booking_state_is_frozen():
    s = BookingState()
    with pytest.raises(ValidationError):
        s.turn = 99


def test_address_defaults_to_all_empty_fields():
    addr = Address()
    assert addr.locality.status == FieldStatus.EMPTY
    assert addr.floor.status == FieldStatus.EMPTY
    assert addr.has_lift.status == FieldStatus.EMPTY


def test_booking_state_round_trips_through_json():
    s = BookingState()
    s = s.model_copy(
        update={
            "pickup": s.pickup.model_copy(
                update={
                    "locality": Field[str](
                        value="Koramangala",
                        status=FieldStatus.PROVIDED,
                        confidence=0.95,
                        evidence="from Koramangala",
                        turn=1,
                        revisions=[
                            Revision(
                                value="Koramanagala",
                                status=FieldStatus.PROVIDED,
                                evidence=None,
                                turn=0,
                            )
                        ],
                    )
                }
            )
        }
    )

    restored = BookingState.model_validate_json(s.model_dump_json())

    assert restored.pickup.locality.value == "Koramangala"
    assert restored.pickup.locality.confidence == 0.95
    assert restored.pickup.locality.evidence == "from Koramangala"
    assert len(restored.pickup.locality.revisions) == 1
    assert restored.pickup.locality.revisions[0].value == "Koramanagala"


def test_model_copy_never_touches_the_original():
    s = BookingState()
    s2 = s.model_copy(update={"turn": 5})
    assert s.turn == 0
    assert s2.turn == 5
