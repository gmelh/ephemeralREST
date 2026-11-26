"""Sites (domain keys), the defaults for new ones, the admin address, and the admin audit log."""
import json

from test_web import post, signed_in


def put(client, path, body, **headers):
    return client.put(path, data=json.dumps(body), headers={'Content-Type': 'application/json', **headers})


def test_a_new_site_gets_its_key_once_and_the_defaults(client):
    C = {'X-CSRF-Token': signed_in(client)}
    d = put(client, '/api/v1/admin/site-defaults',
            {'email_delivery_allowed': True, 'rate_per_minute': 30, 'rate_per_day': None}, **C)
    assert d.status_code == 200 and d.get_json()['rate_per_minute'] == 30
    r = post(client, '/api/v1/admin/sites', {'domain': 'https://Astro.Example.com/', 'name': 'Star Charts'}, **C)
    assert r.status_code == 201, r.get_json()
    made = r.get_json()
    key, site = made['api_key'], made['site']
    assert key.startswith('eph_') or len(key) > 20
    assert site['domain'] == 'astro.example.com' and site['name'] == 'Star Charts'
    assert site['origins'] == ['https://astro.example.com']
    assert site['email_delivery'] == {'allowed': True, 'mode': 'ephemeral'}
    assert site['rate_per_minute'] == 30
    # The key works as the site's own key, and isn't shown again.
    own = client.get('/api/v1/keys/self/link-origins', headers={'X-API-Key': key})
    assert own.status_code == 200 and own.get_json()['domain'] == 'astro.example.com'
    shown = client.get(f"/api/v1/admin/sites/{site['id']}").get_json()
    assert 'api_key' not in shown and key not in json.dumps(shown)
    assert post(client, '/api/v1/admin/sites', {'domain': 'astro.example.com'}, **C).status_code == 409
    assert post(client, '/api/v1/admin/sites', {'domain': 'not a domain'}, **C).status_code == 400


def test_a_site_can_be_changed_suspended_rotated_and_removed(client):
    C = {'X-CSRF-Token': signed_in(client)}
    made = post(client, '/api/v1/admin/sites', {'domain': 'charts.example.org'}, **C).get_json()
    site_id, old = made['site']['id'], made['api_key']
    r = put(client, f'/api/v1/admin/sites/{site_id}', {
        'name': 'Charts', 'origins': ['https://charts.example.org', 'https://www.charts.example.org'],
        'email_delivery_allowed': True, 'rate_per_hour': 500, 'active': False}, **C)
    assert r.status_code == 200, r.get_json()
    site = r.get_json()
    assert site['name'] == 'Charts' and len(site['origins']) == 2 and site['email_delivery']['allowed']
    assert site['rate_per_hour'] == 500 and site['active'] is False
    assert client.get('/api/v1/keys/self/link-origins', headers={'X-API-Key': old}).status_code == 401
    bad = put(client, f'/api/v1/admin/sites/{site_id}', {'origins': ['https://evil.example.net']}, **C)
    assert bad.status_code in (400, 422)
    put(client, f'/api/v1/admin/sites/{site_id}', {'active': True}, **C)
    rotated = post(client, f'/api/v1/admin/sites/{site_id}/rotate', **C).get_json()
    assert rotated['api_key'] != old
    assert client.get('/api/v1/keys/self/link-origins', headers={'X-API-Key': old}).status_code == 401
    assert client.get('/api/v1/keys/self/link-origins', headers={'X-API-Key': rotated['api_key']}).status_code == 200
    gone = client.delete(f'/api/v1/admin/sites/{site_id}', headers=C)
    assert gone.status_code == 200
    assert client.get(f'/api/v1/admin/sites/{site_id}').status_code == 404


