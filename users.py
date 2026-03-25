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
# users.py                                                                    #
################################################################################

"""
User/key resolution for Astro API.

API keys are stored encrypted in the database (api_keys table).
Key lookup uses a two-step approach for efficiency:
    1. Fast prefix match on key_prefix (unencrypted first 8 chars)
    2. Decrypt candidate rows and compare against the provided key

Output configuration, rate limits, admin flag, and key type are all
read from the database record. The ./users/*.cfg files are no longer used.

Use key_manager.py to create, rotate, list, and manage keys.
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Module-level db reference injected by init_users() at app startup
_db_manager = None


def init_users(db_manager) -> None:
    """
    Inject the DatabaseManager instance.
    Must be called once at application startup before any key lookups.
    """
    global _db_manager
    _db_manager = db_manager
    logger.info("User/key resolution initialised (database-backed)")


def get_user_by_key(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Resolve an API key to a user dict.

    Steps:
        1. Extract the 8-char prefix from the provided key
        2. Query api_keys WHERE key_prefix = prefix AND active = 1
        3. Decrypt each candidate and compare to the provided key
        4. Return the matching user dict or None

    Returns a dict with keys:
        id, key_type, name, identifier, admin, active,
        rate_limits, output, is_domain, is_user
    """
    if not _db_manager:
        logger.error("users.py: _db_manager not initialised — call init_users() at startup")
        return None

    if not api_key or len(api_key) < 8:
        return None

    from key_crypto import KeyCrypto
    from config import Config

    try:
        crypto = KeyCrypto(Config.SECRET_KEY)
    except ValueError as e:
        logger.error(f"KeyCrypto init failed: {e}")
        return None

    prefix     = crypto.prefix(api_key)
    candidates = _db_manager.get_api_keys_by_prefix(prefix)

    for candidate in candidates:
        if crypto.verify(api_key, candidate['key_enc']):
            return _build_user_dict(candidate, _db_manager)

    return None


def get_all_user_ids() -> list:
    """Return list of all active key identifiers (domain or email)."""
    if not _db_manager:
        return []
    keys = _db_manager.get_all_api_keys(include_inactive=False)
    return [k['identifier'] for k in keys]


def reload_users() -> None:
    """
    No-op in the database-backed system.
    Keys are always read fresh from the database on each request.
    Retained for API compatibility.
    """
    logger.debug("reload_users() called — no-op in database-backed mode")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_dict(key_record: Dict[str, Any], db_manager) -> Dict[str, Any]:
    """
    Build the user dict from a database key record.
    Merges key-level rate limits with class-level defaults.

    The returned dict matches the shape previously returned by cfg-based users.py
    so auth.py and routes.py require no changes.
    """
    key_type = key_record['key_type']

    # Resolve rate limits: key-level overrides class defaults where set
    class_limits = db_manager.get_key_class_limits(key_type)
    rate_limits  = {
        'per_minute': key_record.get('rate_per_minute') or class_limits['rate_per_minute'],
        'per_hour':   key_record.get('rate_per_hour')   or class_limits['rate_per_hour'],
        'per_day':    key_record.get('rate_per_day')    or class_limits['rate_per_day'],
    }

    # Admin keys are fully exempt from rate limiting via Flask-Limiter's
    # exempt_when=_is_admin in app.py. Nulling the values here ensures
    # nothing downstream accidentally applies a limit.
    if key_record.get('admin'):
        rate_limits = {'per_minute': None, 'per_hour': None, 'per_day': None}

    return {
        'id':         str(key_record['id']),
        'name':       key_record['name'],
        'identifier': key_record['identifier'],
        'key_type':   key_type,
        'is_domain':  key_type == 'domain',
        'is_user':    key_type == 'user',
        'admin':      bool(key_record.get('admin')),
        'active':     bool(key_record.get('active', True)),
        'rate_limits': rate_limits,
        'output':      key_record.get('output_config') or {},
    }