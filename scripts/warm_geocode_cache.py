"""Pre-resolve place names into the geocode cache.

Why this exists: the map export geocodes through Nominatim at one lookup per
second, and free hosting has no persistent disk, so a cold start would
otherwise re-resolve every place from scratch and take minutes. Running this
once and committing the result means the cache ships inside the image and the
map is fast from the first request.

The cache holds place names and coordinates only: no names, no dates. It is
committed and baked into a public image, so street level places are skipped
as well. A GEDCOM residence can be a house someone still lives in, and a
postcode accurate pin of it is not ours to publish. Those places still resolve
normally at runtime for whoever uploaded the file, they just are not shipped.

    python scripts/warm_geocode_cache.py path/to/tree.ged [more.ged ...]

Safe to re-run: existing entries are kept and only new places are looked up.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.gedcom_parser import GedcomParser
from app.services import geocoding
from app.services.pdf_generator import _places_of


# House number, or "6/7" style flat. Anchored to the start of a comma field so
# that a year or a district number mid-name does not trip it.
_HOUSE_NUMBER = re.compile(r'(^|,)\s*\d+\s*(/\s*\d+)?\s*(,|\s)')

# Whole word only, and deliberately without "St": in this data "St" is almost
# always Saint, as in "St Andrew, Bishopwearmouth", not Street.
_STREET_WORD = re.compile(
    r'(^|[,\s])(street|road|lane|avenue|gardens|gdns|terrace|crescent'
    r'|drive|close|square|cottages|villas)([,\s]|$)', re.I)


def is_street_level(place):
    """True for places precise enough to identify a household."""
    return bool(_HOUSE_NUMBER.search(place) or _STREET_WORD.search(place))


def places_in(path):
    parser = GedcomParser()
    with open(path, encoding='utf-8', errors='ignore') as f:
        parser.parse(f.read())
    found = []
    for person in parser.individuals.values():
        found.extend(_places_of(person))
    return found


def main(paths):
    if not paths:
        print(__doc__)
        return 1

    wanted = []
    skipped = []
    seen = set()
    for path in paths:
        if not os.path.exists(path):
            print(f"skipping {path}, not found")
            continue
        for place in places_in(path):
            if place in seen:
                continue
            seen.add(place)
            if is_street_level(place):
                skipped.append(place)
                continue
            wanted.append(place)

    if skipped:
        print(f"skipping {len(skipped)} street level places, too precise to ship")

    if not wanted:
        print("no places found")
        return 1

    service = geocoding.GeocodingService()
    already = sum(1 for p in wanted if p in geocoding.GeocodingService._cache)
    todo = len(wanted) - already

    print(f"{len(wanted)} distinct places, {already} already cached, {todo} to resolve")
    if todo:
        mins = todo * geocoding.NOMINATIM_MIN_INTERVAL / 60
        print(f"at one lookup per second this takes about {mins:.0f} minutes")

    started = time.time()
    resolved = 0
    for i, place in enumerate(wanted, 1):
        if service.get_coords(place):
            resolved += 1
        if i % 25 == 0:
            service.flush()  # checkpoint, so an interrupted run is not wasted
            print(f"  {i}/{len(wanted)} ({time.time() - started:.0f}s elapsed)")

    service.flush()
    print(f"\ndone in {time.time() - started:.0f}s")
    print(f"resolved {resolved} of {len(wanted)}")
    print(f"cache written to {service.cache_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
