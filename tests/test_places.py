"""Places: region and country names from GeoNames, so a person can tell places apart, and a place
chosen from the suggestions is the place a chart is made for."""
import logging
import os

import pytest

from test_web import post, signed_in

# A few GeoNames cities5000 lines (19 tab-separated columns), with names that repeat.
CITIES = [
    # id, name, ascii, lat, lon, country, admin1, admin2, population, timezone
    (2063523, 'Perth', 'Perth', -31.95224, 115.8614, 'AU', '08', '', 1896548, 'Australia/Perth'),
    (2640358, 'Perth', 'Perth', 56.39522, -3.43139, 'GB', 'SCT', 'P8', 47180, 'Europe/London'),
    (4250542, 'Springfield', 'Springfield', 39.80172, -89.64371, 'US', 'IL', '167', 116565, 'America/Chicago'),
    (4409896, 'Springfield', 'Springfield', 37.21533, -93.29824, 'US', 'MO', '077', 166810, 'America/Chicago'),
    (4951788, 'Springfield', 'Springfield', 42.10148, -72.58981, 'US', 'MA', '013', 155929, 'America/New_York'),
    (2641598, 'Newport', 'Newport', 50.70000, -1.29000, 'GB', 'ENG', 'J9', 25496, 'Europe/London'),
    (2641599, 'Newport', 'Newport', 52.76667, -2.38333, 'GB', 'ENG', 'L6', 11387, 'Europe/London'),
    (2641589, 'Newport', 'Newport', 51.58774, -2.99835, 'GB', 'WLS', 'Z1', 128060, 'Europe/London'),
    (5105496, 'Trenton', 'Trenton', 40.21705, -74.74294, 'US', 'NJ', '021', 83203, 'America/New_York'),
    (2657896, 'Zürich', 'Zurich', 47.36667, 8.55, 'CH', 'ZH', '112', 341730, 'Europe/Zurich'),
]
ADMIN1 = ['AU.08\tWestern Australia\tWestern Australia\t2058645', 'GB.SCT\tScotland\tScotland\t2638360',
          'GB.ENG\tEngland\tEngland\t6269131', 'GB.WLS\tWales\tWales\t2634895',
          'US.IL\tIllinois\tIllinois\t4896861', 'US.MO\tMissouri\tMissouri\t4398678',
          'US.MA\tMassachusetts\tMassachusetts\t6254926', 'US.NJ\tNew Jersey\tNew Jersey\t5101760', 'CH.ZH\tZurich\tZurich\t2657895']
ADMIN2 = ['GB.SCT.P8\tPerth and Kinross\tPerth and Kinross\t2640354',
          'GB.ENG.J9\tIsle of Wight\tIsle of Wight\t2646007', 'GB.ENG.L6\tShropshire\tShropshire\t2637934',
          'GB.WLS.Z1\tNewport\tNewport\t3333241', 'US.IL.167\tSangamon County\tSangamon County\t4908052',
          'US.MO.077\tGreene County\tGreene County\t4395147', 'US.MA.013\tHampden County\tHampden County\t4938851', 'US.NJ.021\tMercer County\tMercer County\t5100280',
          'CH.ZH.112\tBezirk Zürich\tBezirk Zurich\t6458798']
COUNTRIES = ['# ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital',
             'AU\tAUS\t036\tAS\tAustralia\tCanberra', 'GB\tGBR\t826\tUK\tUnited Kingdom\tLondon',
             'US\tUSA\t840\tUS\tUnited States\tWashington', 'CH\tCHE\t756\tSZ\tSwitzerland\tBern']


def _city_line(c):
    gid, name, ascii_name, lat, lon, cc, a1, a2, pop, tz = c
    cols = [str(gid), name, ascii_name, '', str(lat), str(lon), 'P', 'PPL', cc, '', a1, a2, '', '',
            str(pop), '', '0', tz, '2024-01-01']
    return '\t'.join(cols)


