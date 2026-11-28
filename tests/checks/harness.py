"""A throwaway ephemeralREST (SQLite, one test city, offline places) for checks."""
import os, sys, tempfile
REPO = os.path.abspath(sys.argv[1])
work = tempfile.mkdtemp()
env = dict(SECRET_KEY='a' * 64, USE_GOOGLE='false', DB_TYPE='sqlite', DATABASE_PATH=os.path.join(work, 'e.db'),
           SWISS_EPHEMERIS_PATH=work, PORTAL_URL='https://portal.example.org', REGISTRATION_ENABLED='true')
os.environ.update(env)
os.chdir(work)
open('.env', 'w').write(''.join(f'{k}={v}\n' for k, v in env.items()))
sys.path.insert(0, REPO)
import logging; logging.disable(logging.WARNING)
import app as app_module
from key_crypto import KeyCrypto
import routes
crypto = KeyCrypto(os.environ['SECRET_KEY'])
db = routes.db_manager
def make_key(name, ident, key_type='user'):
    k = KeyCrypto.generate_key()
    kid = db.create_api_key(name, ident, crypto.encrypt(k), crypto.prefix(k), key_type=key_type)
    return k, kid
key, key_id = make_key('Test', 'test@example.com')
client = app_module.app.test_client()
H = {'X-API-Key': key}
OFF = {'output': {'bodies': {'ceres': False, 'pallas': False, 'juno': False, 'vesta': False, 'chiron': False}}}
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO cities (geoname_id, name, ascii_name, country_code, admin1_code, latitude, longitude, timezone_id, population) VALUES (2063523, 'Perth', 'Perth', 'AU', '08', -31.95224, 115.8614, 'Australia/Perth', 1896548)")
