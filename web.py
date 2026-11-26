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
The web app and browser sessions.

One address serves everything:

  /api/v1/...   the API. Every route also still answers at the root
                (/calculate, /me, ...) so existing clients keep working.
  /             the web app (web/dist), for page loads that aren't API routes.

Browsers never hold an API key. Logging in through /api/v1/auth/login sets a
signed, httpOnly session cookie that names the key by id; auth.py accepts it
wherever an X-API-Key header would do. Requests that change something must
also carry the session's CSRF token in X-CSRF-Token, and the cookie is ignored
on requests from another origin.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional, Tuple

from flask import Blueprint, Response, g, jsonify, make_response, redirect, request, send_from_directory
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import safe_join

from config import Config
from version import __version__

logger = logging.getLogger(__name__)

API_PREFIX     = '/api/v1'
SESSION_COOKIE = 'eph_session'
DEVICE_COOKIE  = 'eph_device'

WEB_DIR = os.environ.get(
    'EPHEMERAL_WEB_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', 'dist'),
)

# Old portal addresses (bookmarks, links in emails already sent) → the web app.
LEGACY_PAGES = {
    'verify.php':             '/verify',
    'set-password.php':       '/set-password',
    'forgot-password.php':    '/forgot-password',
    'login.php':              '/login',
    '2fa.php':                '/login',
    'logout.php':             '/login',
    'register-user.php':      '/register',
    'setup.php':              '/setup',
    'index.php':              '/',
    'landing.php':            '/',
    'portal-admin.php':       '/',
    'portal-user.php':        '/account',
    'key-output.php':         '/output',
    'keys.php':               '/keys',
    'key-detail.php':         '/keys',
    'class-limits.php':       '/rate-limits',
    'smtp.php':               '/email',
    'email-templates.php':    '/email/templates',
    'federated-services.php': '/services',
    'federation-test.php':    '/services/test',
    'portal-settings.php':    '/settings',
    'api-tester.php':         '/api-tester',
}

_db = None
web = Blueprint('web', __name__)


# ---------------------------------------------------------------------------
# /api/v1 prefix
# ---------------------------------------------------------------------------

class ApiPrefix:
    """WSGI middleware: /api/v1/x is routed as /x, and marked as an API call
    so it's never answered with the web app."""

    def __init__(self, wsgi_app, prefix: str = API_PREFIX):
        self.wsgi_app = wsgi_app
        self.prefix   = prefix

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == self.prefix or path.startswith(self.prefix + '/'):
            environ['PATH_INFO']   = path[len(self.prefix):] or '/'
            environ['SCRIPT_NAME'] = environ.get('SCRIPT_NAME', '') + self.prefix
            environ['ephemeral.api'] = True
        return self.wsgi_app(environ, start_response)


def is_api_request() -> bool:
    return bool(request.environ.get('ephemeral.api'))


# ---------------------------------------------------------------------------
# Serving the web app
# ---------------------------------------------------------------------------

def _serve_web_app() -> Optional[Response]:
    """before_request: answer page loads and static files that no API route handles."""
    if is_api_request() or request.method not in ('GET', 'HEAD'):
        return None

    name = request.path.lstrip('/')
    if name.endswith('.php') and name in LEGACY_PAGES:
        target = LEGACY_PAGES[name]
        query  = request.query_string.decode('utf-8', 'replace')
        return redirect(target + (f'?{query}' if query else ''), code=301)

    if request.routing_exception is None:
        return None  # an API route: let it answer

    if not os.path.isdir(WEB_DIR):
        return None

    if name and name != 'index.html':
        path = safe_join(WEB_DIR, name)
        if path and os.path.isfile(path):
            response = send_from_directory(WEB_DIR, name, max_age=0)
            return response

    # Any other page address belongs to the web app's own router — but only
    # for browsers asking for a page, so API clients still get JSON 404s.
    # (Browsers list text/html explicitly; fetch() and curl send */*.)
    if 'text/html' in (request.headers.get('Accept') or ''):
        response = send_from_directory(WEB_DIR, 'index.html', max_age=0)
        response.headers['Cache-Control'] = 'no-cache'
        return response
    return None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(Config.SECRET_KEY, salt='ephemeral-web-session')


def _password_tag(record: dict) -> str:
    """Changes whenever the password does, which ends every session."""
    return hashlib.sha256((record.get('password_hash') or '').encode()).hexdigest()[:16]


