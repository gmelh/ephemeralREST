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
Sites: the apps (such as hosted AstrologyStudio sites) that use ephemeralREST through a domain
key, and the administration around them. Administrators only.

  GET    /admin/sites                    every site, with its accounts and email delivery
  POST   /admin/sites                    a new site: its domain key (shown once), its address
                                         registered for sign-up links, and the defaults below
  GET    /admin/sites/<id>               one site, with its addresses and accounts
  PUT    /admin/sites/<id>               name, sign-up addresses, email delivery, limits, suspended
  POST   /admin/sites/<id>/rotate        a new key for the site (shown once); the old one stops
  DELETE /admin/sites/<id>               remove the site; its accounts belong to no site
  GET    /admin/site-defaults            what a new site starts with
  PUT    /admin/site-defaults
  GET    /admin/audit                    what administrators changed (everything under /admin)

Also here:

- The admin address. With ADMIN_URL (or ADMIN_DOMAIN) set, everything under /admin answers only
  at that address: anywhere else it's 404, as if it weren't there.
- The audit log: every change made under /admin (not reads) is recorded with who made it, from
  where, and what it was about. Passwords, keys and tokens are never recorded.
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from config import Config

logger = logging.getLogger(__name__)

sites = Blueprint('sites', __name__)
_db = None

