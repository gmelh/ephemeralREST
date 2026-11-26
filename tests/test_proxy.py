"""Forwarded client addresses are believed only from a trusted proxy (TRUST_PROXY, TRUSTED_PROXIES)."""
from flask import Flask, request
from werkzeug.test import Client

import app as app_module
from auth import get_client_ip


def _echo():
    flask_app = Flask('echo')

    @flask_app.route('/')
    def who():
        return f'{get_client_ip()} {request.scheme}'
    return flask_app


def _ask(wsgi, peer, forwarded='203.0.113.9'):
    client = Client(wsgi)
    return client.get('/', environ_base={'REMOTE_ADDR': peer},
                      headers={'X-Forwarded-For': forwarded, 'X-Forwarded-Proto': 'https'}).get_data(as_text=True)


def test_from_the_shared_caddy_the_forwarded_address_is_used():
    flask_app = _echo()
    wsgi = app_module._proxied(flask_app.wsgi_app, ['172.31.250.2'])
    assert _ask(wsgi, '172.31.250.2') == '203.0.113.9 https'


def test_another_container_cant_claim_an_address():
    flask_app = _echo()
    wsgi = app_module._proxied(flask_app.wsgi_app, ['172.31.250.2'])
    assert _ask(wsgi, '172.31.250.7') == '172.31.250.7 http'


def test_without_a_trusted_proxy_the_header_is_ignored():
    """Before, any caller could pick its own address with X-Forwarded-For."""
    flask_app = _echo()
    assert _ask(flask_app.wsgi_app, '198.51.100.4') == '198.51.100.4 http'


def test_trust_proxy_without_a_list_trusts_one_hop_from_anywhere():
    flask_app = _echo()
    wsgi = app_module._proxied(flask_app.wsgi_app, [])
    assert _ask(wsgi, '10.0.0.5') == '203.0.113.9 https'
