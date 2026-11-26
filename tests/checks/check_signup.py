exec(open('harness.py').read())
import re
import email_service
SENT = []
email_service.EmailService._send = lambda self, to, subject, text, html: SENT.append((to, subject, text)) or True
def last_link():
    return re.findall(r'https?://\S+', SENT[-1][2])[0]
ok = lambda m: print('  ok', m)

site_key, site_id = make_key('Studio', 'getstudioapps.com', key_type='domain')
other_key, other_id = make_key('Other', 'example.net', key_type='domain')
S = {'X-API-Key': site_key}
print('link origins')
r = client.get('/keys/self/link-origins', headers=S); assert r.status_code == 200 and r.get_json()['origins'] == [], r.get_json()
for origin, code in (('https://astro.getstudioapps.com', 200), ('https://getstudioapps.com', 200), ('https://example.net', 400),
                     ('http://astro.getstudioapps.com', 400), ('http://localhost:8080', 200), ('https://astro.getstudioapps.com/auth', 400),
                     ('https://evilgetstudioapps.com', 400)):
    r = client.put('/keys/self/link-origins', json={'origins': [origin]}, headers=S)
    assert r.status_code == code, (origin, r.status_code, r.get_json())
ok("own domain, subdomain and localhost accepted; other domains, http, paths and look-alikes refused")
r = client.put('/keys/self/link-origins', json={'origins': ['https://astro.getstudioapps.com']}, headers=H)
assert r.status_code == 403; ok('a user key gets 403')
client.put('/keys/self/link-origins', json={'origins': ['https://Astro.GetStudioApps.com:443']}, headers=S)
assert client.get('/keys/self/link-origins', headers=S).get_json()['origins'] == ['https://astro.getstudioapps.com']; ok('origins are normalised')
client.put('/keys/self/link-origins', json={'origins': ['https://other.example.net']}, headers={'X-API-Key': other_key})

print('sending links')
L = {'verify': 'https://astro.getstudioapps.com/auth/verify?t={token}'}
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': L}); assert r.status_code == 401, r.get_json(); ok('links without a key: 401')
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': L}, headers=H); assert r.status_code == 403; ok('links with a user key: 403')
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': L}, headers={'X-API-Key': other_key})
assert r.status_code == 422 and r.get_json()['error'] == 'link-not-allowed'; ok("another domain key's origin: 422 link-not-allowed")
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': {'verify': 'https://astro.getstudioapps.com/auth/verify'}}, headers=S)
assert r.status_code == 400; ok('a template without {token}: 400')
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': {'verify': 'https://{token}.getstudioapps.com/'}}, headers=S)
assert r.status_code in (400, 422); ok('{token} outside the path or query: refused')
assert not SENT; ok('no email was sent by any refused request')
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': {**L, 'set_password': 'https://astro.getstudioapps.com/auth/set-password?t={token}'}}, headers=S)
assert r.status_code == 201 and 'deliver' not in r.get_json(), r.get_json()
link = last_link(); assert link.startswith('https://astro.getstudioapps.com/auth/verify?t='), link
token = link.split('t=')[1]; ok('with the right domain key, the email links to the app')
assert db.get_account_home(db.get_api_key_by_identifier('ann@example.com')['id']) == site_id; ok('the account belongs to that domain key')

print('register/complete')
r = client.post('/register/complete', json={'token': token, 'password': 'short'}); assert r.status_code == 400; ok('password rules apply')
r = client.post('/register/complete', json={'token': token, 'password': 'correct horse'}); j = r.get_json()
assert r.status_code == 200 and j['api_key'] and j['active'] and j['identifier'] == 'ann@example.com', j
assert 'API key' in SENT[-1][1] and j['api_key'] in SENT[-1][2]; ok('activates, sets the password and returns the key; activation email still sent')
ann_key = j['api_key']
r = client.post('/register/complete', json={'token': token, 'password': 'correct horse'}); assert r.status_code == 400; ok('a used token is refused')
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'correct horse'}); assert r.get_json().get('2fa_required'); ok('the new password works for login')
with db.get_connection() as conn:
    conn.execute("UPDATE email_verifications SET expires_at = '2000-01-01 00:00:00'")
client.post('/register', json={'name': 'Bob', 'email': 'bob@example.com', 'links': L}, headers=S)
bob_token = last_link().split('t=')[1]
with db.get_connection() as conn:
    conn.execute("UPDATE email_verifications SET expires_at = '2000-01-01 00:00:00' WHERE token = ?", (bob_token,))
r = client.post('/register/complete', json={'token': bob_token, 'password': 'correct horse'}); assert r.status_code == 400; ok('an expired token is refused')

print('the portal flow without links')
n = len(SENT)
r = client.post('/register', json={'name': 'Cat', 'email': 'cat@example.com'}); assert r.status_code == 201
assert last_link().startswith('https://portal.example.org/verify.php?t='); cat_token = last_link().split('t=')[1]
r = client.get(f'/register/verify?t={cat_token}'); assert r.status_code == 200 and last_link().startswith('https://portal.example.org/set-password.php?t=')
assert db.get_account_home(db.get_api_key_by_identifier('cat@example.com')['id']) is None; ok('no links: portal links, two steps, as before')
client.post('/register', json={'name': 'Dee', 'email': 'dee@example.com', 'links': {**L, 'set_password': 'https://astro.getstudioapps.com/auth/set-password?t={token}'}}, headers=S)
r = client.get(f"/register/verify?t={last_link().split('t=')[1]}"); assert last_link().startswith('https://astro.getstudioapps.com/auth/set-password?t=')
ok('the two-step route keeps the app links for its set-password email')

print('forgot password')
r = client.post('/password/forgot', json={'email': 'ann@example.com', 'links': {'set_password': 'https://astro.getstudioapps.com/auth/set-password?t={token}'}}, headers=S)
assert r.status_code == 200 and last_link().startswith('https://astro.getstudioapps.com/auth/set-password?t='); ok('reset links go to the app')
r = client.post('/password/forgot', json={'email': 'ann@example.com', 'links': {'set_password': 'https://evil.example/x?t={token}'}}, headers=S)
assert r.status_code == 422; ok("a reset link to anywhere else is refused")
r = client.post('/password/forgot', json={'email': 'ann@example.com'}); assert last_link().startswith('https://portal.example.org/'); ok('without links, the portal')

print('health')
reg = client.get('/health').get_json()['registration']; assert reg == {'open': True, 'app_links': True, 'complete': True, 'email_delivery': True}, reg
ok('registration flags')

print('rotate')
n = len(SENT)
r = client.post('/me/rotate', json={'email_key': False}, headers={'X-API-Key': ann_key}); j = r.get_json()
assert r.status_code == 200 and j['api_key'] != ann_key and j['api_key'] not in SENT[-1][2] and 'replaced' in SENT[-1][1].lower(), SENT[-1]
ok('email_key false: the new key is returned and the email is only a notice'); ann_key = j['api_key']
r = client.post('/me/rotate', headers={'X-API-Key': ann_key}); j = r.get_json(); assert j['api_key'] in SENT[-1][2]; ann_key = j['api_key']
ok('without it, the key is emailed as before')
r = client.post('/keys/self/link-origins', headers=S); assert r.status_code == 405
print('all app-link checks passed')
