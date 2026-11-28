################################################################################
#                                                                              #
#  ephemeralREST — Swiss Ephemeris REST API                                   #
#  Copyright (C) 2026  ephemeralREST contributors                             #
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
# config.py                                                                   #
################################################################################

"""
Configuration module for ephemeralREST
Handles environment variables and application settings.

API keys are stored encrypted in the database. Use key_manager.py to create
and manage keys — no key values belong in .env or config files.

Required .env entries:
    SECRET_KEY=...              — used for API key encryption (Fernet/AES-128)
    GOOGLE_MAPS_API_KEY=...     — required when USE_GOOGLE=true, omit otherwise

Optional .env entries:
    USE_GOOGLE=true             — set false for fully offline (cities5000) mode
    CITIES_FOLDER=./cities      — drop cities5000.txt here to trigger a reload
"""
import os
import sys
from dotenv import load_dotenv


_ENV_TEMPLATE = """\
# =============================================================================
# ephemeralREST — environment configuration
# Edit this file then restart the service.
# =============================================================================

# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=false
SECRET_KEY=                         # required — generate with: python3 -c "import secrets; print(secrets.token_hex(32))"

# -----------------------------------------------------------------------------
# Geocoding mode
# USE_GOOGLE=true  → cities5000 autocomplete + Google geocoding (hybrid)
# USE_GOOGLE=false → fully offline, cities5000 for everything, no API key needed
# -----------------------------------------------------------------------------
USE_GOOGLE=true
GOOGLE_MAPS_API_KEY=                # required when USE_GOOGLE=true

# -----------------------------------------------------------------------------
# GeoNames cities5000 auto-import
# Drop a cities5000.txt file into this folder and restart to reload city data.
# Download from: https://download.geonames.org/export/dump/cities5000.zip
# -----------------------------------------------------------------------------
CITIES_FOLDER=./cities

# -----------------------------------------------------------------------------
# Database and ephemeris
# DB_TYPE=sqlite → default, self-contained, zero setup, database file below
# DB_TYPE=mysql  → requires MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/
#                  MYSQL_DATABASE, all set below. DATABASE_PATH is ignored.
# -----------------------------------------------------------------------------
DB_TYPE=sqlite
DATABASE_PATH=ephemeral.db

# Only required when DB_TYPE=mysql. The database itself must already exist —
# this app creates its tables on first run but will not create the schema.
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
# TLS to MySQL. MYSQL_SSL_CA is the server's CA certificate (on Vultr Managed
# MySQL: "Download Signed Certificate"). MYSQL_SSL_MODE is one of DISABLED,
# PREFERRED, REQUIRED, VERIFY_CA, VERIFY_IDENTITY — VERIFY_CA when a CA is
# set, otherwise PREFERRED (encrypted if the server offers it, not checked).
MYSQL_SSL_CA=
MYSQL_SSL_MODE=

SWISS_EPHEMERIS_PATH=./sweph

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_FILE=ephemeral.log
LOG_LEVEL=INFO

# -----------------------------------------------------------------------------
# Google API usage cap (ignored when USE_GOOGLE=false)
# -----------------------------------------------------------------------------
USAGE_COUNT_FILE=api_usage_count.json
MAX_MONTHLY_REQUESTS=10000

# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
CORS_ORIGINS=*
CORS_METHODS=GET,POST,PUT,DELETE,OPTIONS
CORS_HEADERS=Content-Type,Authorization,X-Requested-With,X-API-Key,If-Match,If-None-Match

# -----------------------------------------------------------------------------
# Rate limiting
# -----------------------------------------------------------------------------
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=10
RATE_LIMIT_PER_HOUR=50
RATE_LIMIT_PER_DAY=200

# -----------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------
# Days a main chart can go unaccessed before it (and its derived charts)
# is removed from the cache. Default is 3 years. The permanent chart_archive
# is never removed.
CACHE_EXPIRY_DAYS=1095

# -----------------------------------------------------------------------------
# Account store — per-account JSON documents at /account/store
# (see account_store.py and DOCS/API_REFERENCE.md)
# -----------------------------------------------------------------------------
# Turn the whole feature on or off.
STORE_ENABLED=true

# When true, only keys granted the 'store' service (Keys page / admin API)
# can use it; admin keys always can. When false, any real API key can.
STORE_REQUIRE_GRANT=false

# Size limits, in bytes: one document's content, one document's meta, and
# the total of all live documents per account. Plus a document count.
STORE_MAX_DOC_BYTES=1048576
STORE_MAX_META_BYTES=2048
STORE_MAX_ACCOUNT_BYTES=52428800
STORE_MAX_DOCS=10000

# Days a deletion marker is kept so other devices can learn about the
# delete. Purged by `python cleanup.py --store`. Keep this longer than any
# device is likely to go without syncing.
STORE_TOMBSTONE_DAYS=180

# Syncing a whole namespace can take many requests. When true, store
# requests don't count towards the per-key hourly/daily rate limits.
STORE_RATE_LIMIT_EXEMPT=false

# -----------------------------------------------------------------------------
# Login / 2FA / trusted devices
# -----------------------------------------------------------------------------
# Number of days a "trusted device" cookie remains valid before 2FA is
# required again on login.
TRUSTED_DEVICE_DAYS=28

# Minutes a 2FA login code remains valid.
TWO_FACTOR_CODE_EXPIRY_MINUTES=10

# -----------------------------------------------------------------------------
# Address and web app
# -----------------------------------------------------------------------------
# The one address this server is reached at, e.g. https://astro.example.com.
# The web app is at /, the API at /api/v1 (and, for older clients, at the
# root), and links in emails point here.
PUBLIC_URL=http://localhost:5000

# Whether anyone may create an account from the web app's Register page.
ALLOW_REGISTRATION=true

# Signed-in browsers: logged out after this many idle seconds, and after
# this many hours whatever happens.
SESSION_TIMEOUT=1800
SESSION_MAX_HOURS=168

# Behind a reverse proxy (Caddy, nginx) that sets X-Forwarded-For/-Proto?
TRUST_PROXY=false
# Administration (everything under /admin, and the web app's admin pages) only at this
# address, e.g. https://admin.example.com. Empty: at any address, as before.
ADMIN_URL=
# Only believe those headers from these addresses (comma separated). The installer sets the
# server's shared Caddy (172.31.250.2): other containers share its network and could otherwise
# claim to be any address. Empty: from any address, as before.
TRUSTED_PROXIES=

# Shown in the web app's header and the browser tab.
SITE_NAME=ephemeralREST

# Whether administrators may make other accounts administrators. Set to
# false to lock this down once your admin accounts are established —
# enforced by the API itself.
ALLOW_ADMIN_PROMOTION=true

# Older two-service deployments only (ephemeralADMIN as a separate portal):
# with PUBLIC_URL unset, email links go to PORTAL_URL, falling back to
# API_BASE_URL. Leave these out when PUBLIC_URL is set.
# API_BASE_URL=http://localhost:5000
# PORTAL_URL=http://localhost:3000
"""


