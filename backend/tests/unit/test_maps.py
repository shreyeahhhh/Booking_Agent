"""Google Maps link parsing -- pure and deterministic except for redirect
following, which is exercised with `httpx.MockTransport` rather than a real
`goo.gl` link: there is no real vendor contract to verify here (unlike the
Cartesia/Groq `_live.py` files), just standard HTTP redirect semantics, and
a specific short link would only add flakiness as it inevitably rots.
"""

import httpx
import pytest

from app.services.maps import ParsedLocation, resolve_maps_link, reverse_geocode


@pytest.fixture
def client() -> httpx.AsyncClient:
    # Never actually called for a non-short-link URL -- a real (unopened)
    # client is simpler than a mock nothing needs to invoke.
    return httpx.AsyncClient()


async def test_extracts_coordinates_from_a_place_links_map_centre(client):
    url = "https://www.google.com/maps/place/Koramangala/@12.9352,77.6146,15z/data=!3m1!4b1"
    assert await resolve_maps_link(client, url) == ParsedLocation(12.9352, 77.6146)


async def test_extracts_coordinates_from_a_bare_query(client):
    url = "https://maps.google.com/maps?q=12.9716,77.5946"
    assert await resolve_maps_link(client, url) == ParsedLocation(12.9716, 77.5946)


async def test_extracts_coordinates_from_an_embedded_data_blob(client):
    url = "https://www.google.com/maps/place/x/data=!4m6!3m5!1s0x0!8m2!3d12.9352!4d77.6146!16s"
    assert await resolve_maps_link(client, url) == ParsedLocation(12.9352, 77.6146)


async def test_handles_negative_coordinates(client):
    url = "https://www.google.com/maps/@-33.8688,151.2093,15z"
    assert await resolve_maps_link(client, url) == ParsedLocation(-33.8688, 151.2093)


async def test_a_place_link_with_no_embedded_coordinates_is_unusable(client):
    """Not every Maps link carries coordinates -- a plain place-name link
    with none of the three known shapes present must not crash or guess."""
    url = "https://www.google.com/maps/place/Koramangala,+Bengaluru"
    assert await resolve_maps_link(client, url) is None


async def test_a_non_maps_url_is_unusable(client):
    assert await resolve_maps_link(client, "https://example.com/not-a-map") is None


async def test_garbage_input_is_unusable_not_an_error(client):
    assert await resolve_maps_link(client, "not a url at all") is None


async def test_a_shortened_link_is_resolved_via_redirect_before_parsing():
    """`follow_redirects=True` makes httpx re-request the `Location` target
    through this same handler, so it must answer both hops: the redirect
    itself, then the destination the redirect points to."""
    destination = "https://www.google.com/maps/@12.9352,77.6146,15z"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://maps.app.goo.gl/AbCdEfG":
            return httpx.Response(301, headers={"Location": destination})
        assert str(request.url) == destination
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_maps_link(client, "https://maps.app.goo.gl/AbCdEfG")
    assert result == ParsedLocation(12.9352, 77.6146)


async def test_a_shortened_link_that_fails_to_resolve_is_unusable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_maps_link(client, "https://goo.gl/maps/deadlink")
    assert result is None


def test_as_locality_value_formats_to_five_decimal_places():
    assert ParsedLocation(12.93521234, 77.6).as_locality_value() == "12.93521, 77.60000"


def test_as_maps_url_round_trips_to_a_plain_google_maps_link():
    assert (
        ParsedLocation(12.9352, 77.6146).as_maps_url()
        == "https://www.google.com/maps?q=12.9352,77.6146"
    )


# --- reverse_geocode ------------------------------------------------------


def _nominatim_client(address: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "nominatim.openstreetmap.org" in str(request.url)
        # Nominatim's own usage policy requires an identifying User-Agent --
        # regression coverage for not silently dropping it and risking the
        # app getting blocked.
        assert "User-Agent" in request.headers
        return httpx.Response(200, json={"address": address})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_reverse_geocode_prefers_the_most_specific_locality_field():
    client = _nominatim_client({"suburb": "Koramangala", "city": "Bengaluru", "state": "Karnataka"})
    name = await reverse_geocode(client, ParsedLocation(12.9352, 77.6146))
    assert name == "Koramangala, Bengaluru"


async def test_reverse_geocode_omits_the_city_when_it_equals_the_locality():
    """A village/town point where Nominatim's "city" field just repeats the
    same name would otherwise read as "Kottayam, Kottayam"."""
    client = _nominatim_client({"town": "Kottayam", "city": "Kottayam"})
    name = await reverse_geocode(client, ParsedLocation(9.5916, 76.5222))
    assert name == "Kottayam"


async def test_reverse_geocode_falls_back_through_the_field_priority_order():
    """No suburb/neighbourhood/quarter/village/town at all -- falls all the
    way to "city", the coarsest field this app will still show."""
    client = _nominatim_client({"city": "Bengaluru", "state": "Karnataka"})
    name = await reverse_geocode(client, ParsedLocation(12.9352, 77.6146))
    assert name == "Bengaluru"


async def test_reverse_geocode_returns_none_when_no_usable_address_field_exists():
    client = _nominatim_client({"state": "Karnataka", "country": "India"})
    assert await reverse_geocode(client, ParsedLocation(12.9352, 77.6146)) is None


async def test_reverse_geocode_returns_none_on_a_network_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await reverse_geocode(client, ParsedLocation(12.9352, 77.6146)) is None


async def test_reverse_geocode_returns_none_on_a_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await reverse_geocode(client, ParsedLocation(12.9352, 77.6146)) is None
