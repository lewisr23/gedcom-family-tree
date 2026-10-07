"""Where each person was, year by year.

The map export flattens a whole family into one sheet: every place anyone ever
lived, all at once. That is the one view which throws away the thing this data
is actually good at. These trees are mostly census records, so the residences
land on census years, and at any given year there is a real answer to "who was
alive, and where were they living".

This module turns a parsed tree into that answer: a list of people, each with
the dated places they are known to have been, so the browser can scrub through
time without asking the server again.

Nothing here geocodes. Resolving an unknown place costs a second of Nominatim
courtesy delay, which is fine for a one off PDF export and far too slow for a
slider someone is dragging. Only places already in the cache get a pin, and the
payload says how many were left out so the UI can be honest about it.
"""
import collections
import datetime
import re

from app.services.geocoding import GeocodingService

# A four digit year anywhere in the string. Genealogy dates arrive as "1911",
# "abt 1898", "24 Dec 1909", "Apr 1960" and "11/11/1939", and the only part
# that matters for a timeline is the year, so do not try to parse the rest.
_YEAR = re.compile(r'\b(1[2-9]\d\d|20\d\d)\b')

# "abt 1898" and "Abt. 1850" both appear in real exports, as do BEF, AFT, EST,
# CAL and BET. A pin from one of these is a guess and the UI says so.
_APPROX = re.compile(
    r'\b(abt|about|bef|before|aft|after|est|cal|bet|circa)\b\.?', re.I)

# How long to assume someone lived when the file records no death. Most trees
# are missing far more deaths than births: in a typical Ancestry export only
# about half the people have one. Dropping everyone without a death date would
# empty the map, and assuming they are still alive would leave Victorians
# standing around in the present day, so pick a lifespan and mark it a guess.
ASSUMED_LIFESPAN = 85

# Guard against a mistyped or misparsed year dragging the slider's range out
# to something absurd.
EARLIEST_PLAUSIBLE = 1200

# Decimal places at which two coordinates count as the same pin. Three is about
# 110 metres.
#
# This matters more than it sounds. One town arrives spelled a dozen ways in a
# real file: "South Shields", "South Shields, Durham", "South Shields, Co
# Durham, England", "South Shields,  Durham" and so on. Keyed by name they are
# a dozen pins stacked on one spot and nobody ever appears to stay put. Keyed
# by where they actually resolved, they are one place someone lived for forty
# years, which is the truth the map should show.
PLACE_PRECISION = 3


def parse_year(text):
    """(year, approximate) from a GEDCOM date, or (None, False)."""
    if not text:
        return None, False
    text = str(text)
    match = _YEAR.search(text)
    if not match:
        return None, False
    return int(match.group(1)), bool(_APPROX.search(text))


def located_events(person):
    """Dated, placed events for one person, earliest first.

    pdf_generator._located_events exists for the static map and deliberately
    drops dates, because that map draws a route rather than a chronology. This
    one keeps them, and keeps only events having both a date and a place, since
    an undated residence cannot be put on a slider.
    """
    found = []

    def add(kind, place, date):
        if not place or not str(place).strip():
            return
        year, approx = parse_year(date)
        if year is None or year < EARLIEST_PLAUSIBLE:
            return
        found.append({
            'type': kind,
            'place': str(place).strip(),
            'year': year,
            'approx': approx,
        })

    add('BIRT', person.get('birthPlace'), person.get('birth'))
    for event in person.get('events', []):
        if event.get('type') in ('BIRT', 'DEAT'):
            continue
        add(event.get('type') or 'EVEN', event.get('place'), event.get('date'))
    add('DEAT', person.get('deathPlace'), person.get('death'))

    # Stable within a year: a birth outranks a residence and a death comes
    # last, so someone born and censused in the same year starts at their
    # birthplace rather than wherever the enumerator found them.
    rank = {'BIRT': 0, 'DEAT': 2}
    found.sort(key=lambda e: (e['year'], rank.get(e['type'], 1)))
    return found


