"""Test setup: a throwaway database and settings, before the app is imported.

SQLite by default. To run the same tests on MySQL (as on a managed MySQL service), give a server
and an account that may create databases; a new database is made for the run and dropped after:

    TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3306 TEST_MYSQL_USER=root TEST_MYSQL_PASSWORD=… \
    TEST_MYSQL_SSL_CA=/path/to/ca.pem python -m pytest -q tests
"""
import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix='ephemeral-test-')
os.environ.update({
    'SECRET_KEY':           'test-secret-key-0123456789abcdef0123456789abcdef',
    'USE_GOOGLE':           'false',
    'DB_TYPE':              'sqlite',
    'DATABASE_PATH':        os.path.join(_tmp, 'test.db'),
    'LOG_FILE':             os.path.join(_tmp, 'test.log'),
    'USAGE_COUNT_FILE':     os.path.join(_tmp, 'usage.json'),
    'SWISS_EPHEMERIS_PATH': os.path.join(_tmp, 'sweph'),
    'CITIES_FOLDER':        os.path.join(_tmp, 'cities'),
    'EPHEMERAL_NO_ENV_FILE': '1',
    'RATE_LIMIT_ENABLED':   'false',
})
os.makedirs(os.environ['CITIES_FOLDER'], exist_ok=True)

MYSQL_TEST_DB = None
if os.environ.get('TEST_MYSQL_HOST'):
    MYSQL_TEST_DB = f"ephemeral_test_{os.getpid()}"
    os.environ.update({
        'DB_TYPE':        'mysql',
        'MYSQL_HOST':     os.environ['TEST_MYSQL_HOST'],
        'MYSQL_PORT':     os.environ.get('TEST_MYSQL_PORT', '3306'),
        'MYSQL_USER':     os.environ.get('TEST_MYSQL_USER', 'root'),
        'MYSQL_PASSWORD': os.environ.get('TEST_MYSQL_PASSWORD', ''),
        'MYSQL_DATABASE': MYSQL_TEST_DB,
    })
    if os.environ.get('TEST_MYSQL_SSL_CA'):
        os.environ['MYSQL_SSL_CA'] = os.environ['TEST_MYSQL_SSL_CA']
        os.environ['MYSQL_SSL_MODE'] = os.environ.get('TEST_MYSQL_SSL_MODE', 'VERIFY_CA')
    import mysql.connector
    _admin = mysql.connector.connect(
        host=os.environ['MYSQL_HOST'], port=int(os.environ['MYSQL_PORT']), user=os.environ['MYSQL_USER'],
        password=os.environ['MYSQL_PASSWORD'], **({'ssl_ca': os.environ['TEST_MYSQL_SSL_CA']}
                                                     if os.environ.get('TEST_MYSQL_SSL_CA') else {}))
    _admin.cursor().execute(f"CREATE DATABASE {MYSQL_TEST_DB} CHARACTER SET utf8mb4")
    _admin.close()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


@pytest.fixture(scope='session')
def app():
    return app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()


def pytest_sessionfinish(session, exitstatus):
    if MYSQL_TEST_DB:
        import mysql.connector
        conn = mysql.connector.connect(
            host=os.environ['MYSQL_HOST'], port=int(os.environ['MYSQL_PORT']), user=os.environ['MYSQL_USER'],
            password=os.environ['MYSQL_PASSWORD'], **({'ssl_ca': os.environ['TEST_MYSQL_SSL_CA']}
                                                         if os.environ.get('TEST_MYSQL_SSL_CA') else {}))
        conn.cursor().execute(f"DROP DATABASE IF EXISTS {MYSQL_TEST_DB}")
        conn.close()
