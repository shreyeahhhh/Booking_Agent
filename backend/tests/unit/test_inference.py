"""Vehicle and helper-count inference -- MASTER_PLAN.md step 1.4.

The two calibration cases (bare sofa; sofa + 3 cupboards) are load-bearing:
design.md SS3.4 and SS5.6 both commit to these exact outcomes in prose, so a
regression here would make the documentation wrong, not just the code.
"""

from app.domain.inference import infer_category, infer_vehicle_and_helpers
from app.domain.state import GoodsCategory, Item, VehicleType


def test_no_items_means_no_guess():
    assert infer_vehicle_and_helpers([]) is None


def test_bare_sofa_infers_tata_ace():
    result = infer_vehicle_and_helpers([Item(name="sofa", quantity=1)])
    assert result.vehicle_type == VehicleType.TATA_ACE


def test_sofa_and_three_cupboards_matches_the_design_doc_worked_example():
    items = [Item(name="sofa", quantity=1), Item(name="cupboard", quantity=3)]
    result = infer_vehicle_and_helpers(items)
    assert result.vehicle_type == VehicleType.TATA_ACE
    assert result.helpers_required == 2


def test_a_couple_of_boxes_needs_the_smallest_vehicle_and_no_help():
    result = infer_vehicle_and_helpers([Item(name="box", quantity=2)])
    assert result.vehicle_type in (VehicleType.TWO_WHEELER, VehicleType.THREE_WHEELER)
    assert result.helpers_required == 0


def test_a_single_heavy_appliance_always_gets_at_least_one_helper():
    result = infer_vehicle_and_helpers([Item(name="fridge", quantity=1)])
    assert result.helpers_required >= 1


def test_large_load_never_exceeds_the_realistic_helper_cap():
    items = [
        Item(name="bed", quantity=2),
        Item(name="wardrobe", quantity=2),
        Item(name="fridge", quantity=1),
        Item(name="washing machine", quantity=1),
        Item(name="sofa", quantity=2),
        Item(name="box", quantity=15),
    ]
    result = infer_vehicle_and_helpers(items)
    assert result.vehicle_type == VehicleType.TEMPO_14FT
    assert result.helpers_required <= 4


def test_unrecognised_item_names_still_produce_a_reasonable_guess():
    """An item name with no keyword match must not crash or return nothing."""
    result = infer_vehicle_and_helpers([Item(name="a mysterious large crate", quantity=1)])
    assert result is not None
    assert result.vehicle_type is not None


# --- category ----------------------------------------------------------


def test_infer_category_returns_none_with_no_items():
    assert infer_category([]) is None


def test_infer_category_single_furniture_item():
    assert infer_category([Item(name="sofa")]) == GoodsCategory.FURNITURE


def test_infer_category_single_appliance_item():
    assert infer_category([Item(name="fridge")]) == GoodsCategory.APPLIANCES


def test_infer_category_mixed_furniture_and_appliances_is_household_mixed():
    assert infer_category([Item(name="sofa"), Item(name="fridge")]) == GoodsCategory.HOUSEHOLD_MIXED


def test_infer_category_parcel_keywords():
    assert infer_category([Item(name="documents")]) == GoodsCategory.PARCEL_DOCUMENTS
    assert infer_category([Item(name="a small parcel")]) == GoodsCategory.PARCEL_DOCUMENTS


def test_infer_category_unrecognised_item_is_other_not_none():
    """OTHER, not None: there is something to classify, just nothing this
    heuristic recognises -- an honest "we don't know" category, not a
    refusal to answer that would leave the field looking empty."""
    assert infer_category([Item(name="a mysterious large crate")]) == GoodsCategory.OTHER
