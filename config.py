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
# config.py                                                                   #
################################################################################

"""
Configuration module for Ephemeral.REST
Handles environment variables and application settings.

User API keys are NOT stored here — they are stored in .env and referenced
by the api_key_env field in each user's cfg file under ./users/.

Required .env entries:
    GOOGLE_MAPS_API_KEY=...
    SECRET_KEY=...
    API_KEY_ADMIN=...
    API_KEY_COSMOBIOLOGY_ONLINE=...
    API_KEY_MINDFORGE=...
    (one API_KEY_<NAME> entry per user cfg file)
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration"""

    # Flask settings
    FLASK_HOST  = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT  = int(os.environ.get('FLASK_PORT', '5000'))
    FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    SECRET_KEY  = os.environ.get('SECRET_KEY', os.urandom(24).hex())

    # Google Maps API key (for geocoding and timezone)
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY')

    # Note: individual API_KEY_* entries are no longer used.
    # API keys are stored encrypted in the database.
    # Use key_manager.py to create and manage keys.

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

        # Google Maps API key
        if not cls.GOOGLE_MAPS_API_KEY:
            errors.append("GOOGLE_MAPS_API_KEY is required")
        elif cls.GOOGLE_MAPS_API_KEY == 'your-google-maps-api-key':
            errors.append("GOOGLE_MAPS_API_KEY must not be the placeholder value")
        elif len(cls.GOOGLE_MAPS_API_KEY) < 20:
            warnings.append("GOOGLE_MAPS_API_KEY looks suspicious — verify it is valid")

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
            'key_store':            'database',
            'encryption':           'Fernet (AES-128)',
        }