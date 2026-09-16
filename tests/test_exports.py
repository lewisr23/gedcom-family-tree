"""Export paths: the poster, the generation series ZIP, and the map.

These assert on measurable properties (nothing drawn off the page, no network
calls on a warm cache, parser state left intact) rather than eyeballing a PDF.
"""
import json
import random
import zipfile

import pytest


@pytest.fixture(autouse=True)
def run_from_project_root(project_root, monkeypatch):
    # pdf_generator loads its fonts from a relative 'static/fonts' path.
    monkeypatch.chdir(project_root)


def most_children(parser):
    return max(parser.individuals,
               key=lambda i: len(parser.get_family_context(i)['children']))


def test_poster_draws_nothing_below_the_page(parsed, monkeypatch):
    from reportlab.pdfgen import canvas as rl_canvas
    import app.services.pdf_generator as pg

    drawn = []
    original = rl_canvas.Canvas.drawString

    def record(self, x, y, text, *a, **k):
        drawn.append((x, y, text))
        return original(self, x, y, text, *a, **k)

    monkeypatch.setattr(rl_canvas.Canvas, 'drawString', record)

    subject = most_children(parsed)
    person = parsed.get_person(subject)
    family = parsed.get_family_context(subject)
    assert len(family['children']) >= 10, "expect a genuinely long child list"

    pg.generate_a2_pdf(person, family, relationship='Relative', root_name='Joan Trotter')

    left_column = [(y, t) for (x, y, t) in drawn if x < 440]
    assert left_column
    # Was reaching y = -32 before the column learned to stop.
    assert min(y for y, _ in left_column) > 0


def test_long_child_list_is_summarised(parsed, monkeypatch):
    from reportlab.pdfgen import canvas as rl_canvas
    import app.services.pdf_generator as pg

    drawn = []
    original = rl_canvas.Canvas.drawString
    monkeypatch.setattr(
        rl_canvas.Canvas, 'drawString',
        lambda self, x, y, t, *a, **k: (drawn.append(t), original(self, x, y, t, *a, **k))[1])

    subject = most_children(parsed)
    pg.generate_a2_pdf(parsed.get_person(subject),
                       parsed.get_family_context(subject))

    assert any(t.startswith('...and ') and t.endswith(' more') for t in drawn)


def test_poster_names_the_root_rather_than_claiming_it_is_yours(parsed, monkeypatch):
    from reportlab.pdfgen import canvas as rl_canvas
    import app.services.pdf_generator as pg

    drawn = []
    original = rl_canvas.Canvas.drawString
    monkeypatch.setattr(
        rl_canvas.Canvas, 'drawString',
        lambda self, x, y, t, *a, **k: (drawn.append(t), original(self, x, y, t, *a, **k))[1])

    pg.generate_a2_pdf(parsed.get_person(parsed.root_id),
                       parsed.get_family_context(parsed.root_id),
                       relationship='Grandfather', root_name='Joan Trotter')

    assert any('Grandfather of Joan Trotter' in t for t in drawn)
    assert not any(t.startswith('(Your ') for t in drawn)


def test_generation_zip_has_one_pdf_per_generation(parsed):
    from app.services.pdf_generator import generate_generation_zip

    grouped = parsed.get_ancestors_by_generation(parsed.root_id)
    for people in grouped.values():
        for i, p in enumerate(people):
            people[i]['_family_context'] = parsed.get_family_context(p['id'])

    buf = generate_generation_zip(grouped)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert len(names) == len(grouped)
        for name in names:
            assert zf.read(name).startswith(b'%PDF')


def test_generation_export_leaves_parser_state_intact(parsed):
    """The export decorates what it is given; it must not reach back."""
    grouped = parsed.get_ancestors_by_generation(parsed.root_id)
    for people in grouped.values():
        for i, p in enumerate(people):
            people[i]['_family_context'] = parsed.get_family_context(p['id'])

    node = parsed.get_person(parsed.root_id)
    assert '_family_context' not in node
    assert '_side' not in node
    # Previously raised ValueError: Circular reference detected.
    json.dumps(parsed.graph_data)


