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
# -----------------------------------------------------------------------------
DATABASE_PATH=ephemeral.db
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
CORS_HEADERS=Content-Type,Authorization,X-Requested-With,X-API-Key

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
CACHE_EXPIRY_DAYS=90
"""


def _bootstrap_env_if_missing(env_path: str = '.env') -> None:
    """
    If no .env file exists, write out a pre-filled template and exit.
    Runs before load_dotenv() so the template is always written from scratch
    rather than merging with a partial file.
    """
    if os.path.exists(env_path):
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
    CITIES_FOLDER = os.environ.get('CITIES_FOLDER', './cities')

    # Google Maps API key (for geocoding and timezone) — required when USE_GOOGLE=true
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

    # Database
    DATABASE_PATH = os.environ.get('DATABASE_PATH', 'ephemeral.db')

    # Swiss Ephemeris
    SWISS_EPHEMERIS_PATH = os.environ.get('SWISS_EPHEMERIS_PATH', './sweph')

    # Logging
    LOG_FILE  = os.environ.get('LOG_FILE', 'google_api_usage.log')
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

    # API Usage tracking
    USAGE_COUNT_FILE      = os.environ.get('USAGE_COUNT_FILE', 'api_usage_count.json')
    MAX_MONTHLY_REQUESTS  = int(os.environ.get('MAX_MONTHLY_REQUESTS', '10000'))

    # CORS settings
    CORS_ORIGINS  = os.environ.get('CORS_ORIGINS', '*').split(',')
    CORS_METHODS  = os.environ.get('CORS_METHODS', 'GET,POST,PUT,DELETE,OPTIONS').split(',')
    CORS_HEADERS  = os.environ.get('CORS_HEADERS', 'Content-Type,Authorization,X-Requested-With,X-API-Key').split(',')

    # Global rate limiting (fallback when user cfg has no per-user limits)
    RATE_LIMIT_ENABLED    = os.environ.get('RATE_LIMIT_ENABLED', 'True').lower() == 'true'
    RATE_LIMIT_PER_MINUTE = int(os.environ.get('RATE_LIMIT_PER_MINUTE', '10'))
    RATE_LIMIT_PER_HOUR   = int(os.environ.get('RATE_LIMIT_PER_HOUR', '50'))
    RATE_LIMIT_PER_DAY    = int(os.environ.get('RATE_LIMIT_PER_DAY', '200'))

    # Cache settings
    CACHE_EXPIRY_DAYS = int(os.environ.get('CACHE_EXPIRY_DAYS', '90'))

    @classmethod
    def validate(cls):
        """Validate required configuration on startup"""
        errors   = []
        warnings = []

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

        # Check SECRET_KEY is set — required for API key encryption
        if not cls.SECRET_KEY or cls.SECRET_KEY == 'your-secret-key-here':
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
            'database':              cls.DATABASE_PATH,
            'ephemeris_path':        cls.SWISS_EPHEMERIS_PATH,
            'max_monthly_requests':  cls.MAX_MONTHLY_REQUESTS,
            'cors_origins':          cls.CORS_ORIGINS,
            'rate_limiting_enabled': cls.RATE_LIMIT_ENABLED,
            'google_maps_key_set':  bool(cls.GOOGLE_MAPS_API_KEY),
            'use_google':           cls.USE_GOOGLE,
            'cities_folder':        cls.CITIES_FOLDER,
            'key_store':            'database',
            'encryption':           'Fernet (AES-128)',
        }