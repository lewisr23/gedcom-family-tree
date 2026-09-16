import json
import os
import threading
import time

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Nominatim is a free service run on donated hardware. Its usage policy allows
# at most one request per second and requires a user agent that identifies the
# application. Breaching either gets the caller's IP blocked, so the delay below
# is deliberate: do not remove it without putting a different geocoder behind it.
NOMINATIM_MIN_INTERVAL = 1.0
USER_AGENT = "gedcom-family-tree-mapper (local genealogy tool)"

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'geocode_cache.json')


class GeocodingService:
    """Place name to coordinates, cached to disk across requests.

    A single family tree resolves to well over a hundred distinct places. Those
    lookups are the slowest part of the map export by a wide margin, so results
    persist between runs and negative results are cached too, otherwise every
    unresolvable place would be retried on every export.
    """

    _lock = threading.Lock()
    _cache = None
    _last_request = 0.0

    def __init__(self, cache_path=None):
        # Resolved at call time, not bound as a default, so tests can redirect
        # CACHE_PATH and never write stub coordinates into the real cache.
        self.cache_path = cache_path or CACHE_PATH
        self.geolocator = Nominatim(user_agent=USER_AGENT)
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
        try:
            tmp = self.cache_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({k: list(v) if v else None
                           for k, v in GeocodingService._cache.items()}, f, indent=1)
            os.replace(tmp, self.cache_path)
        except OSError as e:
            print(f"Could not write geocode cache: {e}")

    def _throttle(self):
        elapsed = time.monotonic() - GeocodingService._last_request
        if elapsed < NOMINATIM_MIN_INTERVAL:
            time.sleep(NOMINATIM_MIN_INTERVAL - elapsed)
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

        location = None
        # Timeouts here are common and transient. Without a retry the place is
        # simply absent from the map for that run, which reads as missing data.
        for attempt in range(2):
            try:
                self._throttle()
                # Biased to the isles the map actually covers, so that a bare
                # "Newcastle" does not land in New South Wales and then get
                # silently dropped by the bounds check as if it were missing.
                location = self.geolocator.geocode(
                    clean_name, timeout=10, country_codes='gb,ie', exactly_one=True
                )
                break
            except GeocoderTimedOut:
                if attempt == 0:
                    print(f"Geocoding timed out for {clean_name}, retrying")
                    continue
                # Not cached: a timeout says nothing about whether it exists.
                print(f"Geocoding timed out for {clean_name}, giving up")
                return None
            except GeocoderServiceError as e:
                print(f"Geocoding service error for {clean_name}: {e}")
                return None
            except Exception as e:
                print(f"Geocoding error for {clean_name}: {e}")
                return None

        if location:
            coords = (location.latitude, location.longitude)
            print(f"Geocoded: {clean_name} -> {coords[0]}, {coords[1]}")
        else:
            coords = None
            print(f"No match for: {clean_name}")

        cache[clean_name] = coords
        return coords

    def flush(self):
        """Persist newly resolved places. Call once a batch of lookups is done."""
        self._save_cache()
