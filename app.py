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
# app.py                                                                      #
################################################################################

"""
Main application module for ephemeralREST
Application factory pattern with dependency injection
"""
import logging
from flask import Flask, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config
from database import DatabaseManager
from api_usage import APIUsageTracker
from auth import AuthManager, get_client_ip
from geocoding import GeocodingService
from astronomy import AstronomyService
from routes import api, init_routes
from middleware import setup_middleware, setup_request_logging
from users import init_users, get_all_user_ids


def _get_rate_limit_key():
    """
    Rate limit key — uses user ID when authenticated, falls back to IP.
    Admin users are exempt and always bypass rate limiting.
    """
    user = getattr(g, 'user', None)
    if user:
        return f"user:{user['id']}"
    return f"ip:{get_remote_address()}"



def _per_user_limit(limit_type: str, global_limit: int):
    """
    Dynamic rate limit function for Flask-Limiter.
    Returns the user's specific limit if set, otherwise the global limit.
    Admin users receive an effectively unlimited cap (999999) in the
    correct unit so the limit string is always syntactically valid.
    """
    UNIT = {'per_minute': 'minute', 'per_hour': 'hour', 'per_day': 'day'}

    def limit_value():
        user = getattr(g, 'user', None)
        if user:
            if user.get('admin', False):
                return f"999999 per {UNIT[limit_type]}"
            user_limit = user.get('rate_limits', {}).get(limit_type)
            if user_limit is not None:
                return f"{user_limit} per {UNIT[limit_type]}"
        return f"{global_limit} per {UNIT.get(limit_type, limit_type.replace('per_', ''))}"
    return limit_value


def _import_cities_if_pending(db_manager, cities_folder: str, logger) -> None:
    """
    Check CITIES_FOLDER for a .txt file. If one is found:
        1. Log that import is starting (startup will be slower).
        2. Wipe the existing cities table.
        3. Parse and bulk-insert the GeoNames cities5000 TSV format.
        4. Save import metadata (filename, row count, timestamp) to the DB.
        5. Delete the source file.
    If the import fails partway through, the transaction is rolled back so
    the existing cities data is preserved, and a loud error is logged.
    """
    import os, unicodedata

    if not cities_folder:
        return

    if not os.path.isdir(cities_folder):
        logger.warning(f"CITIES_FOLDER '{cities_folder}' does not exist — skipping cities import check")
        return

    txt_files = [f for f in os.listdir(cities_folder) if f.endswith('.txt')]
    if not txt_files:
        return

    filepath = os.path.join(cities_folder, txt_files[0])
    filename = txt_files[0]

    logger.warning(
        f"Cities import pending: found '{filename}' in {cities_folder}. "
        "Startup will be slower while the cities table is rebuilt (~200k rows)."
    )

    # GeoNames cities5000.txt column indices
    COL_GEONAME_ID   = 0
    COL_NAME         = 1
    COL_ASCII_NAME   = 2
    COL_LATITUDE     = 4
    COL_LONGITUDE    = 5
    COL_COUNTRY_CODE = 8
    COL_ADMIN1_CODE  = 10
    COL_POPULATION   = 14
    COL_TIMEZONE     = 17

    BATCH_SIZE = 1000
    rows       = []
    total      = 0

    try:
        db_manager.clear_cities()
        logger.info("Cities table cleared — beginning import")

        with open(filepath, encoding='utf-8') as fh:
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 19:
                    continue  # malformed line — skip silently

                try:
                    row = (
                        int(parts[COL_GEONAME_ID]),
                        parts[COL_NAME],
                        parts[COL_ASCII_NAME].lower(),   # stored lowercase for LIKE matching
                        parts[COL_COUNTRY_CODE],
                        parts[COL_ADMIN1_CODE] or None,
                        float(parts[COL_LATITUDE]),
                        float(parts[COL_LONGITUDE]),
                        parts[COL_TIMEZONE],
                        int(parts[COL_POPULATION]) if parts[COL_POPULATION] else 0,
                    )
                except (ValueError, IndexError):
                    continue  # skip lines with unparseable numeric fields

                rows.append(row)
                if len(rows) >= BATCH_SIZE:
                    db_manager.bulk_insert_cities(rows)
                    total += len(rows)
                    rows = []

            if rows:
                db_manager.bulk_insert_cities(rows)
                total += len(rows)

        db_manager.save_cities_import_meta(filename, total)
        logger.info(f"Cities import complete: {total} rows from '{filename}'")

        os.remove(filepath)
        logger.info(f"Cities source file '{filepath}' deleted after successful import")

    except Exception as e:
        logger.error(
            f"Cities import FAILED for '{filename}': {e}. "
            "Existing cities data has been preserved (transaction rolled back).",
            exc_info=True
        )


