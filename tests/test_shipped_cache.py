"""What is allowed into the committed geocode cache.

The cache is committed and baked into a public image, which makes it the one
part of this project where a real tree's contents leave the machine. Place
names alone are fine. A house number is not: a GEDCOM residence can be an
address someone still lives at, and the cache would pin it to six decimals in
a public repository. These tests guard the filter that keeps those out.
"""
import importlib.util
import json
import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(PROJECT_ROOT, 'app', 'services', 'geocode_cache.json')
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'scripts', 'warm_geocode_cache.py')


def _load_filter():
    """Pull is_street_level out of the script without importing app modules.

    The script imports the parser and the geocoder at module level, which the
    conftest stub is not set up for here. Only the regexes are under test.
    """
    with open(SCRIPT_PATH, encoding='utf-8') as f:
        head = f.read().split('def places_in')[0]
    head = '\n'.join(
        line for line in head.splitlines()
        if not line.startswith('from app') and 'sys.path.insert' not in line
    )
    namespace = {}
    exec(compile(head, SCRIPT_PATH, 'exec'), namespace)
    return namespace['is_street_level']


is_street_level = _load_filter()


@pytest.mark.parametrize('place', [
    '19 Lawson Street, Sheffield',
    '11 Manet Gdns, Whiteleas, South Shield, Tyne and Wear, England',
    '6/7, The, Cloisters, Sunderland, Durham, England',
    '56, clifton, park, place, Belfast, Antrim, Northern Ireland',
    'Skerry Street, Ballynahinch, Down, Ireland',
    '2 Acacia Avenue, Leeds',
    'Rose Cottages, Durham',
])
def test_household_level_places_are_rejected(place):
    assert is_street_level(place) is True


@pytest.mark.parametrize('place', [
    # "St" here is Saint. Treating it as Street would throw away most of the
    # parish names this data is built from.
    'St Andrew, Bishopwearmouth, Durham, England',
    'St Mary, St Mary Within, Cumberland, England',
    'South Shields, St Thomas, Westoe, Durham, England',
    'St Peters Lag, Newcastle upon Tyne RD, Northumberland, England',
    'Carlisle, St Mary, Cumberland, England',
    'Accrington, Lancashire, England',
    'Yorkshire (West Riding), England',
    'Walker, Christ Church Walker, Longbenton, Northumberland, England',
])
def test_parish_and_town_names_are_kept(place):
    assert is_street_level(place) is False


def test_committed_cache_holds_no_street_level_places():
    with open(CACHE_PATH, encoding='utf-8') as f:
        cache = json.load(f)
    offenders = [place for place in cache if is_street_level(place)]
    assert offenders == [], f"street level places in the shipped cache: {offenders}"


def test_committed_cache_holds_no_digits_at_all():
    """A blunter backstop than the filter, in case a new shape slips past it."""
    with open(CACHE_PATH, encoding='utf-8') as f:
        cache = json.load(f)
    offenders = [place for place in cache if re.search(r'\d', place)]
    assert offenders == [], f"numeric places in the shipped cache: {offenders}"


def test_cache_values_are_coordinates_or_null():
    with open(CACHE_PATH, encoding='utf-8') as f:
        cache = json.load(f)
    assert cache, 'the shipped cache is empty, the map will be slow on cold start'
    for place, value in cache.items():
        if value is None:
            continue
        lat, lon = value
        assert -90 <= lat <= 90, place
        assert -180 <= lon <= 180, place
