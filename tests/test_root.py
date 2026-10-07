"""Re-rooting the chart.

The parser takes the first INDI in the file as the root, which is whoever the
exporting software wrote first rather than whoever is holding the file. The
root decides relationship labels and how the generation series is grouped, so
it has to be changeable, and changing it must not reach anyone else's session.
"""
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sessions import COOKIE_NAME, read_cookie_value, store
from app import ratelimit


# I1 first, so the parser roots on the child and re-rooting onto the parent is
# an actual change rather than a no-op.
TWO_GENERATIONS = b"""0 HEAD
0 @I1@ INDI
1 NAME Child /Smith/
1 SEX M
1 BIRT
2 DATE 1950
1 FAMC @F1@
0 @I2@ INDI
1 NAME Parent /Smith/
1 SEX M
1 BIRT
2 DATE 1920
1 FAMS @F1@
0 @F1@ FAM
1 HUSB @I2@
1 CHIL @I1@
0 TRLR
"""

OTHER_FAMILY = b"""0 HEAD
0 @I9@ INDI
1 NAME Someone /Else/
1 SEX F
1 BIRT
2 DATE 1910
0 TRLR
"""


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    """Rate limiters are process globals; give each test its own."""
    monkeypatch.setattr(ratelimit, 'uploads', ratelimit.RateLimiter(100, 60))
    monkeypatch.setattr(ratelimit, 'exports', ratelimit.RateLimiter(100, 60))
    monkeypatch.setattr(ratelimit, 'maps', ratelimit.RateLimiter(100, 60))


def upload(client, data=TWO_GENERATIONS):
    return client.post('/upload',
                       files={'file': ('tree.ged', io.BytesIO(data), 'text/plain')})


def parser_for(client):
    """The parser held against this client's session cookie."""
    return store.get(read_cookie_value(client.cookies.get(COOKIE_NAME)))


def test_first_individual_is_the_default_root():
    client = TestClient(app)
    assert upload(client).status_code == 200
    assert parser_for(client).root_id == '@I1@'


def test_re_rooting_moves_the_root_and_echoes_the_name():
    client = TestClient(app)
    upload(client)

    response = client.post('/api/root/@I2@')

    assert response.status_code == 200
    assert response.json() == {'root_id': '@I2@', 'name': 'Parent Smith'}
    assert parser_for(client).root_id == '@I2@'


def test_re_rooting_changes_what_the_relationship_is_measured_from():
    """The reason this is a server concern and not just a redraw."""
    client = TestClient(app)
    upload(client)
    parser = parser_for(client)

    # From the child, the parent is a parent.
    assert parser.calculate_relationship(parser.root_id, '@I2@') != 'Self'

    client.post('/api/root/@I2@')

    # From the parent, the parent is themselves.
    assert parser.calculate_relationship(parser.root_id, '@I2@') == 'Self'


def test_unknown_person_is_a_404():
    client = TestClient(app)
    upload(client)

    response = client.post('/api/root/@NOPE@')

    assert response.status_code == 404
    assert parser_for(client).root_id == '@I1@', 'root moved despite the 404'


def test_no_session_is_a_409_not_a_404():
    """409 is what the frontend watches for to prompt a fresh upload."""
    response = TestClient(app).post('/api/root/@I1@')

    assert response.status_code == 409
    assert 'upload' in response.json()['detail'].lower()


def test_re_rooting_does_not_reach_another_session():
    alice, bob = TestClient(app), TestClient(app)
    upload(alice, TWO_GENERATIONS)
    upload(bob, OTHER_FAMILY)

    assert alice.post('/api/root/@I2@').status_code == 200

    # Bob's file has no @I2@ at all, so a leak would show up as either a 200
    # here or a moved root.
    assert bob.post('/api/root/@I2@').status_code == 404
    assert parser_for(bob).root_id == '@I9@'
    assert parser_for(alice).root_id == '@I2@'
