"""A new user, start to finish, through the web app's endpoints."""
from test_web import post, signed_in


def test_register_verify_set_password_and_log_in_with_2fa(client, monkeypatch):
    signed_in(client)  # make sure the install is set up
    post(client, '/api/v1/auth/logout')

    import email_service
    mail = {}
    S = email_service.EmailService
    monkeypatch.setattr(S, 'send_registration_verification', lambda self, to, name, token, **kw: mail.update(verify=token) or True)
    monkeypatch.setattr(S, 'send_set_password', lambda self, to, name, token, **kw: mail.update(set=token) or True)
    monkeypatch.setattr(S, 'send_2fa_code', lambda self, to, name, code, **kw: mail.update(code=code) or True)
    monkeypatch.setattr(S, 'send_user_key_activated', lambda self, to, name, key, **kw: mail.update(key=key) or True)

    assert post(client, '/api/v1/register', {'name': 'Robin', 'email': 'robin@example.com'}).status_code == 201
    assert client.get(f"/api/v1/register/verify?t={mail['verify']}").status_code == 200
    assert post(client, '/api/v1/password/set', {'token': mail['set'], 'new_password': 'a long password'}).status_code == 200
    assert mail['key']

    r = post(client, '/api/v1/auth/login', {'email': 'robin@example.com', 'password': 'a long password'})
    assert r.get_json() == {'2fa_required': True, 'email': 'robin@example.com'}
    r = post(client, '/api/v1/auth/login/2fa', {'email': 'robin@example.com', 'code': mail['code'], 'remember_device': True})
    assert r.status_code == 200
    session = r.get_json()['session']
    assert session['user']['admin'] is False
    assert 'eph_device' in r.headers.get('Set-Cookie', '') or any('eph_device' in c for c in r.headers.getlist('Set-Cookie'))

    # Users can't reach administrator endpoints.
    assert client.get('/api/v1/admin/keys').status_code == 403
    assert client.get('/api/v1/admin/settings').status_code == 403
    # The remembered device skips the code next time.
    post(client, '/api/v1/auth/logout')
    r = post(client, '/api/v1/auth/login', {'email': 'robin@example.com', 'password': 'a long password'})
    assert 'session' in r.get_json()
    # The emailed key works as an API key.
    assert client.get('/api/v1/me', headers={'X-API-Key': mail['key']}).get_json()['identifier'] == 'robin@example.com'
