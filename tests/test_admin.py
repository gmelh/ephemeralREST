"""Administration through the web app's session."""
import json

from test_web import H, post, signed_in


def test_admin_can_manage_a_registered_key(client):
    csrf = signed_in(client)
    C = {'X-CSRF-Token': csrf}
    r = post(client, '/api/v1/register', {'name': 'Pat', 'email': 'pat@example.com'})
    assert r.status_code in (200, 201)
    keys = client.get('/api/v1/admin/keys?inactive=1').get_json()['keys']
    pat = next(k for k in keys if k['identifier'] == 'pat@example.com')
    assert 'key_enc' not in pat

    r = post(client, f"/api/v1/admin/keys/{pat['id']}/limits",
             {'rate_per_minute': 5, 'rate_per_hour': None, 'rate_per_day': 100}, **C)
    assert r.status_code == 200
    detail = client.get(f"/api/v1/admin/keys/{pat['id']}").get_json()
    assert detail['rate_per_minute'] == 5 and detail['rate_per_hour'] is None

    assert post(client, f"/api/v1/admin/keys/{pat['id']}/enable", **C).status_code == 200
    assert post(client, f"/api/v1/admin/keys/{pat['id']}/disable", **C).status_code == 200


def test_admin_cannot_lock_themselves_out(client):
    csrf = signed_in(client)
    me = client.get('/api/v1/me').get_json()
    C = {'X-CSRF-Token': csrf}
    assert post(client, f"/api/v1/admin/keys/{me['id']}/disable", **C).status_code == 400
    r = client.delete(f"/api/v1/admin/keys/{me['id']}", headers=C)
    assert r.status_code == 400


def test_registration_can_be_closed(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'ALLOW_REGISTRATION', False)
    r = post(client, '/api/v1/register', {'name': 'Sam', 'email': 'sam@example.com'})
    assert r.status_code == 403


def test_settings_and_tools_are_admin_only(client):
    csrf = signed_in(client)
    s = client.get('/api/v1/admin/settings').get_json()
    assert s['version'] and 'database' in s
    assert client.get('/api/v1/admin/federation-test/services').get_json()['services'] == []
    t = client.get('/api/v1/admin/email-templates/2fa-code').get_json()
    assert '{code}' in t['body_text']
    post(client, '/api/v1/auth/logout')
    assert client.get('/api/v1/admin/settings').status_code == 401
    assert client.get('/api/v1/admin/email-templates/2fa-code').status_code == 401


def test_federation_test_sends_with_your_key(client, monkeypatch):
    csrf = signed_in(client)
    C = {'X-CSRF-Token': csrf}
    r = post(client, '/api/v1/admin/federated-services',
             {'slug': 'demo', 'display_name': 'Demo', 'base_url': 'https://demo.invalid'}, **C)
    assert r.status_code == 201
    seen = {}

    class Fake:
        status_code = 200
        text = '{"ok": true}'
        url = 'https://demo.invalid/ping'
        headers = {'Content-Type': 'application/json'}

    def fake_request(method, url, **kw):
        seen.update(method=method, url=url, key=kw['headers']['X-API-Key'])
        return Fake()

    import admin_tools
    monkeypatch.setattr(admin_tools.requests, 'request', fake_request)
    r = post(client, '/api/v1/admin/federation-test/send', {'service': 'demo', 'method': 'GET', 'path': '/ping'}, **C)
    assert r.status_code == 200 and r.get_json()['status'] == 200
    assert seen['url'] == 'https://demo.invalid/ping' and len(seen['key']) > 20
    assert post(client, '/api/v1/admin/federation-test/send', {'service': 'demo', 'path': 'http://x'}, **C).status_code == 400
