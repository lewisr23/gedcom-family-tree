import json
import os
import re
import threading
import time

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Which backend to use. Nominatim is the default because it costs nothing and
# needs no signup, which keeps a local install and a small deployment free. Its
# usage policy allows one request per second and requires a user agent that
# identifies the application; breaching either gets the IP blocked, so the
# delay below is deliberate.
#
# That one per second is a per process budget shared by every visitor, so it
# is the first thing that hurts under real traffic. Set GEOCODER=locationiq
# (or mapbox) with GEOCODER_API_KEY to lift it. Both have free tiers well
# beyond what a hobby deployment uses.
GEOCODER = os.environ.get('GEOCODER', 'nominatim').lower()
GEOCODER_API_KEY = os.environ.get('GEOCODER_API_KEY', '')

NOMINATIM_MIN_INTERVAL = 1.0
# Paid tiers permit far more; a small gap still spreads the load politely.
KEYED_MIN_INTERVAL = float(os.environ.get('GEOCODER_MIN_INTERVAL', 0.1))

USER_AGENT = os.environ.get(
    'GEOCODER_USER_AGENT',
    "gedcom-family-tree-mapper (local genealogy tool)")

CACHE_PATH = os.environ.get(
    'GEOCODE_CACHE_PATH',
    os.path.join(os.path.dirname(__file__), 'geocode_cache.json'))


def _build_geolocator():
    """The configured backend, plus the minimum gap between its requests."""
    if GEOCODER in ('locationiq', 'mapbox') and GEOCODER_API_KEY:
        if GEOCODER == 'locationiq':
            from geopy.geocoders import LocationIQ
            return LocationIQ(api_key=GEOCODER_API_KEY), KEYED_MIN_INTERVAL
        from geopy.geocoders import MapBox
        return MapBox(api_key=GEOCODER_API_KEY), KEYED_MIN_INTERVAL

    if GEOCODER != 'nominatim':
        print(f"GEOCODER={GEOCODER} needs GEOCODER_API_KEY; falling back to Nominatim")
    return Nominatim(user_agent=USER_AGENT), NOMINATIM_MIN_INTERVAL


# Historic English county divisions that Nominatim does not recognise as
# places. Ancestry writes them constantly, and leaving one in the query is the
# single biggest cause of a failed lookup.
_HISTORIC_COUNTIES = re.compile(
    r"\byorkshire\s*\(?\s*(?:north|west|east)\s+riding\s*\)?"
    r"|\b(?:north|west|east|mid)\s+riding(?:\s+of\s+yorkshire)?"
    r"|\byorkshire\s+(?:north|west|east)\b",
    re.IGNORECASE)

# Administrative qualifiers that describe a subdivision, not a findable place.
_WARD_NOISE = re.compile(
    r"\b(?:central|north|south|east|west|northern|southern|eastern|western)?"
    r"\s*(?:ward|sub[- ]?district|registration district|poor law union|union|"
    r"civil parish|parish|township|hundred|rural district|urban district)\b",
    re.IGNORECASE)

_LEADING_NOISE = re.compile(r"^\s*(?:of|nr|near|at|in)\s+", re.IGNORECASE)
_HOUSE_NUMBER = re.compile(r"^\s*\d+[a-z]?(?:\s*[/-]\s*\d+[a-z]?)?\s*,?\s*", re.IGNORECASE)
_STREET = re.compile(
    r"\b(?:street|st|road|rd|lane|ln|avenue|ave|terrace|place|row|court|"
    r"close|drive|crescent|square|buildings|cottages|cloisters)\b\.?$",
    re.IGNORECASE)

# Answering a query with one of these gives the centroid of an entire country,
# which is a confidently wrong pin in the middle of the map. Never ask.
_TOO_BROAD = {
    'england', 'scotland', 'wales', 'ireland', 'northern ireland',
    'united kingdom', 'uk', 'great britain', 'britain', 'gb', 'eire',
    'the', 'unknown', 'n/a', 'none',
}

_COUNTRIES = {'england', 'scotland', 'wales', 'ireland',
              'northern ireland', 'united kingdom'}