def create_app(config_class=Config):
    """Application factory"""

    # Validate configuration
    config_class.validate()

    # Create Flask app
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Setup logging
    setup_logging(config_class)
    logger = logging.getLogger(__name__)
    logger.info("Starting ephemeralREST application")
    logger.info(f"Configuration: {config_class.get_summary()}")
    logger.info(f"User key resolution: database-backed")

    # Setup CORS
    CORS(app,
         origins=config_class.CORS_ORIGINS,
         methods=config_class.CORS_METHODS,
         allow_headers=config_class.CORS_HEADERS,
         supports_credentials=True)

    # Initialize components
    db_manager = DatabaseManager(config_class.DATABASE_PATH)
    usage_tracker = APIUsageTracker(
        config_class.USAGE_COUNT_FILE,
        config_class.MAX_MONTHLY_REQUESTS
    )
    auth_manager = AuthManager(debug_mode=config_class.FLASK_DEBUG)

    # Cities5000 auto-import: if any .txt file is present in CITIES_FOLDER,
    # wipe the existing cities table, import the file, then delete it.
    _import_cities_if_pending(db_manager, config_class.CITIES_FOLDER, logger)

    geocoding_service = GeocodingService(
        config_class.GOOGLE_MAPS_API_KEY,
        db_manager,
        usage_tracker,
        use_google=config_class.USE_GOOGLE
    )
    astronomy_service = AstronomyService(config_class.SWISS_EPHEMERIS_PATH)

    # Initialise database-backed user/key resolution
    init_users(db_manager)

    # Initialize routes with dependencies
    init_routes(
        db_manager,
        geocoding_service,
        astronomy_service,
        usage_tracker,
        auth_manager
    )

    # Register blueprints
    app.register_blueprint(api)

    # Setup middleware
    setup_middleware(app)

    # Apply authentication to protected endpoints
    _protected = [
        'api.calculate',
        'api.cache_cleanup',
        'api.secondary_progressions',
        'api.solar_arc_directions',
        'api.solar_return',
        'api.lunar_return',
        'api.get_derived_charts',
        'api.get_derived_chart',
        'api.delete_derived_chart',
        'api.me',
        'api.me_rotate',
        'api.me_get_output',
        'api.admin_get_key_output',
        'api.me_output',
        'api.admin_list_keys',
        'api.admin_get_key',
        'api.admin_disable_key',
        'api.admin_enable_key',
        'api.admin_rotate_key',
        'api.admin_set_key_limits',
        'api.admin_set_key_output',
        'api.admin_delete_key',
        'api.admin_get_smtp',
        'api.admin_set_smtp',
        'api.admin_test_smtp',
        'api.admin_clear_smtp',
        'api.admin_get_class_limits',
        'api.admin_set_class_limits',
        'api.apsides',
        'api.next_apsides',
        'api.lunations',
        'api.ephemeris',
        'api.eclipses',
        'api.save_view',
        'api.search_archive',
        'api.get_archive_entry',
        'api.update_view',
        'api.admin_list_registrations',
        'api.admin_approve_registration',
        'api.admin_reject_registration',
        'api.admin_get_email_template',
        'api.admin_set_email_template',
        'api.admin_reset_email_template',
        'api.admin_set_key_admin',
        'api.admin_set_key_type',
    ]
    for _endpoint in _protected:
        app.view_functions[_endpoint] = auth_manager.require_api_key(
            app.view_functions[_endpoint]
        )
    logger.info(f"API authentication enabled for: {_protected}")

    # Setup rate limiting with per-user dynamic limits
    if config_class.RATE_LIMIT_ENABLED:
        limiter = Limiter(
            app=app,
            key_func=_get_rate_limit_key,
            default_limits=[
                _per_user_limit('per_day',  config_class.RATE_LIMIT_PER_DAY),
                _per_user_limit('per_hour', config_class.RATE_LIMIT_PER_HOUR),
            ],
            storage_uri="memory://"
        )

        limiter.limit(
            _per_user_limit('per_minute', config_class.RATE_LIMIT_PER_MINUTE)
        )(app.view_functions['api.calculate'])

        logger.info("Per-user rate limiting enabled")

    logger.info("Application initialisation complete")

    return app


def setup_logging(config):
    """Setup application logging"""
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)

    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(logging.INFO)
    file_formatter = setup_request_logging()
    file_handler.setFormatter(file_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.LOG_LEVEL))
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def print_startup_info(config):
    """Print startup information"""
    print("\n" + "=" * 60)
    print("🌟 ephemeralREST Server Starting")
    print("=" * 60)
    print(f"📍 Host:                {config.FLASK_HOST}")
    print(f"🔌 Port:                {config.FLASK_PORT}")
    print(f"🐛 Debug:               {config.FLASK_DEBUG}")
    print(f"📊 Swiss Ephemeris:     {config.SWISS_EPHEMERIS_PATH}")
    print(f"📈 Max Monthly Req:     {config.MAX_MONTHLY_REQUESTS}")
    print(f"📝 Log File:            {config.LOG_FILE}")
    print(f"🗄️  Database:            {config.DATABASE_PATH}")
    print(f"🌐 CORS Origins:        {', '.join(config.CORS_ORIGINS)}")
    print(f"👥 Key store:           database")
    print(f"⚡ Rate Limiting:       {'Enabled (per-user)' if config.RATE_LIMIT_ENABLED else 'Disabled'}")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    app = create_app()
    print_startup_info(Config)
    app.run(
        debug=Config.FLASK_DEBUG,
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT
    )

app = create_app()