def _same_origin() -> bool:
    """False when the browser says the request comes from another site or origin."""
    origin = request.headers.get('Origin')
    if not origin or origin == 'null':
        return origin is None
    host = request.host
    return origin in (f'https://{host}', f'http://{host}')


def _secure() -> bool:
    return request.scheme == 'https'


def _issue(response: Response, record: dict, csrf: Optional[str] = None, started: Optional[int] = None) -> str:
    csrf = csrf or secrets.token_urlsafe(24)
    token = _serializer().dumps({
        'k': int(record['id']),
        'p': record['key_prefix'],
        'h': _password_tag(record),
        'c': csrf,
        's': started or int(time.time()),
    })
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=Config.SESSION_TIMEOUT, httponly=True, secure=_secure(), samesite='Strict', path='/',
    )
    return csrf


def _clear(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path='/', samesite='Strict', secure=_secure(), httponly=True)


def _read_session() -> Optional[Tuple[dict, dict]]:
    """The (key record, session payload) behind this request's cookie, if it's still good."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or _db is None or not _same_origin():
        return None
    try:
        payload = _serializer().loads(token, max_age=Config.SESSION_TIMEOUT)
    except (BadSignature, SignatureExpired):
        return None
    if time.time() - payload.get('s', 0) > Config.SESSION_MAX_HOURS * 3600:
        return None
    record = _db.get_api_key_by_id(int(payload.get('k', 0)))
    if (not record or not record.get('active') or record.get('key_prefix') != payload.get('p')
            or _password_tag(record) != payload.get('h') or record.get('must_change_password')):
        return None
    return record, payload


def session_user():
    """
    For auth.py: the user behind a valid session cookie.

    Returns (user, None) when signed in, (None, error_response) when the
    cookie is good but the request is missing its CSRF token, and
    (None, None) when there's no usable session.
    """
    found = _read_session()
    if not found:
        return None, None
    record, payload = found
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        sent = request.headers.get('X-CSRF-Token', '')
        if not hmac.compare_digest(sent, payload.get('c', '')):
            return None, (jsonify({
                'error':   'Forbidden',
                'status':  403,
                'message': 'Missing or wrong X-CSRF-Token for this session.',
            }), 403)
    from users import _build_user_dict
    g.web_session = payload
    return _build_user_dict(record, _db), None


def _refresh_session(response: Response) -> Response:
    """after_request: keep a used session alive (rolling timeout), follow a key
    rotation made in this request, and drop a session whose key was disabled."""
    payload = g.pop('web_session', None)
    if payload is None or getattr(g, 'web_session_ended', False):
        return response
    record = _db.get_api_key_by_id(int(payload['k'])) if _db else None
    if not record or not record.get('active') or _password_tag(record) != payload.get('h'):
        _clear(response)
    else:
        _issue(response, record, csrf=payload['c'], started=payload['s'])
    return response


# ---------------------------------------------------------------------------
# Trusted devices ("remember this device" skips 2FA)
# ---------------------------------------------------------------------------

def _devices() -> dict:
    raw = request.cookies.get(DEVICE_COOKIE)
    if not raw:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_devices(response: Response, devices: dict) -> None:
    if not devices:
        response.delete_cookie(DEVICE_COOKIE, path='/')
        return
    raw = base64.urlsafe_b64encode(json.dumps(devices).encode()).decode()
    response.set_cookie(
        DEVICE_COOKIE, raw, max_age=Config.TRUSTED_DEVICE_DAYS * 86400,
        httponly=True, secure=_secure(), samesite='Lax', path='/',
    )


# ---------------------------------------------------------------------------
# /auth — the web app's sign-in endpoints
# ---------------------------------------------------------------------------

def _public_config() -> dict:
    return {
        'site_name':             Config.SITE_NAME,
        'version':               __version__,
        'trusted_device_days':   Config.TRUSTED_DEVICE_DAYS,
        'allow_admin_promotion': Config.ALLOW_ADMIN_PROMOTION,
        'allow_registration':    Config.ALLOW_REGISTRATION,
        'public_url':            Config.PUBLIC_URL,
        # With an admin address, administration is only there: the web app shows it (or a
        # link to it) accordingly.
        'admin_url':             Config.ADMIN_URL,
        'admin_here':            _admin_here(),
    }


def _admin_here() -> bool:
    from sites import here_is_admin
    return here_is_admin()


def _session_body(record: dict, csrf: str) -> dict:
    from users import _build_user_dict
    user = _build_user_dict(record, _db)
    return {
        'user': {
            'id':          int(user['id']),
            'name':        user['name'],
            'identifier':  user['identifier'],
            'admin':       user['admin'],
            'active':      user['active'],
            'rate_limits': user['rate_limits'],
        },
        'csrf_token': csrf,
    }


def _signed_in(record_id: int, extra: Optional[dict] = None, status: int = 200) -> Response:
    record   = _db.get_api_key_by_id(int(record_id))
    response = make_response()
    csrf     = _issue(response, record)
    body     = {'session': _session_body(record, csrf)}
    body.update(extra or {})
    response.set_data(json.dumps(body))
    response.mimetype    = 'application/json'
    response.status_code = status
    return response


def _cross_site():
    return jsonify({'error': 'Forbidden', 'status': 403, 'message': 'Cross-site request refused.'}), 403


@web.route('/auth/status', methods=['GET'])
def auth_status():
    """Whether this install needs setting up, who's signed in, and the public settings."""
    found = _read_session()
    body = {
        'setup_required': _db.is_database_empty(),
        'session':        _session_body(found[0], found[1]['c']) if found else None,
        'config':         _public_config(),
    }
    return jsonify(body)