def _too_broad(candidate):
    """True if resolving this would pin a person to a whole country.

    Also true for nothing at all: a blank PLAC is common in real files and
    there is no query to make from it.
    """
    bare = (candidate or '').strip().strip(',').lower()
    if not bare or bare in _TOO_BROAD:
        return True
    # "England, United Kingdom" and similar: every token is a country name.
    parts = [p.strip().lower() for p in bare.split(',') if p.strip()]
    return bool(parts) and all(p in _TOO_BROAD for p in parts)


def _query_variants(place):
    """Progressively simpler forms of a place name, most specific first.

    Genealogy place strings are typed by hand over decades and arrive in a
    state no geocoder accepts: unbalanced brackets, historic ridings, joined
    parish names, house numbers. Rather than drop those people from the map,
    fall back through coarser forms until one resolves, because the right town
    beats no pin at all.

    What this deliberately will not do is fall back to a bare country. A pin in
    the middle of England is not a weaker answer, it is a wrong one, and on a
    map it looks just as authoritative as a correct pin.
    """
    out = []
    if not place or not str(place).strip():
        return out
    place = str(place)

    def add(candidate):
        candidate = re.sub(r"\s+", " ", candidate or "")
        candidate = re.sub(r"\s*,\s*", ", ", candidate).strip(" ,")
        candidate = re.sub(r"(,\s*)+", ", ", candidate)
        if not candidate or _too_broad(candidate):
            return
        if candidate.lower() not in {c.lower() for c in out}:
            out.append(candidate)

    add(place)

    # Brackets, balanced or not: "Scarborough (Central Ward, North Riding"
    cleaned = re.sub(r"\([^)]*\)", " ", place)
    cleaned = re.sub(r"[()]", " ", cleaned)
    add(cleaned)

    # Drop historic ridings and ward/parish qualifiers.
    simplified = _WARD_NOISE.sub(" ", _HISTORIC_COUNTIES.sub(" ", cleaned))
    add(simplified)

    # Strip a leading "of"/"near" and any house number.
    simplified = _HOUSE_NUMBER.sub("", _LEADING_NOISE.sub("", simplified))
    add(simplified)

    parts = [p.strip() for p in simplified.split(",") if p.strip()]

    # A street address: the street itself will not geocode, its town might.
    if parts and _STREET.search(parts[0]) and len(parts) > 1:
        parts = parts[1:]
        add(", ".join(parts))

    # Joined parishes: "Ingleby Arncliffe and Ingleby Cross" -> the first.
    if parts and re.search(r"\s+and\s+", parts[0]):
        first = re.split(r"\s+and\s+", parts[0], maxsplit=1)[0].strip()
        if first:
            add(", ".join([first] + parts[1:]))

    country = parts[-1] if parts and parts[-1].lower() in _COUNTRIES else ''
    settlement = (re.split(r"\s+and\s+", parts[0], maxsplit=1)[0].strip()
                  if parts else '')

    # The settlement paired with its country, tried BEFORE anything coarser.
    #
    # Genealogy strings nest outward: "South Shields, St Thomas, Westoe,
    # Durham, England" is a town, then a parish, then a district, then a
    # county. Coarsening from the left throws away the town first and lands on
    # the county centroid, which is how thirteen different places ended up
    # pinned to the middle of Durham. Ask for the town first.
    if settlement and country:
        add(f"{settlement}, {country}")
    elif settlement and len(parts) > 1:
        add(settlement)

    # Then progressively drop the innermost qualifiers, keeping the settlement.
    for keep in range(len(parts) - 1, 1, -1):
        add(", ".join(parts[:keep]))

    # Only now give up on the settlement and try the wider area, which is
    # still better than nothing for a place that no longer exists by name.
    for drop in range(1, len(parts)):
        remaining = parts[drop:]
        if len(remaining) < 2:
            break
        add(", ".join(remaining))

    return out


class _Transient(Exception):
    """A lookup failed for reasons unrelated to the place existing."""


