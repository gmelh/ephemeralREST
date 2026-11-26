"""The /api/v1 prefix, the web app, and browser sessions."""
import json

H = {'Content-Type': 'application/json'}
ADMIN = {'name': 'Admin', 'email': 'admin@example.com', 'password': 'correct horse battery'}


def post(client, path, body=None, **headers):
    return client.post(path, data=json.dumps(body or {}), headers={**H, **headers})


def signed_in(client):
    """Set up the install (first test to run) or log in; returns the CSRF token."""
    status = client.get('/api/v1/auth/status').get_json()
    if status['setup_required']:
        r = post(client, '/api/v1/auth/setup', ADMIN)
        assert r.status_code == 201
        assert r.get_json()['api_key']
        return r.get_json()['session']['csrf_token']
    r = post(client, '/api/v1/auth/login', {'email': ADMIN['email'], 'password': ADMIN['password']})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['session']['csrf_token']


def test_prefix_and_root_both_answer(client):
    assert client.get('/ping').get_json()['status'] == 'ok'
    assert client.get('/api/v1/ping').get_json()['version']


def test_unknown_api_paths_stay_json(client):
    r = client.get('/api/v1/keys', headers={'Accept': 'text/html'})
    assert r.status_code == 404 and r.is_json
    r = client.get('/no-such-thing')
    assert r.status_code == 404 and r.is_json


def test_old_portal_links_redirect(client):
    r = client.get('/set-password.php?t=abc')
    assert r.status_code == 301
    assert r.headers['Location'].endswith('/set-password?t=abc')


def test_session_cookie_authenticates_and_needs_csrf(client):
    csrf = signed_in(client)
    me = client.get('/api/v1/me')
    assert me.status_code == 200 and me.get_json()['admin'] is True
    assert post(client, '/api/v1/me/output', {'output_config': None}).status_code == 403
    assert post(client, '/api/v1/me/output', {'output_config': None}, **{'X-CSRF-Token': csrf}).status_code == 200


def test_session_ignored_from_other_origins(client):
    signed_in(client)
    assert client.get('/api/v1/me', headers={'Origin': 'https://evil.example'}).status_code == 401
    assert post(client, '/api/v1/auth/login', {'email': ADMIN['email'], 'password': 'x'},
                Origin='https://evil.example').status_code == 403


def test_logout_ends_the_session(client):
    signed_in(client)
    assert post(client, '/api/v1/auth/logout').status_code == 200
    assert client.get('/api/v1/me').status_code == 401


def test_rotating_your_own_key_keeps_you_signed_in(client):
    csrf = signed_in(client)
    r = post(client, '/api/v1/me/rotate', **{'X-CSRF-Token': csrf})
    assert r.status_code == 200 and r.get_json()['api_key']
    assert client.get('/api/v1/me').status_code == 200
    # The new key works as an API key, too.
    assert client.get('/me', headers={'X-API-Key': r.get_json()['api_key']}).status_code == 200


def test_wrong_password_is_refused(client):
    r = post(client, '/api/v1/auth/login', {'email': ADMIN['email'], 'password': 'nope'})
    assert r.status_code == 401
    assert 'session' not in r.get_json()


def test_api_key_login_still_returns_the_key(client):
    signed_in(client)
    r = post(client, '/login', {'email': ADMIN['email'], 'password': ADMIN['password']})
    assert r.status_code == 200 and r.get_json()['api_key']


def test_page_loads_get_the_web_app(client):
    import os, web
    if not os.path.isfile(os.path.join(web.WEB_DIR, 'index.html')):
        import pytest
        pytest.skip('web app not built')
    for path in ('/', '/keys', '/verify', '/login'):
        r = client.get(path, headers={'Accept': 'text/html,application/xhtml+xml'})
        assert r.status_code == 200 and b'<div id="app">' in r.data, path
    assert client.get('/app.js').status_code == 200
    # An API GET route still answers JSON, even for a browser.
    assert client.get('/ping', headers={'Accept': 'text/html'}).is_json
