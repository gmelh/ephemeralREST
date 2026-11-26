import sys, json
exec(open('harness.py').read())
import swisseph as swe
c = client.post('/calculate', json={'chart_name': 'P', 'datetime': '1966-03-20 10:15:00', 'location': 'Perth, Australia', 'house_system': 'placidus', **OFF}, headers=H).get_json()
cid = c['chart_id']
natal_sun = c['planetary_positions']['geocentric']['sun']['longitude']
natal_jd = swe.julday(1966, 3, 20, 2.25)
expect_jd = natal_jd + (swe.julday(2026, 11, 26, 0.0) - natal_jd) / 365.24219
p = client.post(f'/chart/{cid}/progressions', json={'progression_date': '2026-11-26', **OFF}, headers=H).get_json()
assert abs(p['progressed_jd'] - expect_jd) < 1e-9, (p['progressed_jd'], expect_jd)
y, m, d, _ = swe.revjul(p['progressed_jd'])
psun = p['planetary_positions']['geocentric']['sun']['longitude']
assert abs(psun - swe.calc_ut(expect_jd, swe.SUN, swe.FLG_SWIEPH)[0][0]) < 1e-6
assert round(p['years_elapsed'], 2) == 60.69 and p['days_elapsed'] == 22166
print(f'progressions ok: progressed date {y}-{m:02}-{d:02}, Sun {psun:.3f} (natal {natal_sun:.3f}), years {p["years_elapsed"]:.3f}')
s = client.post(f'/chart/{cid}/solar-arc', json={'progression_date': '2026-11-26', **OFF}, headers=H).get_json()
arc = (psun - natal_sun) % 360
assert abs(s['solar_arc_geo'] - arc) < 1e-5, (s['solar_arc_geo'], arc)
assert abs(s['planetary_positions']['geocentric']['moon']['longitude'] - (c['planetary_positions']['geocentric']['moon']['longitude'] + arc) % 360) < 1e-5
print(f"solar arc ok: arc {s['solar_arc_geo']:.3f}, everything moved by it")
# An old-style saved progression: the wrong (target-date) data, without progression_method.
import json as _j
with db.get_connection() as conn:
    row = conn.execute('SELECT chart_data FROM derived_charts WHERE id = ?', (p['derived_chart_id'],)).fetchone()
    data = _j.loads(row[0]); data.pop('progression_method'); data['planetary_positions']['geocentric']['sun']['longitude'] = 244.331
    conn.execute('UPDATE derived_charts SET chart_data = ? WHERE id = ?', (_j.dumps(data), p['derived_chart_id']))
d = client.get(f"/derived/{p['derived_chart_id']}", headers=H).get_json()
assert abs(d['planetary_positions']['geocentric']['sun']['longitude'] - psun) < 1e-6 and d['progression_method'] == 'day_for_a_year', d.get('progression_method')
with db.get_connection() as conn:
    assert 'day_for_a_year' in conn.execute('SELECT chart_data FROM derived_charts WHERE id = ?', (p['derived_chart_id'],)).fetchone()[0]
print('an old saved progression is recalculated when read, and saved in place')