DEFAULTS_SETTING = 'site_defaults'
BUILT_IN_DEFAULTS = {
    'email_delivery_allowed': False,
    'rate_per_minute': None,
    'rate_per_hour': None,
    'rate_per_day': None,
}
DOMAIN = re.compile(r'^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
SECRET_WORDS = ('password', 'key', 'secret', 'token', 'code')
# Admin requests that change nothing, so aren't recorded.
NOT_CHANGES = {'admin_tools.federation_test_send', 'api.admin_test_smtp'}


def init_sites(app, db_manager, auth_manager) -> None:
    global _db
    _db = db_manager
    app.register_blueprint(sites)
    for endpoint, view in list(app.view_functions.items()):
        if endpoint.startswith('sites.'):
            app.view_functions[endpoint] = auth_manager.require_api_key(view)
    app.before_request(_admin_address_only)
    app.after_request(_record_admin_change)


# ---------------------------------------------------------------------------
# The admin address
# ---------------------------------------------------------------------------

def admin_host() -> Optional[str]:
    """The host administration answers at, or None when it answers anywhere."""
    url = Config.ADMIN_URL
    if not url:
        return None
    return (urlparse(url if '//' in url else f'https://{url}').hostname or '').lower() or None


def is_admin_endpoint(endpoint: Optional[str]) -> bool:
    if not endpoint:
        return False
    return (endpoint.startswith('api.admin_') or endpoint.startswith('admin_tools.')
            or endpoint.startswith('sites.'))


def here_is_admin() -> bool:
    """Whether administration answers at the address of this request."""
    host = admin_host()
    return host is None or (request.host.split(':')[0].lower() == host)


def _admin_address_only():
    if is_admin_endpoint(request.endpoint) and not here_is_admin():
        return jsonify({'error': 'Not found', 'status': 404}), 404
    return None


# ---------------------------------------------------------------------------
# The audit log
# ---------------------------------------------------------------------------

def _scrub(value):
    """A request body with anything secret taken out, for the audit log."""
    if isinstance(value, dict):
        return {k: ('…' if any(w in k.lower() for w in SECRET_WORDS) and not k.lower().endswith(('_id', '_ids'))
                    and not isinstance(v, (bool, type(None))) else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value[:20]]
    return value


def _target(view_args: dict):
    for name, kind in (('site_id', 'site'), ('key_id', 'key'), ('service_id', 'service'), ('name', 'template'),
                       ('service', 'service')):
        if name in view_args:
            raw = view_args[name]
            return kind, (int(raw) if isinstance(raw, int) or str(raw).isdigit() else None), (
                '' if isinstance(raw, int) or str(raw).isdigit() else str(raw))
    return '', None, ''


def record(action: str, target_type: str = '', target_id: Optional[int] = None, detail: str = '') -> None:
    user = getattr(g, 'user', None) or {}
    from auth import get_client_ip
    try:
        _db.record_admin_action(user.get('id'), user.get('identifier', ''), get_client_ip(), action,
                                target_type, target_id, detail)
    except Exception:  # the log never stops the change itself
        logger.exception('Could not record an admin action')


def _record_admin_change(response):
    endpoint = request.endpoint
    if (not is_admin_endpoint(endpoint) or request.method in ('GET', 'HEAD', 'OPTIONS')
            or endpoint in NOT_CHANGES or response.status_code >= 400 or getattr(g, 'audit_done', False)):
        return response
    user = getattr(g, 'user', None) or {}
    if not user.get('admin'):
        return response
    kind, target_id, name = _target(request.view_args or {})
    if kind == 'key' and target_id is not None:
        changed = _db.get_api_key_by_id(target_id)
        if changed and changed.get('key_type') == 'domain':
            kind = 'site'  # shown on the site's page too
    body = request.get_json(silent=True)
    detail = name
    if isinstance(body, (dict, list)) and body:
        detail = (name + ' ' if name else '') + json.dumps(_scrub(body), separators=(',', ':'), default=str)
    action = endpoint.split('.', 1)[1].removeprefix('admin_')
    record(action, kind, target_id, detail)
    return response


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

def _require_admin():
    user = getattr(g, 'user', None) or {}
    if not user.get('admin'):
        return jsonify({'error': 'Admin access required', 'status': 403}), 403
    return None


def _error(message: str, status: int = 400, **extra):
    return jsonify({'error': message, 'status': status, **extra}), status


def site_defaults() -> dict:
    stored = _db.get_admin_setting(DEFAULTS_SETTING, {}) or {}
    return {**BUILT_IN_DEFAULTS, **{k: v for k, v in stored.items() if k in BUILT_IN_DEFAULTS}}


def _site(record: dict, homes: Optional[dict] = None) -> dict:
    homes = _db.count_accounts_by_home() if homes is None else homes
    delivery = _db.get_email_delivery(record['id'])
    return {
        'id': record['id'],
        'name': record['name'],
        'domain': record['identifier'],
        'key_prefix': record['key_prefix'],
        'active': bool(record['active']),
        'created_at': record.get('created_at'),
        'origins': _db.get_link_origins(record['id']),
        'email_delivery': delivery,
        'accounts': homes.get(record['id'], 0),
        'rate_per_minute': record.get('rate_per_minute'),
        'rate_per_hour': record.get('rate_per_hour'),
        'rate_per_day': record.get('rate_per_day'),
    }


def _site_or_404(site_id: int):
    record = _db.get_api_key_by_id(site_id)
    if not record or record.get('key_type') != 'domain':
        return None, _error('Site not found', 404)
    return record, None


def _clean_limit(value, name: str):
    if value is None or value == '':
        return None, None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10_000_000:
        return None, _error(f'{name} must be a whole number, 0 or more (or null for the default)', 400)
    return value, None


def _origins(record: dict, origins):
    if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins) or len(origins) > 20:
        return None, _error('origins must be a list of addresses', 400)
    from routes import _save_link_origins
    saved, error = _save_link_origins(record, origins)
    return saved, error


@sites.route('/admin/sites', methods=['GET'])
def list_sites():
    refused = _require_admin()
    if refused:
        return refused
    homes = _db.count_accounts_by_home()
    keys = [k for k in _db.get_all_api_keys(include_inactive=True) if k.get('key_type') == 'domain']
    return jsonify({'sites': [_site(k, homes) for k in sorted(keys, key=lambda k: k['identifier'])],
                    'defaults': site_defaults()})


@sites.route('/admin/sites', methods=['POST'])
def create_site():
    """A new site. Body: { "domain": "astro.example.com", "name": "…", "origins": ["https://…"] }.
    The key is in the answer, once: give it to the site's owner."""
    refused = _require_admin()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    domain = str(body.get('domain') or '').strip().lower().removeprefix('https://').removeprefix('http://').split('/')[0]
    if not DOMAIN.match(domain):
        return _error('Give the site\'s domain, like astro.example.com', 400)
    if _db.get_api_key_by_identifier(domain):
        return _error(f'There\'s already a key for {domain}', 409)
    name = str(body.get('name') or domain).strip()[:100]
    from key_crypto import KeyCrypto
    crypto = KeyCrypto(Config.SECRET_KEY)
    key = KeyCrypto.generate_key()
    defaults = site_defaults()
    site_id = _db.create_api_key(
        name, domain, crypto.encrypt(key), crypto.prefix(key), key_type='domain',
        rate_per_minute=defaults['rate_per_minute'], rate_per_hour=defaults['rate_per_hour'],
        rate_per_day=defaults['rate_per_day'],
    )
    # A domain key has no password or account of its own to finish setting up.
    _db.update_api_key(site_id, must_change_password=0)
    created = _db.get_api_key_by_id(site_id)
    origins = body.get('origins')
    if origins is None:
        origins = [f'https://{domain}']
    _, error = _origins(created, origins)
    if error:
        _db.delete_api_key(site_id)
        return error
    if defaults['email_delivery_allowed']:
        _db.set_email_delivery(site_id, allowed=True)
    record('create_site', 'site', site_id, domain)
    g.audit_done = True
    logger.info(f"Admin created site {site_id} ({domain})")
    return jsonify({'site': _site(_db.get_api_key_by_id(site_id)), 'api_key': key}), 201


@sites.route('/admin/sites/<int:site_id>', methods=['GET'])
def get_site(site_id):
    refused = _require_admin()
    if refused:
        return refused
    record, error = _site_or_404(site_id)
    if error:
        return error
    found = _site(record)
    found['account_list'] = _db.list_home_accounts(site_id)
    found['activity'] = _db.list_admin_actions(limit=20, target_type='site', target_id=site_id)
    return jsonify(found)


@sites.route('/admin/sites/<int:site_id>', methods=['PUT'])
def update_site(site_id):
    """Change any of: name, origins, email_delivery_allowed, rate_per_minute/hour/day, active."""
    refused = _require_admin()
    if refused:
        return refused
    record, error = _site_or_404(site_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    fields = {}
    if 'name' in body:
        name = str(body['name'] or '').strip()
        if not name:
            return _error('The name can\'t be empty', 400)
        fields['name'] = name[:100]
    for limit in ('rate_per_minute', 'rate_per_hour', 'rate_per_day'):
        if limit in body:
            fields[limit], error = _clean_limit(body[limit], limit)
            if error:
                return error
    if 'active' in body:
        fields['active'] = 1 if body['active'] else 0
    if 'origins' in body:
        _, error = _origins(record, body['origins'])
        if error:
            return error
    if fields:
        _db.update_api_key(site_id, **fields)
    if 'email_delivery_allowed' in body:
        _db.set_email_delivery(site_id, allowed=bool(body['email_delivery_allowed']))
    return jsonify(_site(_db.get_api_key_by_id(site_id)))


@sites.route('/admin/sites/<int:site_id>/rotate', methods=['POST'])
def rotate_site(site_id):
    """A new key for the site, shown once; the old one stops working at once."""
    refused = _require_admin()
    if refused:
        return refused
    record, error = _site_or_404(site_id)
    if error:
        return error
    from key_crypto import KeyCrypto
    crypto = KeyCrypto(Config.SECRET_KEY)
    key = KeyCrypto.generate_key()
    _db.update_api_key(site_id, key_enc=crypto.encrypt(key), key_prefix=crypto.prefix(key))
    return jsonify({'site': _site(_db.get_api_key_by_id(site_id)), 'api_key': key})


@sites.route('/admin/sites/<int:site_id>', methods=['DELETE'])
def delete_site(site_id):
    refused = _require_admin()
    if refused:
        return refused
    record, error = _site_or_404(site_id)
    if error:
        return error
    released = _db.clear_home(site_id)
    _db.forget_site(site_id)
    _db.delete_api_key(site_id)
    return jsonify({'deleted': site_id, 'accounts_released': released})


@sites.route('/admin/site-defaults', methods=['GET'])
def get_defaults():
    refused = _require_admin()
    if refused:
        return refused
    return jsonify(site_defaults())


@sites.route('/admin/site-defaults', methods=['PUT'])
def set_defaults():
    refused = _require_admin()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    current = site_defaults()
    if 'email_delivery_allowed' in body:
        current['email_delivery_allowed'] = bool(body['email_delivery_allowed'])
    for limit in ('rate_per_minute', 'rate_per_hour', 'rate_per_day'):
        if limit in body:
            current[limit], error = _clean_limit(body[limit], limit)
            if error:
                return error
    _db.set_admin_setting(DEFAULTS_SETTING, current)
    return jsonify(current)


@sites.route('/admin/audit', methods=['GET'])
def audit():
    """Newest first, 50 at a time: `next` is the `before` for the next page."""
    refused = _require_admin()
    if refused:
        return refused
    before = request.args.get('before', type=int)
    rows = _db.list_admin_actions(before=before, limit=51)
    more = len(rows) > 50
    rows = rows[:50]
    return jsonify({'data': rows, 'next': rows[-1]['id'] if more and rows else None})
