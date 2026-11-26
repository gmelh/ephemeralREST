"""Test setup: a throwaway SQLite database and settings, before the app is imported."""
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


@pytest.fixture(scope='session')
def app():
    return app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()