def _bootstrap_env_if_missing(env_path: str = '.env') -> None:
    """
    If no .env file exists, write out a pre-filled template and exit.
    Runs before load_dotenv() so the template is always written from scratch
    rather than merging with a partial file.
    """
    if os.path.exists(env_path):
        return
    # In the container (and tests) settings come from the environment itself.
    if os.environ.get('EPHEMERAL_NO_ENV_FILE') or os.environ.get('EPHEMERAL_DATA_DIR'):
        return

    with open(env_path, 'w', encoding='utf-8') as fh:
        fh.write(_ENV_TEMPLATE)

    msg = (
        f"\n"
        f"  ephemeralREST — first-run setup\n"
        f"  --------------------------------\n"
        f"  No .env file was found. A template has been written to '{env_path}'.\n"
        f"  Please edit it — at minimum set SECRET_KEY and (if USE_GOOGLE=true)\n"
        f"  GOOGLE_MAPS_API_KEY — then restart the service.\n"
    )
    print(msg, file=sys.stderr)
    sys.exit(0)


_bootstrap_env_if_missing()
load_dotenv()


# In the container everything the server keeps lives under one directory
# (EPHEMERAL_DATA_DIR, /data in the image); the paths below default to it.
_DATA_DIR = os.environ.get('EPHEMERAL_DATA_DIR', '').strip()


def _data_path(env_var: str, default: str, name: str) -> str:
    if os.environ.get(env_var):
        return os.environ[env_var]
    return os.path.join(_DATA_DIR, name) if _DATA_DIR else default


