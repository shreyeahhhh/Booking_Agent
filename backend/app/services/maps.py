"""Parsing a Google Maps link into coordinates.

Deliberately not an LLM concern -- this is not language understanding, it is
structured URL parsing, so it stays in exactly the kind of deterministic code
this project's whole thesis says should own anything that is not "what did
the user mean" (see docs/architecture.md's "Thesis"). A dropped pin is also
strictly more precise than a spoken locality name ever can be, which is the
actual point of offering it: it sidesteps the entire class of STT-mishearing
problem this project has spent so much effort mitigating for spoken place
names, for the user who has an exact address in hand and would rather not
rely on speech recognition for it at all.

Reverse geocoding (coordinates -> a human-readable name) uses Nominatim,
OpenStreetMap's free geocoding service: no API key, no cost, matching this
project's stance on every other vendor choice (Cartesia over a paid TTS,
Groq's free tier). It is genuinely best-effort -- `reverse_geocode` returns
None on any failure, and the caller falls back to plain coordinates, the
same "a missing nice-to-have degrades the experience, not the correctness
of what was pinned" contract `services/tts.py` already has for Cartesia
falling back to browser speechSynthesis. Nominatim's own usage policy asks
for a maximum of 1 request/second and an identifying User-Agent for
anything beyond casual, non-bulk use; a user-initiated pin-drop is exactly
that, not a bulk pipeline, so both are satisfied by construction here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

_NOMINATIM_USER_AGENT = "relay-voice-booking-agent/1.0 (technical-assessment demo)"

# Shortened Maps links do not carry coordinates themselves -- the real URL
# only exists after following the redirect. Matched by exact host or as a
# subdomain (some locales serve from e.g. "maps.app.goo.gl" behind a CDN
# that echoes a slightly different host on redirect) rather than a single
# hardcoded string.
_SHORT_LINK_HOSTS = ("goo.gl", "maps.app.goo.gl")

# Three coordinate shapes actually seen in real Google Maps URLs, tried in
# order: the map's centre on a "place" link ("@lat,lng,zoom"), a bare
# coordinate query ("?q=lat,lng"), and the embedded data blob some place
# links carry ("!3d{lat}!4d{lng}"). A link matching none of these (a place
# name with no embedded coordinates, or not a Maps link at all) is treated
# as unusable, not an error -- see resolve_maps_link's return contract.
_COORD_PATTERNS = (
    re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)"),
    re.compile(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)(?:&|$)"),
    re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)"),
)


@dataclass(frozen=True)
class ParsedLocation:
    latitude: float
    longitude: float

    def as_locality_value(self) -> str:
        """What gets written to `Address.locality` -- see routes.py. Fixed
        5 decimal places (~1.1m of precision, plenty for a pickup point) so
        the value is stable and comparable rather than however many digits
        happened to be in the source URL."""
        return f"{self.latitude:.5f}, {self.longitude:.5f}"

    def as_maps_url(self) -> str:
        """A plain, always-resolvable link back to this point -- for the UI
        to offer "view on map" without needing to keep the original
        (possibly shortened, possibly expiring) URL the user pasted."""
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"


def _is_short_link(host: str) -> bool:
    return any(host == h or host.endswith(f".{h}") for h in _SHORT_LINK_HOSTS)


def _extract_coordinates(url: str) -> ParsedLocation | None:
    for pattern in _COORD_PATTERNS:
        match = pattern.search(url)
        if match:
            return ParsedLocation(latitude=float(match.group(1)), longitude=float(match.group(2)))
    return None


async def resolve_maps_link(client: httpx.AsyncClient, url: str) -> ParsedLocation | None:
    """Best-effort parse. Returns None for anything unusable -- not a Maps
    link, a shortened link that fails to resolve, or one of the coordinate
    shapes above not being present -- so the caller can fall back to asking
    the user to try again rather than treating this as a hard error."""
    url = url.strip()
    try:
        host = httpx.URL(url).host or ""
    except httpx.InvalidURL:
        return None

    if _is_short_link(host):
        try:
            response = await client.get(url, follow_redirects=True, timeout=10)
        except httpx.HTTPError:
            return None
        url = str(response.url)

    return _extract_coordinates(url)


# Preferred order for turning Nominatim's structured `address` object into
# one short name, closest match first -- this app's existing localities
# ("Koramangala", "Whitefield") are neighbourhood-level, not full postal
# addresses, so a pin should read the same way rather than as a verbose
# `display_name` string ("12, MG Road, Kottayam, Kottayam District, Kerala,
# 686001, India"). Falls through to progressively coarser fields only when
# a finer one is not present in the response, which varies by how well an
# area is mapped in OpenStreetMap.
_LOCALITY_ADDRESS_FIELDS = (
    "suburb",
    "neighbourhood",
    "quarter",
    "village",
    "town",
    "city",
)


async def reverse_geocode(client: httpx.AsyncClient, location: ParsedLocation) -> str | None:
    """Best-effort human-readable name for a pinned point. None on any
    failure (network, rate limit, no address data for this point) -- the
    caller falls back to plain coordinates, never blocks on this."""
    try:
        response = await client.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": f"{location.latitude:.6f}",
                "lon": f"{location.longitude:.6f}",
                "format": "jsonv2",
            },
            headers={"User-Agent": _NOMINATIM_USER_AGENT},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    address = data.get("address")
    if not isinstance(address, dict):
        return None

    name = next((address[field] for field in _LOCALITY_ADDRESS_FIELDS if address.get(field)), None)
    if not name:
        return None

    city = address.get("city") or address.get("town")
    if city and city != name:
        return f"{name}, {city}"
    return name