@web.route('/auth/login', methods=['POST'])
def auth_login():
    from routes import _do_login
    from validators import LoginSchema
    if not _same_origin():
        return _cross_site()
    data, error = _load(LoginSchema)
    if error:
        return error
    email = data['email'].strip().lower()
    payload, status = _do_login(email, data['password'], _devices().get(email))
    if status != 200 or 'api_key' not in payload:
        return jsonify(payload), status
    return _signed_in(payload['id'])


@web.route('/auth/login/2fa', methods=['POST'])
def auth_login_2fa():
    from routes import _do_login_2fa
    from validators import Login2FASchema
    if not _same_origin():
        return _cross_site()
    data, error = _load(Login2FASchema)
    if error:
        return error
    payload, status = _do_login_2fa(data['email'], data['code'], data.get('remember_device', False))
    if status != 200:
        return jsonify(payload), status
    response = _signed_in(payload['id'])
    if payload.get('device_token'):
        devices = _devices()
        devices[payload['identifier']] = payload['device_token']
        _save_devices(response, devices)
    return response


@web.route('/auth/setup', methods=['POST'])
def auth_setup():
    """First run: create the administrator and sign them in. The API key is
    returned this once, for the person to copy into their own apps."""
    from routes import _do_setup
    from validators import SetupSchema
    if not _same_origin():
        return _cross_site()
    data, error = _load(SetupSchema)
    if error:
        return error
    payload, status = _do_setup(data['name'], data['email'], data['password'])
    if status != 201:
        return jsonify(payload), status
    return _signed_in(payload['id'], {'api_key': payload['api_key']}, status=201)


@web.route('/auth/logout', methods=['POST'])
def auth_logout():
    """End the session. With {"forget_device": true}, this browser also needs 2FA next time."""
    data     = request.get_json(silent=True) or {}
    found    = _read_session()
    response = jsonify({'message': 'Logged out'})
    _clear(response)
    g.web_session_ended = True
    if found and data.get('forget_device'):
        record, _ = found
        devices = _devices()
        token = devices.pop(record['identifier'], None)
        if token:
            _db.delete_trusted_device(token)
        _save_devices(response, devices)
    return response


def _load(schema_class):
    from marshmallow import ValidationError
    data = request.get_json(silent=True)
    if data is None:
        return None, (jsonify({'error': 'Invalid JSON payload', 'status': 400}), 400)
    try:
        return schema_class().load(data), None
    except ValidationError as err:
        return None, (jsonify({'error': 'Validation failed', 'status': 400, 'details': err.messages}), 400)


# ---------------------------------------------------------------------------

def init_web(app, db_manager) -> None:
    """Wire everything above into the app. Call before the other before_request hooks."""
    global _db
    _db = db_manager
    app.wsgi_app = ApiPrefix(app.wsgi_app)
    app.before_request(_serve_web_app)
    app.after_request(_refresh_session)
    app.register_blueprint(web)
    if not os.path.isdir(WEB_DIR):
        logger.warning(f"Web app not found at {WEB_DIR} — build it with `npm run build` in web/")
