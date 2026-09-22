"""Session isolation, expiry and limits, through the real HTTP app.

The point of all this is that two visitors never see each other's family, and
that nobody's tree outlives their visit.
"""
import io
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sessions import COOKIE_NAME, SessionStore, make_cookie_value, read_cookie_value
from app import ratelimit


TINY = b"""0 HEAD
0 @I1@ INDI
1 NAME Alpha /One/
1 SEX M
1 BIRT
2 DATE 1900
0 TRLR
"""

OTHER = b"""0 HEAD
0 @I9@ INDI
1 NAME Beta /Two/
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


def upload(client, data=TINY, name='tree.ged'):
    return client.post('/upload', files={'file': (name, io.BytesIO(data), 'text/plain')})


# --- isolation ---------------------------------------------------------------

def test_two_visitors_do_not_see_each_others_tree():
    """The whole reason the global parser had to go."""
    alice, bob = TestClient(app), TestClient(app)

    assert upload(alice, TINY).status_code == 200
    assert upload(bob, OTHER).status_code == 200

    a = alice.post('/upload', files={'file': ('t.ged', io.BytesIO(TINY), 'text/plain')}).json()
    b = bob.post('/upload', files={'file': ('t.ged', io.BytesIO(OTHER), 'text/plain')}).json()

    assert [n['name'] for n in a['nodes'] if n['type'] == 'person'] == ['Alpha One']
    assert [n['name'] for n in b['nodes'] if n['type'] == 'person'] == ['Beta Two']

    # Bob's id must not resolve against Alice's tree.
    bob_id = [n['id'] for n in b['nodes'] if n['type'] == 'person'][0]
    assert alice.get(f'/api/export/pdf/{bob_id}').status_code == 404


def test_export_without_uploading_asks_for_a_file():
    client = TestClient(app)
    r = client.get('/api/export/pdf/@I1@')
    assert r.status_code == 409
    assert 'upload' in r.json()['detail'].lower()


def test_map_without_uploading_is_rejected():
    assert TestClient(app).get('/api/export/map').status_code == 409


def test_upload_sets_an_httponly_session_cookie():
    client = TestClient(app)
    r = upload(client)
    cookie = r.headers['set-cookie']
    assert COOKIE_NAME in cookie
    assert 'HttpOnly' in cookie
    assert 'Path=/' in cookie


def test_reupload_reuses_the_same_session():
    client = TestClient(app)
    upload(client)
    first = client.cookies.get(COOKIE_NAME)
    upload(client, OTHER)
    assert client.cookies.get(COOKIE_NAME) == first


def test_session_can_be_cleared_on_request():
    client = TestClient(app)
    upload(client)
    person = [n for n in upload(client).json()['nodes'] if n['type'] == 'person'][0]
    assert client.get(f'/api/export/pdf/{person["id"]}').status_code == 200

    assert client.delete('/api/session').status_code == 200
    assert client.get(f'/api/export/pdf/{person["id"]}').status_code == 409


# --- cookie integrity --------------------------------------------------------

def test_forged_cookie_is_rejected():
    assert read_cookie_value('someid.deadbeef') is None
    assert read_cookie_value('nosignature') is None
    assert read_cookie_value('') is None
    good = make_cookie_value('abc123')
    assert read_cookie_value(good) == 'abc123'


def test_tampering_with_a_real_cookie_invalidates_it():
    client = TestClient(app)
    upload(client)
    real = client.cookies.get(COOKIE_NAME)
    client.cookies.set(COOKIE_NAME, real[:-1] + ('0' if real[-1] != '0' else '1'))
    assert client.get('/api/export/map').status_code == 409


# --- store mechanics ---------------------------------------------------------

def test_store_expires_idle_sessions():
    store = SessionStore(ttl=0.05, max_sessions=10)
    sid = store.new_session()
    store.put(sid, object())
    assert store.get(sid) is not None
    time.sleep(0.08)
    assert store.get(sid) is None


def test_reading_a_session_keeps_it_alive():
    store = SessionStore(ttl=0.15, max_sessions=10)
    sid = store.new_session()
    store.put(sid, object())
    for _ in range(4):
        time.sleep(0.05)
        assert store.get(sid) is not None, 'activity should refresh the TTL'


def test_store_sheds_least_recently_used_over_the_cap():
    store = SessionStore(ttl=600, max_sessions=3)
    ids = []
    for _ in range(3):
        sid = store.new_session()
        store.put(sid, object())
        ids.append(sid)
        time.sleep(0.01)

    store.get(ids[0])  # touch the oldest so it is no longer least recent
    newest = store.new_session()
    store.put(newest, object())

    assert len(store) == 3
    assert store.get(ids[0]) is not None
    assert store.get(newest) is not None
    assert store.get(ids[1]) is None, 'least recently used should have gone'


def test_sweep_drops_expired_entries():
    store = SessionStore(ttl=0.05, max_sessions=10)
    store.put(store.new_session(), object())
    assert len(store) == 1
    time.sleep(0.08)
    store.sweep()
    assert len(store) == 0


# --- upload guards -----------------------------------------------------------

def test_oversized_upload_is_refused(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'MAX_UPLOAD_BYTES', 1024)
    client = TestClient(app)
    r = upload(client, b'0' * 5000)
    assert r.status_code == 413
    assert 'larger than' in r.json()['detail']


def test_empty_upload_is_refused():
    assert upload(TestClient(app), b'').status_code == 400


def test_non_gedcom_upload_is_refused():
    r = upload(TestClient(app), b'this is just prose, not a family tree', 'notes.txt')
    assert r.status_code == 422
    assert 'GEDCOM' in r.json()['detail']


# --- rate limiting -----------------------------------------------------------

def test_map_endpoint_is_rate_limited(monkeypatch):
    monkeypatch.setattr(ratelimit, 'maps', ratelimit.RateLimiter(2, 3600))
    client = TestClient(app)
    upload(client)
    codes = [client.get('/api/export/map').status_code for _ in range(3)]
    assert codes[-1] == 429, codes
    assert 'Retry-After' in client.get('/api/export/map').headers


def test_rate_limiter_windows_expire():
    limiter = ratelimit.RateLimiter(1, 0.05)
    assert limiter.check('k')[0] is True
    assert limiter.check('k')[0] is False
    time.sleep(0.08)
    assert limiter.check('k')[0] is True, 'window should have rolled over'


def test_rate_limits_are_per_caller():
    limiter = ratelimit.RateLimiter(1, 60)
    assert limiter.check('caller-a')[0] is True
    assert limiter.check('caller-b')[0] is True, 'one caller must not limit another'
    assert limiter.check('caller-a')[0] is False


def test_forwarded_header_identifies_the_caller():
    class Req:
        headers = {'x-forwarded-for': '203.0.113.7, 10.0.0.1'}
        client = type('C', (), {'host': '10.0.0.1'})()

    assert ratelimit.client_key(Req()) == '203.0.113.7'


# --- health ------------------------------------------------------------------

def test_healthz_reports_session_count():
    body = TestClient(app).get('/healthz').json()
    assert body['status'] == 'ok'
    assert isinstance(body['sessions'], int)
