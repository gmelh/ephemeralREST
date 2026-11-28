"""MySQL TLS settings: MYSQL_SSL_CA and MYSQL_SSL_MODE to connector options.
These need no MySQL server. The full test run on MySQL (see conftest.py)
checks a real encrypted, verified connection."""

import pytest

from database import MySQLConnectError, mysql_ssl_args, mysql_ssl_mode


@pytest.fixture
def ca(tmp_path):
    path = tmp_path / 'ca.pem'
    path.write_text('-----BEGIN CERTIFICATE-----\n')
    return str(path)


def test_default_without_a_ca_is_preferred():
    assert mysql_ssl_mode({}) == 'PREFERRED'
    assert mysql_ssl_args({}) == {}


def test_default_with_a_ca_verifies_it(ca):
    assert mysql_ssl_mode({'ssl_ca': ca}) == 'VERIFY_CA'
    assert mysql_ssl_args({'ssl_ca': ca}) == {
        'ssl_ca': ca, 'ssl_verify_cert': True, 'ssl_verify_identity': False}


def test_verify_identity_checks_the_host_name(ca):
    args = mysql_ssl_args({'ssl_ca': ca, 'ssl_mode': 'verify-identity'})
    assert args['ssl_verify_identity'] is True


def test_disabled_turns_tls_off():
    assert mysql_ssl_args({'ssl_mode': 'DISABLED'}) == {'ssl_disabled': True}


def test_required_needs_no_ca():
    assert mysql_ssl_mode({'ssl_mode': 'required'}) == 'REQUIRED'
    assert mysql_ssl_args({'ssl_mode': 'REQUIRED'}) == {}


@pytest.mark.parametrize('mode', ['VERIFY_CA', 'VERIFY_IDENTITY'])
def test_verify_without_a_ca_is_refused(mode):
    with pytest.raises(MySQLConnectError, match='needs MYSQL_SSL_CA'):
        mysql_ssl_mode({'ssl_mode': mode})


def test_a_missing_ca_file_is_refused(tmp_path):
    with pytest.raises(MySQLConnectError, match="no file there"):
        mysql_ssl_mode({'ssl_ca': str(tmp_path / 'nope.pem')})


def test_an_unknown_mode_is_refused():
    with pytest.raises(MySQLConnectError, match="isn't known"):
        mysql_ssl_mode({'ssl_mode': 'ALWAYS'})