class Config:
    """Application configuration"""

    # Flask settings
    FLASK_HOST  = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT  = int(os.environ.get('FLASK_PORT', '5000'))
    FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    SECRET_KEY  = os.environ.get('SECRET_KEY', os.urandom(24).hex())

    # Geocoding mode
    # USE_GOOGLE=true  → hybrid: cities5000 autocomplete + Google geocoding
    # USE_GOOGLE=false → fully offline: cities5000 for both autocomplete and geocoding
    USE_GOOGLE = os.environ.get('USE_GOOGLE', 'true').lower() == 'true'

    # Folder watched on startup for a GeoNames cities5000 .txt file.
    # If a .txt file is present, the cities table is wiped, the file is
    # imported, and the file is deleted on completion.
    CITIES_FOLDER = _data_path('CITIES_FOLDER', './cities', 'cities')

    # Google Maps API key (for geocoding and timezone) — required when USE_GOOGLE=true
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

    # Database
    # DB_TYPE: 'sqlite' (default) or 'mysql'
    DB_TYPE       = os.environ.get('DB_TYPE', 'sqlite').strip().lower()
    DATABASE_PATH = _data_path('DATABASE_PATH', 'ephemeral.db', 'ephemeral.db')

    # MySQL connection settings — only used when DB_TYPE=mysql
    MYSQL_HOST     = os.environ.get('MYSQL_HOST', 'localhost')
    MYSQL_PORT     = int(os.environ.get('MYSQL_PORT', '3306'))
    MYSQL_USER     = os.environ.get('MYSQL_USER', '')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', '')
    MYSQL_SSL_CA   = os.environ.get('MYSQL_SSL_CA', '')
    MYSQL_SSL_MODE = os.environ.get('MYSQL_SSL_MODE', '').strip().upper()

    # Swiss Ephemeris
    SWISS_EPHEMERIS_PATH = _data_path('SWISS_EPHEMERIS_PATH', './sweph', 'sweph')

    # Logging
    LOG_FILE  = _data_path('LOG_FILE', 'google_api_usage.log', 'logs/ephemeral.log')
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

    # API Usage tracking
    USAGE_COUNT_FILE      = _data_path('USAGE_COUNT_FILE', 'api_usage_count.json', 'api_usage_count.json')
    MAX_MONTHLY_REQUESTS  = int(os.environ.get('MAX_MONTHLY_REQUESTS', '10000'))

    # CORS settings
    CORS_ORIGINS  = os.environ.get('CORS_ORIGINS', '*').split(',')
    CORS_METHODS  = os.environ.get('CORS_METHODS', 'GET,POST,PUT,DELETE,OPTIONS').split(',')
    CORS_HEADERS  = os.environ.get('CORS_HEADERS', 'Content-Type,Authorization,X-Requested-With,X-API-Key,If-Match,If-None-Match').split(',')

    # Global rate limiting (fallback when user cfg has no per-user limits)
    RATE_LIMIT_ENABLED    = os.environ.get('RATE_LIMIT_ENABLED', 'True').lower() == 'true'
    RATE_LIMIT_PER_MINUTE = int(os.environ.get('RATE_LIMIT_PER_MINUTE', '10'))
    RATE_LIMIT_PER_HOUR   = int(os.environ.get('RATE_LIMIT_PER_HOUR', '50'))
    RATE_LIMIT_PER_DAY    = int(os.environ.get('RATE_LIMIT_PER_DAY', '200'))

    # Cache settings
    CACHE_EXPIRY_DAYS = int(os.environ.get('CACHE_EXPIRY_DAYS', '1095'))

    # Account store (account_store.py)
    STORE_ENABLED           = os.environ.get('STORE_ENABLED', 'true').strip().lower() in ('true', '1', 'yes')
    STORE_REQUIRE_GRANT     = os.environ.get('STORE_REQUIRE_GRANT', 'false').strip().lower() in ('true', '1', 'yes')
    STORE_MAX_DOC_BYTES     = int(os.environ.get('STORE_MAX_DOC_BYTES', '1048576'))
    STORE_MAX_META_BYTES    = int(os.environ.get('STORE_MAX_META_BYTES', '2048'))
    STORE_MAX_ACCOUNT_BYTES = int(os.environ.get('STORE_MAX_ACCOUNT_BYTES', '52428800'))
    STORE_MAX_DOCS          = int(os.environ.get('STORE_MAX_DOCS', '10000'))
    STORE_TOMBSTONE_DAYS    = int(os.environ.get('STORE_TOMBSTONE_DAYS', '180'))
    STORE_RATE_LIMIT_EXEMPT = os.environ.get('STORE_RATE_LIMIT_EXEMPT', 'false').strip().lower() in ('true', '1', 'yes')

    # Login / 2FA / trusted devices
    TRUSTED_DEVICE_DAYS             = int(os.environ.get('TRUSTED_DEVICE_DAYS', '28'))
    TWO_FACTOR_CODE_EXPIRY_MINUTES  = int(os.environ.get('TWO_FACTOR_CODE_EXPIRY_MINUTES', '10'))

    # Portal branding and behaviour — deliberately config-only (see the
    # .env template above for why), read by the portal via /public-config
    # and enforced server-side here for ALLOW_ADMIN_PROMOTION specifically.
    SITE_NAME              = os.environ.get('SITE_NAME', 'ephemeralREST')
    ALLOW_ADMIN_PROMOTION  = os.environ.get('ALLOW_ADMIN_PROMOTION', 'true').strip().lower() in ('true', '1', 'yes')

    # Web app (web.py). PUBLIC_URL is the one address everything lives at —
    # the web app, the API under /api/v1, and the links in emails.
    PUBLIC_URL         = os.environ.get('PUBLIC_URL', '').strip().rstrip('/')
    ALLOW_REGISTRATION = os.environ.get('ALLOW_REGISTRATION', 'true').strip().lower() in ('true', '1', 'yes')
    # Signed-in browsers: idle timeout (seconds, renewed on use) and the most
    # a session can last before logging in again (hours).
    SESSION_TIMEOUT    = int(os.environ.get('SESSION_TIMEOUT', '1800'))
    SESSION_MAX_HOURS  = int(os.environ.get('SESSION_MAX_HOURS', '168'))
    # Behind a reverse proxy (Caddy in the standard install): trust one hop of
    # X-Forwarded-For/-Proto/-Host for client IPs and https detection.
    TRUST_PROXY        = os.environ.get('TRUST_PROXY', 'false').strip().lower() in ('true', '1', 'yes')
    TRUSTED_PROXIES    = [a.strip() for a in os.environ.get('TRUSTED_PROXIES', '').split(',') if a.strip()]
    # Where administration answers (sites.py): everything under /admin, only at this address.
    # ADMIN_DOMAIN (as the installer writes it) is the same as ADMIN_URL=https://ADMIN_DOMAIN.
    ADMIN_URL          = (os.environ.get('ADMIN_URL', '').strip().rstrip('/')
                          or (f"https://{os.environ['ADMIN_DOMAIN'].strip()}"
                              if os.environ.get('ADMIN_DOMAIN', '').strip() else ''))

    @classmethod
    def validate(cls):
        """Validate required configuration on startup"""
        errors   = []
        warnings = []

        # Database type
        if cls.DB_TYPE not in ('sqlite', 'mysql'):
            errors.append(
                f"DB_TYPE must be 'sqlite' or 'mysql', got '{cls.DB_TYPE}'"
            )
        elif cls.DB_TYPE == 'mysql':
            if not cls.MYSQL_USER:
                errors.append("MYSQL_USER is required when DB_TYPE=mysql")
            if not cls.MYSQL_DATABASE:
                errors.append("MYSQL_DATABASE is required when DB_TYPE=mysql")
            mode = cls.MYSQL_SSL_MODE or ('VERIFY_CA' if cls.MYSQL_SSL_CA else 'PREFERRED')
            if mode not in ('DISABLED', 'PREFERRED', 'REQUIRED', 'VERIFY_CA', 'VERIFY_IDENTITY'):
                errors.append(f"MYSQL_SSL_MODE={cls.MYSQL_SSL_MODE} isn't known")
            elif mode.startswith('VERIFY') and not cls.MYSQL_SSL_CA:
                errors.append(f"MYSQL_SSL_MODE={mode} needs MYSQL_SSL_CA")
            elif mode in ('DISABLED', 'PREFERRED') and cls.MYSQL_HOST not in ('localhost', '127.0.0.1', '::1'):
                warnings.append(f"MYSQL_SSL_MODE={mode}: the connection to {cls.MYSQL_HOST} "
                                "isn't checked. Set MYSQL_SSL_CA to verify the server.")
            if not cls.MYSQL_PASSWORD:
                warnings.append("MYSQL_PASSWORD is empty — only appropriate for local/dev MySQL")

        # Google Maps API key — only required when USE_GOOGLE=true
        if cls.USE_GOOGLE:
            if not cls.GOOGLE_MAPS_API_KEY:
                errors.append(
                    "GOOGLE_MAPS_API_KEY is required when USE_GOOGLE=true. "
                    "Set USE_GOOGLE=false to run in fully offline (cities5000) mode."
                )
            elif cls.GOOGLE_MAPS_API_KEY == 'your-google-maps-api-key':
                errors.append("GOOGLE_MAPS_API_KEY must not be the placeholder value")
            elif len(cls.GOOGLE_MAPS_API_KEY) < 20:
                warnings.append("GOOGLE_MAPS_API_KEY looks suspicious — verify it is valid")
        else:
            if cls.GOOGLE_MAPS_API_KEY:
                warnings.append(
                    "USE_GOOGLE=false but GOOGLE_MAPS_API_KEY is set — key will be ignored"
                )

        # Account store limits must be positive
        if cls.STORE_ENABLED:
            for name in ('STORE_MAX_DOC_BYTES', 'STORE_MAX_META_BYTES',
                         'STORE_MAX_ACCOUNT_BYTES', 'STORE_MAX_DOCS', 'STORE_TOMBSTONE_DAYS'):
                if getattr(cls, name) <= 0:
                    errors.append(f"{name} must be greater than 0")
            if cls.STORE_MAX_DOC_BYTES > cls.STORE_MAX_ACCOUNT_BYTES:
                warnings.append("STORE_MAX_DOC_BYTES is larger than STORE_MAX_ACCOUNT_BYTES")

        # Web app address
        if not cls.PUBLIC_URL:
            warnings.append("PUBLIC_URL is not set — links in emails may point to the wrong address")
        elif os.environ.get('PORTAL_URL') or os.environ.get('API_PUBLIC_URL'):
            warnings.append("PORTAL_URL / API_PUBLIC_URL are no longer used when PUBLIC_URL is set — you can remove them")

        # Check SECRET_KEY is set — required for API key encryption. (Without
        # one, a random key would be made at every start, and stored API keys
        # could never be decrypted again.)
        if not os.environ.get('SECRET_KEY') or cls.SECRET_KEY == 'your-secret-key-here':
            if not cls.FLASK_DEBUG:
                errors.append("SECRET_KEY must be set to a secure value for API key encryption")
            else:
                warnings.append("SECRET_KEY is not set — API key encryption will fail")
        if warnings:
            print("\n⚠️  Configuration Warnings:")
            for warning in warnings:
                print(f"   - {warning}")
            print()

        if errors:
            print("\n❌ Configuration Errors:")
            for error in errors:
                print(f"   - {error}")
            print()
            raise EnvironmentError("Configuration errors found. Please check your .env file.")

        return True

    @classmethod
    def get_summary(cls):
        """Get configuration summary for logging"""
        return {
            'host':                  cls.FLASK_HOST,
            'port':                  cls.FLASK_PORT,
            'debug':                 cls.FLASK_DEBUG,
            'db_type':               cls.DB_TYPE,
            'database':              cls.DATABASE_PATH if cls.DB_TYPE == 'sqlite'
                                      else f"mysql://{cls.MYSQL_HOST}:{cls.MYSQL_PORT}/{cls.MYSQL_DATABASE}",
            'ephemeris_path':        cls.SWISS_EPHEMERIS_PATH,
            'max_monthly_requests':  cls.MAX_MONTHLY_REQUESTS,
            'cors_origins':          cls.CORS_ORIGINS,
            'rate_limiting_enabled': cls.RATE_LIMIT_ENABLED,
            'google_maps_key_set':  bool(cls.GOOGLE_MAPS_API_KEY),
            'use_google':           cls.USE_GOOGLE,
            'cities_folder':        cls.CITIES_FOLDER,
            'key_store':            'database',
            'encryption':           'Fernet (AES-128)',
            'account_store':        ('enabled' + (' (grant required)' if cls.STORE_REQUIRE_GRANT else ''))
                                    if cls.STORE_ENABLED else 'disabled',
        }