class GeocodingService:
    """Place name to coordinates, cached to disk across requests.

    A single family tree resolves to well over a hundred distinct places. Those
    lookups are the slowest part of the map export by a wide margin, so results
    persist between runs and negative results are cached too, otherwise every
    unresolvable place would be retried on every export.
    """

    _lock = threading.Lock()
    _throttle_lock = threading.Lock()
    _cache = None
    _last_request = 0.0

    def __init__(self, cache_path=None):
        # Resolved at call time, not bound as a default, so tests can redirect
        # CACHE_PATH and never write stub coordinates into the real cache.
        self.cache_path = cache_path or CACHE_PATH
        self.geolocator, self.min_interval = _build_geolocator()
        self._load_cache()

    def _load_cache(self):
        with GeocodingService._lock:
            if GeocodingService._cache is not None:
                return
            cache = {}
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                # Stored as [lat, lon] or null; restore tuples for callers.
                for key, val in raw.items():
                    cache[key] = tuple(val) if val else None
            except FileNotFoundError:
                pass
            except (ValueError, OSError) as e:
                print(f"Could not read geocode cache, starting empty: {e}")
            GeocodingService._cache = cache

    def _save_cache(self):
        # Snapshot under the lock: another request's lookups may be writing to
        # the same dict, and serialising it mid-write raises.
        with GeocodingService._lock:
            snapshot = {k: list(v) if v else None
                        for k, v in GeocodingService._cache.items()}
        try:
            # Unique temp name so two concurrent flushes cannot clobber each
            # other's partial file before the rename.
            tmp = f"{self.cache_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=1)
            os.replace(tmp, self.cache_path)
        except OSError as e:
            print(f"Could not write geocode cache: {e}")

    def _throttle(self):
        # Class level, so the gap holds across concurrent requests rather than
        # each visitor getting their own allowance.
        with GeocodingService._throttle_lock:
            elapsed = time.monotonic() - GeocodingService._last_request
            interval = getattr(self, 'min_interval', NOMINATIM_MIN_INTERVAL)
            if elapsed < interval:
                time.sleep(interval - elapsed)
            GeocodingService._last_request = time.monotonic()

    def get_coords(self, place_name):
        if not place_name:
            return None

        clean_name = place_name.strip()
        if not clean_name:
            return None

        cache = GeocodingService._cache
        if clean_name in cache:
            return cache[clean_name]

        # Try the name as written, then progressively simpler forms. Genealogy
        # place strings are messy enough that roughly a third of them fail a
        # literal lookup, and a nearby town is far more use than no pin.
        coords = None
        try:
            variants = _query_variants(clean_name)
        except Exception:
            variants = [clean_name]

        for i, candidate in enumerate(variants):
            try:
                coords = self._lookup(candidate)
            except _Transient:
                # Network trouble rather than an unknown place: leave it
                # uncached so a later run can try again.
                return None
            if coords:
                if i:
                    print(f"Geocoded: {clean_name} -> via '{candidate}' "
                          f"-> {coords[0]}, {coords[1]}")
                else:
                    print(f"Geocoded: {clean_name} -> {coords[0]}, {coords[1]}")
                break
        else:
            print(f"No match for: {clean_name}")

        cache[clean_name] = coords
        return coords

    def _lookup(self, query):
        """One geocoder call. None on no match, and on a failure we should not
        cache as a miss (a timeout says nothing about whether a place exists)."""
        for attempt in range(2):
            try:
                self._throttle()
                # Biased to the isles the map actually covers, so that a bare
                # "Newcastle" does not land in New South Wales and then get
                # silently dropped by the bounds check as if it were missing.
                location = self.geolocator.geocode(
                    query, timeout=10, country_codes='gb,ie', exactly_one=True)
                if location is None:
                    return None
                return (location.latitude, location.longitude)
            except GeocoderTimedOut:
                if attempt == 0:
                    print(f"Geocoding timed out for {query}, retrying")
                    continue
                print(f"Geocoding timed out for {query}, giving up")
                raise _Transient()
            except GeocoderServiceError as e:
                print(f"Geocoding service error for {query}: {e}")
                raise _Transient()
            except Exception as e:
                print(f"Geocoding error for {query}: {e}")
                raise _Transient()
        return None

    def flush(self):
        """Persist newly resolved places. Call once a batch of lookups is done."""
        self._save_cache()