def test_a_sites_accounts_are_listed_and_can_be_moved(client):
    import routes
    C = {'X-CSRF-Token': signed_in(client)}
    a = post(client, '/api/v1/admin/sites', {'domain': 'a.example.com'}, **C).get_json()['site']
    b = post(client, '/api/v1/admin/sites', {'domain': 'b.example.com'}, **C).get_json()['site']
    post(client, '/api/v1/register', {'name': 'Kim', 'email': 'kim@example.com'})
    kim = routes.db_manager.get_api_key_by_identifier('kim@example.com')
    routes.db_manager.set_account_home(kim['id'], a['id'])
    found = client.get(f"/api/v1/admin/sites/{a['id']}").get_json()
    assert found['accounts'] == 1 and found['account_list'][0]['identifier'] == 'kim@example.com'
    r = put(client, f"/api/v1/admin/keys/{kim['id']}/home", {'home_key_id': b['id']}, **C)
    assert r.status_code == 200
    listed = {s['domain']: s['accounts'] for s in client.get('/api/v1/admin/sites').get_json()['sites']}
    assert listed['a.example.com'] == 0 and listed['b.example.com'] == 1
    released = client.delete(f"/api/v1/admin/sites/{b['id']}", headers=C).get_json()
    assert released['accounts_released'] == 1
    assert routes.db_manager.get_account_home(kim['id']) is None


def test_changes_are_recorded_without_secrets(client):
    C = {'X-CSRF-Token': signed_in(client)}
    site = post(client, '/api/v1/admin/sites', {'domain': 'log.example.com'}, **C).get_json()['site']
    put(client, f"/api/v1/admin/sites/{site['id']}", {'rate_per_day': 900}, **C)
    post(client, '/api/v1/admin/smtp', {'host': 'smtp.example.com', 'port': 587, 'username': 'u',
                                        'password': 'hunter2-secret', 'from_email': 'a@example.com'}, **C)
    rows = client.get('/api/v1/admin/audit').get_json()['data']
    actions = [r['action'] for r in rows]
    assert actions[:3] == ['set_smtp', 'update_site', 'create_site'], actions
    assert 'hunter2-secret' not in json.dumps(rows)
    assert rows[1]['target_type'] == 'site' and rows[1]['target_id'] == site['id']
    assert '"rate_per_day":900' in rows[1]['detail']
    assert rows[0]['admin_email'] == 'admin@example.com'
    # Reads aren't changes.
    client.get('/api/v1/admin/sites')
    assert client.get('/api/v1/admin/audit').get_json()['data'][0]['action'] == 'set_smtp'
    # The site's own page shows its changes.
    page = client.get(f"/api/v1/admin/sites/{site['id']}").get_json()
    assert [a['action'] for a in page['activity']] == ['update_site', 'create_site']
    # With email set up, administrators would need a code to log in: put it back.
    assert client.delete('/api/v1/admin/smtp', headers=C).status_code == 200


def test_sites_are_admin_only(client):
    signed_in(client)
    post(client, '/api/v1/auth/logout')
    assert client.get('/api/v1/admin/sites').status_code == 401


def test_administration_answers_only_at_the_admin_address(client, monkeypatch):
    from config import Config
    C = {'X-CSRF-Token': signed_in(client)}
    monkeypatch.setattr(Config, 'ADMIN_URL', 'https://admin.example.com')
    # Elsewhere, /admin isn't there at all (the session's cookie belongs to localhost here).
    assert client.get('/api/v1/admin/sites').status_code == 404
    assert client.get('/api/v1/admin/keys').status_code == 404
    assert client.get('/api/v1/admin/settings').status_code == 404
    status = client.get('/api/v1/auth/status').get_json()['config']
    assert status['admin_here'] is False and status['admin_url'] == 'https://admin.example.com'
    # Everything else still answers.
    assert client.get('/api/v1/ping').status_code == 200
    assert client.get('/api/v1/me').status_code == 200
    # At the admin address it does.
    there = client.get('/api/v1/admin/sites', headers={'Host': 'admin.example.com'})
    assert there.status_code in (200, 401)  # 401: the test session's cookie is for another host
    assert client.get('/api/v1/auth/status', headers={'Host': 'admin.example.com'}).get_json()['config']['admin_here']