def test_map_caches_places_and_needs_no_network_when_warm(parsed, tmp_path, monkeypatch):
    import app.services.geocoding as geo
    from app.services.pdf_generator import generate_map_pdf
    from geopy.geocoders import Nominatim
    from reportlab.pdfgen import canvas as rl_canvas

    monkeypatch.setattr(geo, 'CACHE_PATH', str(tmp_path / 'geocode_cache.json'))
    monkeypatch.setattr(geo, 'NOMINATIM_MIN_INTERVAL', 0)
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)

    # Compare what gets drawn rather than the PDF bytes: reportlab stamps a
    # creation date into every file, so two identical maps never match byte
    # for byte.
    plotted = []
    original = rl_canvas.Canvas.circle
    monkeypatch.setattr(
        rl_canvas.Canvas, 'circle',
        lambda self, x, y, r, *a, **k: (plotted.append((round(x, 3), round(y, 3), r)),
                                        original(self, x, y, r, *a, **k))[1])

    people = list(parsed.individuals.values())

    Nominatim.calls = 0
    first = generate_map_pdf(people)
    cold_calls, cold_plot = Nominatim.calls, list(plotted)
    assert cold_calls > 50, "expected a realistic number of distinct places"
    assert len(cold_plot) > 50, "expected markers on the map"
    assert (tmp_path / 'geocode_cache.json').exists()

    # A fresh process would rebuild the in-memory cache from disk.
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)
    plotted.clear()
    Nominatim.calls = 0
    second = generate_map_pdf(people)
    assert Nominatim.calls == 0, "warm cache must not hit the network"
    assert plotted == cold_plot, "warm run must plot exactly the same points"
    assert len(first.getvalue()) == len(second.getvalue())


def test_map_cache_records_failures_too(tmp_path, monkeypatch):
    import app.services.geocoding as geo
    from geopy.geocoders import Nominatim

    monkeypatch.setattr(geo, 'CACHE_PATH', str(tmp_path / 'c.json'))
    monkeypatch.setattr(geo, 'NOMINATIM_MIN_INTERVAL', 0)
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)

    svc = geo.GeocodingService()
    # Seeded random makes this query a miss; the point is it is only asked once.
    unresolvable = next(q for q in (f"place {i}" for i in range(100))
                        if random.Random(q).random() < 0.15)

    Nominatim.calls = 0
    assert svc.get_coords(unresolvable) is None
    assert svc.get_coords(unresolvable) is None
    assert Nominatim.calls == 1


def test_timeout_is_retried_once_then_left_uncached(tmp_path, monkeypatch):
    import app.services.geocoding as geo

    attempts = {'n': 0}

    class Flaky:
        def __init__(self, *a, **k):
            pass

        def geocode(self, query, **kw):
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise geo.GeocoderTimedOut('slow')
            raise geo.GeocoderTimedOut('slow again')

    monkeypatch.setattr(geo, 'CACHE_PATH', str(tmp_path / 'c.json'))
    monkeypatch.setattr(geo, 'NOMINATIM_MIN_INTERVAL', 0)
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)
    monkeypatch.setattr(geo, 'Nominatim', Flaky)

    svc = geo.GeocodingService()
    assert svc.get_coords('Somewhere') is None
    assert attempts['n'] == 2, 'should retry a timeout exactly once'
    # A timeout says nothing about whether the place exists, so it must not
    # be remembered as a miss.
    assert 'Somewhere' not in geo.GeocodingService._cache


def test_transient_timeout_still_resolves(tmp_path, monkeypatch):
    import app.services.geocoding as geo

    attempts = {'n': 0}

    class Flaky:
        def __init__(self, *a, **k):
            pass

        def geocode(self, query, **kw):
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise geo.GeocoderTimedOut('slow')
            return type('L', (), {'latitude': 54.97, 'longitude': -1.61})()

    monkeypatch.setattr(geo, 'CACHE_PATH', str(tmp_path / 'c.json'))
    monkeypatch.setattr(geo, 'NOMINATIM_MIN_INTERVAL', 0)
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)
    monkeypatch.setattr(geo, 'Nominatim', Flaky)

    assert geo.GeocodingService().get_coords('Newcastle') == (54.97, -1.61)


def test_geocoder_is_asked_for_british_isles_only(tmp_path, monkeypatch):
    import app.services.geocoding as geo

    seen = {}

    class Recording:
        def __init__(self, *a, **k):
            seen['user_agent'] = k.get('user_agent')

        def geocode(self, query, **kw):
            seen['kwargs'] = kw
            return None

    monkeypatch.setattr(geo, 'CACHE_PATH', str(tmp_path / 'c.json'))
    monkeypatch.setattr(geo, 'NOMINATIM_MIN_INTERVAL', 0)
    monkeypatch.setattr(geo.GeocodingService, '_cache', None)
    monkeypatch.setattr(geo, 'Nominatim', Recording)

    geo.GeocodingService().get_coords('Newcastle')

    assert seen['kwargs']['country_codes'] == 'gb,ie'
    assert 'gedcom' in seen['user_agent'].lower()
