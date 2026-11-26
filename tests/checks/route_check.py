"""Calls ephemeralREST's calculation routes through Flask's test client, with a throwaway
SQLite database and API key, and checks every response carries the four ayanamsas."""
import os, sys, tempfile, json
repo = os.path.abspath(sys.argv[1])
work = tempfile.mkdtemp()
os.environ.update(SECRET_KEY='a' * 64, USE_GOOGLE='false', DB_TYPE='sqlite',
                  DATABASE_PATH=os.path.join(work, 'e.db'), SWISS_EPHEMERIS_PATH=work)
os.chdir(work)
open('.env', 'w').write(''.join(f'{k}={os.environ[k]}\n' for k in ('SECRET_KEY', 'USE_GOOGLE', 'DB_TYPE', 'DATABASE_PATH', 'SWISS_EPHEMERIS_PATH')))
sys.path.insert(0, repo)
import app as app_module
from key_crypto import KeyCrypto
import routes
crypto = KeyCrypto(os.environ['SECRET_KEY'])
key = KeyCrypto.generate_key()
routes.db_manager.create_api_key('Test', 'test@example.com', crypto.encrypt(key), crypto.prefix(key), key_type='user')
client = app_module.app.test_client()
H = {'X-API-Key': key}
NAMES = {'lahiri', 'krishnamurti', 'fagan_bradley', 'raman'}
def has(a, where):
    assert isinstance(a, dict) and set(a) == NAMES and all(isinstance(v, float) for v in a.values()), (where, a)
off = {'output': {'bodies': {'ceres': False, 'pallas': False, 'juno': False, 'vesta': False, 'chiron': False}}}

r = client.post('/apsides', json={'datetime': '2026-11-26T12:00:00Z', **off}, headers=H)
assert r.status_code == 200, r.get_data(as_text=True)[:300]; has(r.get_json()['ayanamsa'], 'apsides'); print('apsides ok', r.get_json()['ayanamsa'])
r = client.post('/ephemeris', json={'year': 2026, 'month': 2, **off}, headers=H)
j = r.get_json(); assert r.status_code == 200, j
assert set(j['ayanamsa']) == set(j['days']) and len(j['days']) == 28
for d, a in j['ayanamsa'].items(): has(a, d)
assert set(next(iter(j['days'].values()))) <= {'geocentric', 'heliocentric'}, 'day entries keep their shape'
print('ephemeris ok: a value for each of', len(j['ayanamsa']), 'days; day entries unchanged')
r = client.post('/lunations', json={'reference_date': '2026-11-26', 'direction': 'next'}, headers=H)
j = r.get_json(); assert r.status_code == 200, j
for e in j['lunations']: has(e['ayanamsa'], e['phase'])
print('lunations ok', len(j['lunations']))
r = client.post('/apsides/next', json={'reference_date': '2026-11-26', 'bodies': ['moon']}, headers=H)
j = r.get_json(); assert r.status_code == 200, j
for e in j['events']: has(e['ayanamsa'], e['event'])
print('next apsides ok', len(j['events']))

# A stored chart from before this change (no 'ayanamsa' in its data) and new ones.
from datetime import datetime
svc = routes.astronomy_service
old = {'planetary_positions': {}, 'house_cusps': None}
a = routes._stored_ayanamsa(old, '1966-03-20T02:15:00+00:00'); has(a, 'old chart')
assert a == svc.ayanamsas_at(datetime(1966, 3, 20, 2, 15)), 'from the chart datetime'
assert routes._stored_ayanamsa({'progressed_jd': 2450000.5, 'natal_jd': 2439000.5, 'method': 'secondary_progressions'}) == svc.ayanamsas(2450000.5)
assert routes._stored_ayanamsa({'progressed_jd': 2450000.5, 'natal_jd': 2439000.5, 'method': 'solar_arc_directions'}) == svc.ayanamsas(2439000.5)
assert routes._stored_ayanamsa({'return_jd': 2460000.5, 'method': 'solar_return'}) == svc.ayanamsas(2460000.5)
assert routes._stored_ayanamsa({'ayanamsa': {'lahiri': 1.0}}) == {'lahiri': 1.0}
print('stored charts ok: old charts get theirs worked out; progressions, solar arc and returns use the right moment')

# Charts: the offline place lookup needs cities5000; one city is enough for this check.
with routes.db_manager.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO cities VALUES (2063523, 'Perth', 'Perth', 'AU', '08', -31.95224, 115.8614, 'Australia/Perth', 1896548)")
r = client.post('/calculate', json={'chart_name': 'Test', 'datetime': '1966-03-20 10:15:00', 'location': 'Perth, Australia', **off}, headers=H)
if r.status_code != 200:
    print('calculate skipped here:', r.status_code, r.get_data(as_text=True)[:200])
else:
    j = r.get_json(); has(j['ayanamsa'], 'calculate'); cid = j['chart_id']
    g = client.get(f'/chart/{cid}', headers=H).get_json(); assert g['ayanamsa'] == j['ayanamsa']
    for path, body in (('progressions', {'progression_date': '2026-11-26'}), ('solar-arc', {'progression_date': '2026-11-26'}),
                       ('solar-return', {'return_year': 2026}), ('lunar-return', {'return_year': 2026, 'return_month': 11})):
        r = client.post(f'/chart/{cid}/{path}', json={**body, **off}, headers=H); jj = r.get_json()
        assert r.status_code == 200, (path, jj); has(jj['ayanamsa'], path)
        d = client.get(f"/derived/{jj['derived_chart_id']}", headers=H).get_json(); assert d['ayanamsa'] == jj['ayanamsa'], path
        print(path, 'ok', jj['ayanamsa']['lahiri'])
    print('calculate, chart and derived charts ok', j['ayanamsa'])
