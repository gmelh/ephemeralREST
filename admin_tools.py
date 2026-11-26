################################################################################
#                                                                              #
#  ephemeralREST — Swiss Ephemeris REST API                                    #
#  Copyright (C) 2026  ephemeralREST contributors                              #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
################################################################################
"""
Administrator tools for the web app (formerly pages of ephemeralADMIN):

  GET  /admin/settings                   the server's configuration, read-only
  GET  /admin/federation-test/services   federated services that can be tested
  POST /admin/federation-test/send       send a request to one, with your own key

Federation test configs are JSON files in FEDERATION_CONFIG_DIR, one per
service (the same files ephemeralADMIN read from /data/federation):

  {"service": "marketrest", "display_name": "marketREST", "description": "...",
   "base_url": "https://market.example.com",
   "endpoints": [{"method": "GET", "path": "/quote/{symbol}", "group": "Quotes",
                  "description": "...", "body": null, "timeout_seconds": 15,
                  "params": [{"name": "symbol", "in": "path", "required": true,
                              "placeholder": "AAPL"}]}]}

Services registered on the Services page are listed too (with no endpoint
list) when they have a base URL.
"""

import glob
import json
import logging
import os
import time

import requests
from flask import Blueprint, g, jsonify, request

from config import Config
from version import __version__

logger = logging.getLogger(__name__)

admin_tools = Blueprint('admin_tools', __name__)
_db = None


def init_admin_tools(app, db_manager, auth_manager) -> None:
    global _db
    _db = db_manager
    app.register_blueprint(admin_tools)
    for endpoint, view in list(app.view_functions.items()):
        if endpoint.startswith('admin_tools.'):
            app.view_functions[endpoint] = auth_manager.require_api_key(view)


def _require_admin():
    user = getattr(g, 'user', None) or {}
    if not user.get('admin'):
        return jsonify({'error': 'Admin access required', 'status': 403}), 403
    return None


def federation_config_dir() -> str:
    if os.environ.get('FEDERATION_CONFIG_DIR'):
        return os.environ['FEDERATION_CONFIG_DIR']
    data = os.environ.get('EPHEMERAL_DATA_DIR')
    return os.path.join(data, 'federation') if data else './federation-config'


# ---------------------------------------------------------------------------

@admin_tools.route('/admin/settings', methods=['GET'])
def admin_settings():
    """What this server is running with. Changed in .env, not here."""
    refused = _require_admin()
    if refused:
        return refused

    ephemeris = sorted(os.path.basename(p) for p in glob.glob(os.path.join(Config.SWISS_EPHEMERIS_PATH, '*.se1')))
    try:
        cities = _db.get_cache_stats().get('cities_loaded')
    except Exception:
        cities = None
    smtp = _db.get_smtp_config() or {}

    if Config.DB_TYPE == 'mysql':
        database = f"MySQL — {Config.MYSQL_DATABASE} on {Config.MYSQL_HOST}:{Config.MYSQL_PORT}"
    else:
        database = f"SQLite — {Config.DATABASE_PATH}"

    return jsonify({
        'public_url':            Config.PUBLIC_URL,
        'site_name':             Config.SITE_NAME,
        'version':               __version__,
        'database':              database,
        'geocoding':             'google' if Config.USE_GOOGLE else 'offline',
        'ephemeris_files':       ephemeris,
        'cities_imported':       cities,
        'email_configured':      bool(smtp.get('host') or os.environ.get('SMTP_HOST')),
        'session_timeout':       Config.SESSION_TIMEOUT,
        'session_max_hours':     Config.SESSION_MAX_HOURS,
        'trusted_device_days':   Config.TRUSTED_DEVICE_DAYS,
        'allow_admin_promotion': Config.ALLOW_ADMIN_PROMOTION,
        'allow_registration':    Config.ALLOW_REGISTRATION,
        'rate_limiting':         Config.RATE_LIMIT_ENABLED,
        'cors_origins':          Config.CORS_ORIGINS,
        'store_enabled':         Config.STORE_ENABLED,
        'federation_config_dir': federation_config_dir(),
    })


