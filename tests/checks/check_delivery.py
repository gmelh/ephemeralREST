exec(open('harness.py').read())
import re, email_service
SENT = []
email_service.EmailService._send = lambda self, to, subject, text, html: SENT.append((to, subject, text)) or True
ok = lambda m: print('  ok', m)
admin_key, admin_id = make_key('Admin', 'admin@example.com')
db.update_api_key(admin_id, admin=1)
A = {'X-API-Key': admin_key}
site_key, site_id = make_key('Studio', 'getstudioapps.com', key_type='domain')
other_key, other_id = make_key('Other', 'example.net', key_type='domain')
S = {'X-API-Key': site_key}; O = {'X-API-Key': other_key}
client.put('/keys/self/link-origins', json={'origins': ['https://astro.getstudioapps.com']}, headers=S)
client.put('/keys/self/link-origins', json={'origins': ['https://astro.example.net']}, headers=O)
L = {'verify': 'https://astro.getstudioapps.com/auth/verify?t={token}', 'set_password': 'https://astro.getstudioapps.com/auth/set-password?t={token}'}

print('asking to deliver')
r = client.put('/keys/self/email-delivery', json={'mode': 'self'}, headers=S); assert r.status_code == 403 and r.get_json()['error'] == 'email-delivery-not-allowed'
ok("refused until the admin allows it")
r = client.put(f'/admin/keys/{site_id}/email-delivery', json={'allowed': True}, headers=H); assert r.status_code == 403; ok('only an admin can allow it')
r = client.put(f'/admin/keys/{site_id}/email-delivery', json={'allowed': True}, headers=A); assert r.get_json() == {'allowed': True, 'mode': 'ephemeral'}, r.get_json()
r = client.put('/keys/self/email-delivery', json={'mode': 'self'}, headers=S); assert r.get_json()['mode'] == 'self'; ok('allowed, then switched on by the key')
client.put(f'/admin/keys/{other_id}/email-delivery', json={'allowed': True}, headers=A); client.put('/keys/self/email-delivery', json={'mode': 'self'}, headers=O)

print('the site delivers for its own accounts')
n = len(SENT)
r = client.post('/register', json={'name': 'Ann', 'email': 'ann@example.com', 'links': L}, headers=S); d = r.get_json()['deliver']
assert d['kind'] == 'verify' and d['token'] and d['expires_at'] and len(SENT) == n; ok('register: a verify token, and no email')
r = client.post('/register/complete', json={'token': d['token'], 'password': 'correct horse'}, headers=S); j = r.get_json()
assert j['deliver'] == {'kind': 'welcome'} and len(SENT) == n and j['api_key']; ok('complete: welcome, and the key is not emailed')
ann_key = j['api_key']
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'correct horse'}, headers=S); j = r.get_json()
assert j['2fa_required'] and j['deliver']['kind'] == 'login_code' and re.fullmatch(r'\d{6}', j['deliver']['code']) and len(SENT) == n
r = client.post('/login/2fa', json={'email': 'ann@example.com', 'code': j['deliver']['code']}); assert r.status_code == 200 and r.get_json()['api_key'] == ann_key
ok('login: the code comes back to the site, and it works')
r = client.post('/password/forgot', json={'email': 'ann@example.com', 'links': {'set_password': L['set_password']}}, headers=S)
d = r.get_json()['deliver']; assert d['kind'] == 'password_reset' and len(SENT) == n
r = client.post('/password/set', json={'token': d['token'], 'new_password': 'battery staple'}, headers=S); assert r.status_code == 200 and len(SENT) == n; ok('forgot password: a reset token, and it works, with nothing emailed')
r = client.post('/me/rotate', json={'email_key': False}, headers={'X-API-Key': ann_key, 'X-Site-Key': site_key}); j = r.get_json()
assert j['deliver'] == {'kind': 'key_replaced'} and len(SENT) == n; ok('rotate with the site key: key_replaced, no email'); ann_key = j['api_key']

print('never for anyone else')
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'battery staple'}, headers=O); j = r.get_json()
assert 'deliver' not in j and len(SENT) == n + 1 and re.search(r'\d{6}', SENT[-1][2]); ok("another site's key: no code, ephemeralREST emails it")
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'battery staple'}, headers={'X-API-Key': ann_key}); assert 'deliver' not in r.get_json(); ok('a user key: no code')
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'battery staple'}); assert 'deliver' not in r.get_json(); ok('no key: no code')
r = client.post('/password/forgot', json={'email': 'ann@example.com'}, headers=O); assert 'deliver' not in r.get_json(); ok("another site can't get a reset token")
client.post('/register', json={'name': 'Cat', 'email': 'cat@example.com'})
cat_token = re.findall(r't=(\S+)', SENT[-1][2])[0]
client.post('/register/complete', json={'token': cat_token, 'password': 'correct horse'})
for hdr in (S, O):
    r = client.post('/login', json={'email': 'cat@example.com', 'password': 'correct horse'}, headers=hdr); assert 'deliver' not in r.get_json()
    r = client.post('/password/forgot', json={'email': 'cat@example.com'}, headers=hdr); assert 'deliver' not in r.get_json()
ok('an account made on the portal: no site ever gets its codes or tokens')
client.put(f'/admin/keys/{site_id}/email-delivery', json={'allowed': False}, headers=A)
assert db.get_email_delivery(site_id) == {'allowed': False, 'mode': 'ephemeral'}
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'battery staple'}, headers=S); assert 'deliver' not in r.get_json()
ok("taking the permission away stops it at once, and ephemeralREST emails again")
client.put(f'/admin/keys/{site_id}/email-delivery', json={'allowed': True}, headers=A); client.put('/keys/self/email-delivery', json={'mode': 'self'}, headers=S)
client.put('/keys/self/email-delivery', json={'mode': 'ephemeral'}, headers=S)
r = client.post('/login', json={'email': 'ann@example.com', 'password': 'battery staple'}, headers=S); assert 'deliver' not in r.get_json()
ok('switching back to ephemeral restores ephemeralREST emails')
r = client.post('/register', json={'name': 'Ann again', 'email': 'ann@example.com', 'links': L}, headers=S)
assert 'deliver' not in r.get_json() and r.status_code == 200; ok('registering an existing email gives nothing away')
r = client.put(f'/admin/keys/{db.get_api_key_by_identifier("cat@example.com")["id"]}/home', json={'home_key_id': site_id}, headers=A); assert r.get_json()['home_key_id'] == site_id
r = client.put(f'/admin/keys/{db.get_api_key_by_identifier("cat@example.com")["id"]}/home', json={'home_key_id': admin_id}, headers=A); assert r.status_code == 400
ok('the admin can move an account to a site, and only to a domain key')
print('all delivery checks passed')
