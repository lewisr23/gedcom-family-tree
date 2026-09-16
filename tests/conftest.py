import os
import random
import sys
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# --- keep the suite off the network ------------------------------------------
#
# This runs at conftest import, before any test module is loaded. It has to:
# importing app.main pulls in pdf_generator and then geocoding, which binds
# Nominatim at import time. Stub it any later and the map tests would issue
# real, rate limited lookups against a public service.

class FakeLocation:
    def __init__(self, lat, lon):
        self.latitude, self.longitude = lat, lon


class FakeNominatim:
    """Deterministic stand-in: same query always gives the same answer."""

    calls = 0

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def geocode(self, query, **kwargs):
        FakeNominatim.calls += 1
        rnd = random.Random(query)
        if rnd.random() < 0.15:
            return None  # a realistic share of places do not resolve
        return FakeLocation(rnd.uniform(50.0, 58.5), rnd.uniform(-6.0, 1.5))


def _install_fake_geopy():
    geopy = types.ModuleType('geopy')
    geocoders = types.ModuleType('geopy.geocoders')
    exc = types.ModuleType('geopy.exc')

    geocoders.Nominatim = FakeNominatim
    exc.GeocoderTimedOut = type('GeocoderTimedOut', (Exception,), {})
    exc.GeocoderServiceError = type('GeocoderServiceError', (Exception,), {})

    geopy.geocoders = geocoders
    geopy.exc = exc

    sys.modules['geopy'] = geopy
    sys.modules['geopy.geocoders'] = geocoders
    sys.modules['geopy.exc'] = exc


assert 'app.services.geocoding' not in sys.modules, \
    'geocoding was imported before the geopy stub was installed'
_install_fake_geopy()


@pytest.fixture(autouse=True)
def no_real_geocoder():
    """Fail loudly if anything reintroduces the real library mid-suite."""
    from geopy.geocoders import Nominatim
    assert Nominatim is FakeNominatim, 'the real geopy leaked into the tests'
    yield


@pytest.fixture(scope="session")
def project_root():
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def real_gedcom(project_root):
    """The full Ancestry export. The edge cases live here, not in sample.ged."""
    path = os.path.join(project_root, 'gedcomexample.txt')
    if not os.path.exists(path):
        pytest.skip("gedcomexample.txt not present")
    with open(path, encoding='utf-8', errors='ignore') as f:
        return f.read()


@pytest.fixture(scope="session")
def parsed(real_gedcom):
    from app.gedcom_parser import GedcomParser
    parser = GedcomParser()
    parser.parse(real_gedcom)
    return parser