# ---------------------------------------------------------------------------
# Federation test

def _load_configs() -> dict:
    """slug → config, from the JSON files. A broken file is skipped, not fatal."""
    configs = {}
    for path in sorted(glob.glob(os.path.join(federation_config_dir(), '*.json'))):
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            logger.warning(f"Federation test config {path} skipped: {e}")
            continue
        if not isinstance(data, dict) or not data.get('base_url'):
            continue
        slug = str(data.get('service') or os.path.basename(path)[:-5])
        configs[slug] = data
    return configs


def _services() -> dict:
    services = {}
    for slug, cfg in _load_configs().items():
        services[slug] = {
            'slug':         slug,
            'display_name': cfg.get('display_name') or slug,
            'description':  cfg.get('description') or '',
            'base_url':     cfg['base_url'],
            'endpoints':    cfg.get('endpoints') or [],
            'source':       'config',
        }
    for svc in _db.get_federated_services(active_only=False):
        if svc['slug'] in services or not svc.get('base_url'):
            continue
        services[svc['slug']] = {
            'slug':         svc['slug'],
            'display_name': svc.get('display_name') or svc['slug'],
            'description':  svc.get('description') or '',
            'base_url':     svc['base_url'],
            'endpoints':    [],
            'source':       'registry',
        }
    return services


@admin_tools.route('/admin/federation-test/services', methods=['GET'])
def federation_services():
    refused = _require_admin()
    if refused:
        return refused
    services = sorted(_services().values(), key=lambda s: s['display_name'].lower())
    return jsonify({'services': services, 'config_dir': federation_config_dir()})


@admin_tools.route('/admin/federation-test/send', methods=['POST'])
def federation_send():
    """
    Send one request to a federated service with the caller's own API key —
    the realistic test, since grants travel with the key.

    Body: {"service": slug, "method": "GET", "path": "/x/1",
           "query": {"a": "b"}, "body": <any JSON> | null}
    """
    refused = _require_admin()
    if refused:
        return refused

    data    = request.get_json(silent=True) or {}
    service = _services().get(str(data.get('service', '')))
    if not service:
        return jsonify({'error': 'Unknown service', 'status': 404}), 404

    method = str(data.get('method') or 'GET').upper()
    if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
        return jsonify({'error': 'Unsupported method', 'status': 400}), 400
    path = str(data.get('path') or '/')
    if not path.startswith('/') or '://' in path:
        return jsonify({'error': 'The path must start with /', 'status': 400}), 400
    query = {str(k): str(v) for k, v in (data.get('query') or {}).items() if v not in (None, '')}

    from key_crypto import KeyCrypto
    record = _db.get_api_key_by_id(int(g.user['id']))
    api_key = KeyCrypto(Config.SECRET_KEY).decrypt(record['key_enc'])

    headers = {'Accept': 'application/json', 'X-API-Key': api_key}
    kwargs = {}
    if data.get('body') is not None:
        kwargs['json'] = data['body']

    timeout = 15
    for ep in service['endpoints']:
        if ep.get('path') and ep.get('timeout_seconds') and ep.get('method', '').upper() == method:
            timeout = int(ep['timeout_seconds'])
            break

    url = service['base_url'].rstrip('/') + path
    started = time.monotonic()
    try:
        response = requests.request(method, url, params=query, headers=headers, timeout=timeout,
                                    allow_redirects=False, **kwargs)
    except requests.RequestException as e:
        return jsonify({'ok': False, 'status': 0, 'time_ms': round((time.monotonic() - started) * 1000),
                        'url': url, 'headers': {}, 'body': '', 'error': f'Connection failed: {e}'})
    return jsonify({
        'ok':      200 <= response.status_code < 300,
        'status':  response.status_code,
        'time_ms': round((time.monotonic() - started) * 1000),
        'url':     response.url,
        'headers': dict(response.headers),
        'body':    response.text[:500_000],
        'error':   None,
    })
