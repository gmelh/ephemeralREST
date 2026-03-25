################################################################################
#                                                                              #
#  Ephemeral.REST — Swiss Ephemeris REST API                                   #
#  Copyright (C) 2026  Ephemeral.REST contributors                             #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
#  This program is distributed in the hope that it will be useful,            #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the              #
#  GNU Affero General Public License for more details.                         #
#                                                                              #
#  You should have received a copy of the GNU Affero General Public License   #
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.    #
#                                                                              #
#  ADDITIONAL NOTICE — Swiss Ephemeris dependency:                             #
#  This software uses the Swiss Ephemeris library developed by                #
#  Astrodienst AG, Zurich, Switzerland. The Swiss Ephemeris is licensed       #
#  under the GNU Affero General Public License (AGPL) v3. Use of this        #
#  software therefore requires compliance with the AGPL v3, which includes    #
#  the obligation to make source code available to users who interact with    #
#  this software over a network.                                              #
#  See https://www.astro.com/swisseph/ for full details.                      #
#                                                                              #
################################################################################
################################################################################
# auth.py                                                                     #
################################################################################

"""
Authentication module for Astro API.
Handles multi-user API key authentication.
Matched user is injected into Flask g so routes can access user config.
"""
import logging
from functools import wraps
from flask import request, jsonify, g

from users import get_user_by_key

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages multi-user API key authentication"""

    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode

    def get_user(self, api_key: str) -> dict | None:
        """Look up user by API key. Returns user dict or None."""
        if not api_key:
            return None
        return get_user_by_key(api_key)

    def require_api_key(self, f):
        """
        Decorator that enforces API key authentication.
        On success, injects the matched user into Flask g as g.user.
        Admin users bypass key validation.
        In debug mode with no key provided, a default guest user is injected.
        """

        @wraps(f)
        def decorated_function(*args, **kwargs):
            api_key = request.headers.get('X-API-Key')

            # Debug mode with no key — inject a guest user and continue
            if self.debug_mode and not api_key:
                g.user = _guest_user()
                logger.debug("Debug mode: unauthenticated request allowed")
                return f(*args, **kwargs)

            # No key provided
            if not api_key:
                logger.warning(f"Request to {request.path} missing API key")
                return jsonify({
                    'error':   'Unauthorized',
                    'status':  401,
                    'message': 'API key required. Include X-API-Key header.'
                }), 401

            # Look up user
            user = self.get_user(api_key)
            if not user:
                logger.warning(f"Request to {request.path} with unrecognised API key")
                return jsonify({
                    'error':   'Unauthorized',
                    'status':  401,
                    'message': 'Invalid API key.'
                }), 401

            # Inject user into request context
            g.user = user
            logger.info(f"Authenticated user: {user['name']} ({user['id']})")
            return f(*args, **kwargs)

        return decorated_function


def _guest_user() -> dict:
    """Minimal guest user for debug mode — inherits all server defaults."""
    return {
        'id':      'guest',
        'name':    'Guest (debug)',
        'admin':   False,
        'rate_limits': {
            'per_minute': None,
            'per_hour':   None,
            'per_day':    None,
        },
        'output': {}
    }


def get_client_ip() -> str:
    """Get client IP address from request, handling proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr or 'unknown'