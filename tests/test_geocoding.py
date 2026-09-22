"""Place name normalisation.

Genealogy place strings are messy, so the geocoder falls back through coarser
forms. The rule that matters most: never fall back so far that the answer is a
whole country, because a pin in the middle of England looks exactly as
authoritative on the map as a correct one.
"""
import pytest

from app.services.geocoding import _query_variants, _too_broad


@pytest.mark.parametrize('broad', [
    'England', 'england', 'United Kingdom', 'UK', 'Scotland', 'Wales',
    'Ireland', 'Northern Ireland', 'Great Britain', 'England, United Kingdom',
    'The', '', '   ', 'unknown',
])
def test_country_sized_answers_are_rejected(broad):
    assert _too_broad(broad) is True


@pytest.mark.parametrize('fine', [
    'Sheffield', 'Sunderland, Durham, England', 'Jarrow', 'County Down',
])
def test_real_places_are_not_rejected(fine):
    assert _too_broad(fine) is False


@pytest.mark.parametrize('place', [
    'England',
    'United Kingdom',
    'England, United Kingdom',
    'Scotland',
])
def test_a_bare_country_yields_no_query_at_all(place):
    # Better to leave the person off the map than to place them in the middle
    # of the country with the same confidence as a real pin.
    assert _query_variants(place) == []


@pytest.mark.parametrize('place', [
    'Scarborough (Central Ward, North Riding, England',
    'Sheffield, Yorkshire West Riding, United Kingdom',
    '6/7, The, Cloisters, Sunderland, Durham, England',
    'Ingleby Arncliffe and Ingleby Cross, North Riding, England',
    '19 Lawson Street, Sheffield',
    'Hartlepool Throsting, Durham, England',
])
def test_no_variant_is_ever_a_bare_country(place):
    for variant in _query_variants(place):
        assert not _too_broad(variant), f"{place!r} produced {variant!r}"


def test_the_original_spelling_is_always_tried_first():
    place = 'Tyne and Wear, England'
    assert _query_variants(place)[0] == place


def test_unbalanced_bracket_is_recovered():
    variants = _query_variants('Scarborough (Central Ward, North Riding, England')
    assert 'Scarborough, England' in variants


def test_historic_riding_is_dropped():
    variants = _query_variants('Sheffield, Yorkshire West Riding, United Kingdom')
    assert 'Sheffield, United Kingdom' in variants
    assert not any('riding' in v.lower() for v in variants[1:])


def test_joined_parishes_fall_back_to_the_first():
    variants = _query_variants(
        'Ingleby Arncliffe and Ingleby Cross, North Riding, England')
    assert 'Ingleby Arncliffe, England' in variants


def test_house_number_and_street_give_way_to_the_town():
    variants = _query_variants('19 Lawson Street, Sheffield')
    assert 'Sheffield' in variants


def test_street_address_falls_back_to_its_town():
    variants = _query_variants('6/7, The, Cloisters, Sunderland, Durham, England')
    assert 'Sunderland, Durham, England' in variants


def test_the_settlement_is_tried_before_the_county():
    """The ordering bug that mattered.

    Coarsening from the left drops the town first, so a string like
    "South Shields, St Thomas, Westoe, Durham, England" resolved to the Durham
    county centroid. Thirteen distinct places ended up on the same pin that
    way. The town has to be asked for before the county.
    """
    variants = _query_variants('South Shields, St Thomas, Westoe, Durham, England')
    town = next(i for i, v in enumerate(variants) if v.startswith('South Shields,'))
    county = next(i for i, v in enumerate(variants) if v == 'Durham, England')
    assert town < county, variants


@pytest.mark.parametrize('place,settlement,wider', [
    ('South Shields, St Thomas, Westoe, Durham, England', 'South Shields', 'Durham, England'),
    ('Tynemouth, Northumberland, England', 'Tynemouth', 'Northumberland, England'),
    ('6/7, The, Cloisters, Sunderland, Durham, England', 'Sunderland', 'Durham, England'),
])
def test_specific_place_always_precedes_the_broader_one(place, settlement, wider):
    variants = _query_variants(place)
    first_specific = next((i for i, v in enumerate(variants) if settlement in v), None)
    first_wider = next((i for i, v in enumerate(variants) if v == wider), None)
    assert first_specific is not None, variants
    if first_wider is not None:
        assert first_specific < first_wider, variants


def test_no_duplicate_queries():
    for place in ['Jarrow, Durham, England', 'Sheffield',
                  'Scarborough (Central Ward, North Riding, England']:
        variants = _query_variants(place)
        assert len(variants) == len({v.lower() for v in variants})


def test_clean_place_name_needs_no_fallback():
    assert _query_variants('Jarrow, Durham, England')[0] == 'Jarrow, Durham, England'


def test_empty_input_is_safe():
    assert _query_variants('') == []
    assert _query_variants(None) == []