def life_span(person, events):
    """(start_year, end_year, flags) for the window a person appears in.

    end_year is when they stop being drawn: a death year when the file has one,
    otherwise an assumption, stretched if the paper trail runs on past it.
    """
    birth_year, birth_approx = parse_year(person.get('birth'))
    death_year, death_approx = parse_year(person.get('death'))

    event_years = [e['year'] for e in events]
    first_seen = min(event_years) if event_years else None
    last_seen = max(event_years) if event_years else None

    start = birth_year if birth_year is not None else first_seen
    if start is None:
        return None, None, {}

    estimated_end = False
    if death_year is not None:
        end = death_year
    else:
        # Outlive the last record rather than vanishing the moment the paper
        # trail stops, but not past a plausible lifetime.
        end = start + ASSUMED_LIFESPAN
        if last_seen is not None:
            end = max(end, last_seen)
        estimated_end = True

    if end < start:
        # A death recorded before a birth is a data error, not a negative life.
        # Show the person at a single point rather than dropping them.
        end = start

    return start, end, {
        'birthApprox': birth_approx,
        'deathApprox': death_approx,
        'estimatedEnd': estimated_end,
        'birthKnown': birth_year is not None,
        'deathKnown': death_year is not None,
    }



def _resolve_places(usage, cache):
    """Group place names onto shared pins.

    Takes {name: how often it is used} and returns (name -> pin index, pins,
    unresolved names). Names that geocoded to the same point become one pin,
    labelled with whichever spelling the file leans on most, falling back to
    the shortest so that "Durham, England" wins over "Hartlepool Throsting,
    Durham, England" when a run of places all fell back to the same centroid.
    """
    by_point = collections.defaultdict(list)
    unresolved = set()

    for name in usage:
        coords = cache.get(name)
        if not coords:
            unresolved.add(name)
            continue
        key = (round(coords[0], PLACE_PRECISION), round(coords[1], PLACE_PRECISION))
        by_point[key].append(name)

    index_of = {}
    pins = []
    for (lat, lon), names in by_point.items():
        label = max(names, key=lambda n: (usage[n], -len(n)))
        pin = len(pins)
        pins.append({
            'name': label,
            'lat': lat,
            'lon': lon,
            'variants': len(names),
        })
        for name in names:
            index_of[name] = pin

    return index_of, pins, unresolved


def build_timeline(people):
    """The payload the year slider runs on.

    Pins are interned: 144 people share about 90 distinct points and most of
    them repeat, so sending an index rather than a place string each time keeps
    the whole thing to one small response and no further round trips.
    """
    cache = GeocodingService._cache
    if cache is None:
        # Touching the service loads the cache into the class attribute.
        GeocodingService()
        cache = GeocodingService._cache or {}

    # Pass one: every person's dated, placed events, and how often each
    # spelling of a place is used. The counts decide the pin labels.
    per_person = []
    usage = collections.Counter()
    for person in people:
        events = located_events(person)
        per_person.append((person, events))
        for event in events:
            usage[event['place']] += 1

    index_of, pins, unresolved = _resolve_places(usage, cache)

    # Nobody should still be walking around after today just because the file
    # never recorded their death.
    this_year = datetime.date.today().year

    entries = []
    undated = 0
    for person, events in per_person:
        start, end, flags = life_span(person, events)
        if start is None:
            undated += 1
            continue
        end = min(end, this_year)
        if end < start:
            end = start

        moves = []
        for event in events:
            pin = index_of.get(event['place'])
            if pin is None:
                continue
            # Collapse a stay: a census finding someone at the same point in
            # 1901 and again in 1911 is one pin, not two. "until" carries how
            # long the record holds them there.
            if moves and moves[-1]['place'] == pin:
                moves[-1]['until'] = max(moves[-1]['until'], event['year'])
                continue
            moves.append({
                'year': event['year'],
                'until': event['year'],
                'place': pin,
                'type': event['type'],
                'approx': event['approx'],
            })

        entry = {
            'id': person.get('id'),
            'name': (person.get('name') or '').replace('/', '').strip(),
            'sex': person.get('sex') or '',
            'birth': start,
            'end': end,
            'moves': moves,
        }
        entry.update(flags)
        entries.append(entry)

    years = [e['birth'] for e in entries] + [e['end'] for e in entries]
    span = {'from': min(years), 'to': max(years)} if years else None

    return {
        'span': span,
        'places': pins,
        'people': entries,
        'unresolved': sorted(unresolved),
        'stats': {
            'people': len(entries),
            'undated': undated,
            'placed': sum(1 for e in entries if e['moves']),
            'unresolvedPlaces': len(unresolved),
        },
    }