def _import(folder, with_regions=True):
    import routes
    from cities_import import import_cities_if_pending
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))
    with open(os.path.join(folder, 'cities5000.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(_city_line(c) for c in CITIES) + '\n')
    if with_regions:
        for name, lines in (('admin1CodesASCII.txt', ADMIN1), ('admin2Codes.txt', ADMIN2),
                            ('countryInfo.txt', COUNTRIES)):
            with open(os.path.join(folder, name), 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(lines) + '\n')
    import_cities_if_pending(routes.db_manager, folder, logging.getLogger('test'))
    return routes


@pytest.fixture()
def places(app, tmp_path):
    routes = _import(str(tmp_path / 'cities'))
    yield routes
    routes.db_manager.clear_cities()


def _key(client):
    C = {'X-CSRF-Token': signed_in(client)}
    made = post(client, '/api/v1/admin/sites', {'domain': f'places-{os.urandom(3).hex()}.example.com'}, **C)
    return made.get_json()['api_key']


def test_suggestions_are_short_and_name_the_state_and_country(client, places):
    found = client.get('/api/v1/autocomplete?q=Springf', headers={'X-API-Key': _key(client)}).get_json()
    texts = [p['description'] for p in found['predictions']]
    assert texts == ['Springfield MO, USA', 'Springfield MA, USA', 'Springfield IL, USA']
    first = found['predictions'][0]
    assert first['region'] == 'Missouri' and first['country'] == 'United States' and first['country_code'] == 'US'
    assert first['district'] == 'Greene County' and first['name'] == 'Springfield'


def test_places_that_would_read_the_same_get_their_district(places):
    texts = [p['description'] for p in places.geocoding_service.autocomplete('Newport')['predictions']]
    assert texts == ['Newport, Wales, UK', 'Newport, Isle of Wight, UK', 'Newport, Shropshire, UK']


@pytest.mark.parametrize('query, text', [
    ('Perth', ['Perth WA, AU', 'Perth, Scotland, UK']),
    ('Trenton', ['Trenton NJ, USA']),
    ('Zur', ['Zürich, CH']),            # not "Zürich, Zurich, CH"
])
def test_short_names(places, query, text):
    assert [p['description'] for p in places.geocoding_service.autocomplete(query)['predictions']] == text


def test_every_suggestion_resolves_to_that_same_place(places):
    svc = places.geocoding_service.cities_svc
    for q in ('Perth', 'Springfield', 'Newport', 'Zur', 'Trenton'):
        for p in svc.search(q):
            place, error = svc.resolve(p['description'])
            assert error is None and place['geoname_id'] == p['geoname_id'], p['description']
            assert place['formatted_name'] == p['description']


@pytest.mark.parametrize('query, geoname_id', [
    ('Perth', 2063523),                                   # the most populous, with nothing else said
    ('Perth, Scotland', 2640358),                         # before 1.0.2, this was Perth in Australia
    ('Perth, UK', 2640358),
    ('Perth, Western Australia, Australia', 2063523),
    ('Perth, AU', 2063523),                               # as places were written before 1.0.2
    ('Perth WA, AU', 2063523),
    ('Perth WA', 2063523),
    ('Perth, WA', 2063523),
    ('Perth, Scotland, UK', 2640358),
    ('Trenton NJ, USA', 5105496),
    ('Trenton, New Jersey', 5105496),
    ('Springfield, Sangamon County IL, USA', 4250542),
    ('Springfield, Greene County', 4409896),
    ('Springfield, IL', 4250542),
    ('Springfield, Ill', 4250542),
    ('Springfield, Massachusetts', 4951788),
    ('Newport, Shropshire, England, United Kingdom', 2641599),
    ('newport, isle of wight', 2641598),
    ('Zürich, Switzerland', 2657896),
    ('zurich', 2657896),
])
def test_a_place_is_found_by_its_region_and_country(places, query, geoname_id):
    place, error = places.geocoding_service.cities_svc.resolve(query)
    assert error is None and place['geoname_id'] == geoname_id


def test_a_place_that_doesnt_match_its_region_isnt_swapped_for_another(places):
    place, error = places.geocoding_service.cities_svc.resolve('Springfield, Texas')
    assert place is None and 'matches' in error


def test_a_chart_is_made_for_the_place_chosen(client, places):
    key = _key(client)
    r = client.post('/api/v1/calculate', headers={'X-API-Key': key, 'Content-Type': 'application/json'},
                    json={'chart_name': 'Test', 'datetime': '1990-05-01 12:00:00',
                          'location': 'Perth, Scotland, UK'})
    assert r.status_code in (200, 201), r.get_json()
    body = r.get_json()
    text = str(body)
    assert 'Perth, Scotland, UK' in text and 'Europe/London' in text


def test_a_new_import_retires_old_lookups(places, tmp_path):
    db = places.db_manager
    # As 1.0.1 would have cached it: "Perth, Scotland" resolved to Perth in Australia.
    db.save_location_to_cache('Perth, Scotland', {
        'latitude': -31.95, 'longitude': 115.86, 'formatted_address': 'Perth, AU',
        'timezone': 'Australia/Perth', 'utc_offset_seconds': 28800, 'dst_offset_seconds': 0})
    assert db.get_location_from_cache('Perth, Scotland')['timezone'] == 'Australia/Perth'
    _import(str(tmp_path / 'again'))
    assert db.get_location_from_cache('Perth, Scotland') is None
    found, error = places.geocoding_service.geocode_location('Perth, Scotland')
    assert error is None and found['timezone'] == 'Europe/London'


def test_without_the_region_files_places_read_as_before(app, tmp_path):
    routes = _import(str(tmp_path / 'plain'), with_regions=False)
    try:
        svc = routes.geocoding_service.cities_svc
        assert [p['description'] for p in svc.search('Perth')] == ['Perth, AU', 'Perth, UK']
        assert svc.resolve('Perth, GB')[0]['geoname_id'] == 2640358
        assert svc.resolve('Perth, Scotland')[0]['geoname_id'] == 2063523  # can't tell, as before
        assert routes.db_manager.get_cache_stats()['cities_regions'] is False
    finally:
        routes.db_manager.clear_cities()


def test_the_region_files_are_kept_for_the_next_import(places, tmp_path):
    folder = tmp_path / 'cities'
    assert sorted(os.listdir(folder)) == ['admin1CodesASCII.txt', 'admin2Codes.txt', 'countryInfo.txt']
    assert places.db_manager.get_cache_stats()['cities_regions'] is True
