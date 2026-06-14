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
# routes.py                                                                   #
################################################################################

"""
API routes for ephemeralREST
Defines all endpoints and their handlers
"""
import logging
import pytz
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from validators import validate_request, CalculateSchema, AutocompleteSchema, ProgressionSchema, SolarReturnSchema, LunarReturnSchema, ApsideSchema, LunationSchema, NextApsideSchema, EphemerisSchema, EclipseSchema, RegisterSchema, AdminReviewSchema, SaveViewSchema, LoginSchema, Login2FASchema, SetPasswordSchema, SetupSchema
from output_config import OutputConfig
from email_service import EmailService
import secrets as _secrets

logger = logging.getLogger(__name__)


def _error(message: str, status: int):
    """Return a consistent error response including the HTTP status code."""
    return jsonify({'error': message, 'status': status}), status

# Create blueprint
api = Blueprint('api', __name__)

# These will be injected by the app factory
db_manager        = None
geocoding_service = None
astronomy_service = None
usage_tracker     = None
auth_manager      = None


def init_routes(db, geo_service, astro_service, usage_track, auth_mgr):
    """Initialize route dependencies"""
    global db_manager, geocoding_service, astronomy_service, usage_tracker, auth_manager
    db_manager        = db
    geocoding_service = geo_service
    astronomy_service = astro_service
    usage_tracker     = usage_track
    auth_manager      = auth_mgr


@api.route('/autocomplete', methods=['GET'])
@validate_request(AutocompleteSchema)
def autocomplete(validated_data):
    """Autocomplete endpoint for location search"""
    query = validated_data['q']

    if len(query) < 2:
        return jsonify({'predictions': []})

    # Usage tracking only applies when Google is the geocoding backend.
    # In cities5000 mode autocomplete is fully offline — no counter needed.
    if geocoding_service.use_google:
        if not usage_tracker.check_and_increment():
            stats = usage_tracker.get_usage_stats()
            return jsonify({
                'error': 'Google API usage limit exceeded for this month',
                'usage_stats': stats
            }), 429

    result = geocoding_service.autocomplete(query)
    return jsonify(result)


@api.route('/calculate', methods=['POST'])
@validate_request(CalculateSchema)
def calculate(validated_data):
    """
    Main calculation endpoint.

    Output priority (lowest → highest):
        1. OutputConfig server defaults  (output_config.py)
        2. User output config            (users.py per-user output block)
        3. Per-request output overrides  (request body 'output' field)
    """
    try:
        chart_name       = validated_data['chart_name']
        datetime_str     = validated_data['datetime']
        location         = validated_data['location']
        house_system      = validated_data.get('house_system')
        request_overrides = validated_data.get('output') or {}
        recalc            = validated_data.get('recalc', False)
        recalc_chart_id   = validated_data.get('chart_id')

        # Validate recalc request
        if recalc and not recalc_chart_id:
            return _error('chart_id is required when recalc is true', 400)

        if recalc and recalc_chart_id:
            existing = db_manager.get_chart_by_id(recalc_chart_id)
            if not existing:
                return _error(f'Chart {recalc_chart_id} not found', 404)

        # Get the authenticated user's output config
        user = getattr(g, 'user', {})
        user_output = user.get('output', {})

        # Build merged config: server defaults → user config → request overrides
        output_cfg = OutputConfig.merge(user_output)       # server + user
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)  # + request

        # House system: request param → user default → server default
        if house_system is None:
            house_system = output_cfg.get('default_house_system')

        # Parse datetime
        dt = _parse_datetime(datetime_str)
        if dt is None:
            return jsonify({
                'error': 'Invalid datetime format. Use ISO format or YYYY-MM-DD HH:MM:SS'
            }), 400

        # Geocode location
        location_info, error = geocoding_service.geocode_location(location)
        if error:
            return _error(error, 400)

        # Convert to UTC
        dt_utc, dt_local = _convert_to_utc(dt, location_info['timezone'])

        # Compute DST for the chart's actual datetime using pytz.
        # This is always correct regardless of when the location was cached —
        # Google's cached DST value reflects now, not the chart's datetime.
        import pytz as _pytz
        _tz        = _pytz.timezone(location_info['timezone'])
        _dst_delta = dt_local.dst()
        _utc_delta = dt_local.utcoffset()
        dst_seconds = int(_dst_delta.total_seconds()) if _dst_delta else 0
        utc_seconds = int(_utc_delta.total_seconds()) if _utc_delta else 0
        location_info['dst_offset_seconds'] = dst_seconds
        location_info['utc_offset_seconds'] = utc_seconds
        location_info['daylight_saving']    = dst_seconds != 0

        logger.info(
            f"[{user.get('name', 'unknown')}] Calculating for {dt_utc} "
            f"at {location_info['id']} (house_system={house_system or 'none'})"
        )

        # Calculate positions
        result, error = astronomy_service.calculate_planetary_positions(
            dt_utc,
            location_info['latitude'],
            location_info['longitude'],
            house_system=house_system,
            output_config=output_cfg
        )

        if error:
            return _error(error, 500)

        # Save chart — recalc updates the existing record in place by UUID,
        # normal save uses hash-based upsert (may create a new record)
        if recalc and recalc_chart_id:
            db_manager.update_chart_data_by_id(
                recalc_chart_id, result, dt_utc, dt_local
            )
            chart_id    = recalc_chart_id
            recalc_note = validated_data.get('recalc_note')
            db_manager.record_recalculation(
                chart_id       = chart_id,
                chart_name     = chart_name,
                datetime_utc   = dt_utc.isoformat(),
                datetime_local = dt_local.isoformat(),
                location       = location_info['formatted_address'],
                note           = recalc_note,
            )
            logger.info(
                f"[{user.get('name', 'unknown')}] Recalculated chart {chart_id}"
                + (f" — note: {recalc_note}" if recalc_note else "")
            )
        else:
            chart_id = db_manager.save_chart_to_cache(
                dt_utc, dt_local, location_info['id'], result, chart_name,
                house_system=house_system
            )
            # Archive every new chart permanently — survives cache cleanup
            db_manager.archive_chart(
                chart_id       = chart_id,
                chart_name     = chart_name,
                datetime_utc   = dt_utc.isoformat(),
                datetime_local = dt_local.isoformat(),
                location       = location_info['formatted_address'],
            )

        # Build response
        response = {
            'chart_id':       chart_id,
            'chart_name':     chart_name,
            'recalculated':   recalc,
            'datetime_utc':   dt_utc.isoformat(),
            'datetime_local': dt_local.isoformat(),
            'location':       {k: v for k, v in location_info.items() if k != 'id'},
            'house_cusps':    result.get('house_cusps'),
        }

        response['planetary_positions'] = result.get('planetary_positions')

        meta = output_cfg.get('meta', {})
        if meta.get('from_cache', True):
            response['from_cache'] = False
        if meta.get('api_usage', True):
            response['api_usage'] = usage_tracker.get_usage_stats()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Calculation error: {str(e)}", exc_info=True)
        return _error(f'Calculation failed: {str(e)}', 500)


@api.route('/chart/<chart_id>', methods=['GET'])
def get_chart(chart_id):
    """Get a chart by its ID"""
    try:
        chart_data = db_manager.get_chart_by_id(chart_id)
        if not chart_data:
            return _error('Chart not found', 404)

        stored = chart_data['chart_data']

        return jsonify({
            'chart_id':            chart_data['id'],
            'chart_name':          chart_data.get('chart_name', 'Untitled Chart'),
            'datetime_utc':        chart_data['datetime_utc'],
            'datetime_local':      chart_data['datetime_local'],
            'location':            chart_data['location'],
            'planetary_positions': stored.get('planetary_positions'),
            'house_cusps':         stored.get('house_cusps'),
            'access_count':        chart_data['access_count'],
            'from_cache':          True
        })

    except Exception as e:
        logger.error(f"Chart retrieval error: {str(e)}", exc_info=True)
        return _error(f'Chart retrieval failed: {str(e)}', 500)


@api.route('/cache/stats', methods=['GET'])
def cache_stats():
    """Get cache statistics"""
    try:
        stats = db_manager.get_cache_stats()
        return jsonify({
            'cache_statistics': stats,
            'api_usage': usage_tracker.get_usage_stats()
        })
    except Exception as e:
        logger.error(f"Cache stats error: {str(e)}", exc_info=True)
        return _error(f'Cache stats failed: {str(e)}', 500)


@api.route('/cache/cleanup', methods=['POST'])
def cache_cleanup():
    """Cleanup old cache entries (admin endpoint)"""
    try:
        days = request.json.get('days', 90) if request.json else 90
        deleted_count = db_manager.cleanup_old_cache(days)
        return jsonify({
            'message':         'Cache cleanup completed',
            'entries_deleted': deleted_count,
            'days_threshold':  days
        })
    except Exception as e:
        logger.error(f"Cache cleanup error: {str(e)}", exc_info=True)
        return _error(f'Cache cleanup failed: {str(e)}', 500)


@api.route('/setup/status', methods=['GET'])
def setup_status():
    """
    Return whether the system needs initial setup.

    Public endpoint — safe to call before any keys exist.
    Response: { "setup_required": true|false }
    """
    return jsonify({'setup_required': db_manager.is_database_empty()})


@api.route('/setup', methods=['POST'])
@validate_request(SetupSchema)
def setup(validated_data):
    """
    First-run setup — create the initial administrator account.

    Only works when the database contains no API keys at all. Once any
    key exists this endpoint returns 403, permanently.

    Body: { "name": "...", "email": "...", "password": "..." }

    On success, the account is immediately active (no email verification
    required) with admin=True and must_change_password=False. The
    decrypted API key is returned once in the response so the portal
    can store it in the session — the user does not need to see or
    record it.
    """
    from werkzeug.security import generate_password_hash
    from key_crypto import KeyCrypto
    from config import Config

    if not db_manager.is_database_empty():
        return _error('Setup has already been completed', 403)

    name     = validated_data['name'].strip()
    email    = validated_data['email'].strip().lower()
    password = validated_data['password']

    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    key_id = db_manager.create_api_key(
        name=name,
        identifier=email,
        key_enc=key_enc,
        key_prefix=prefix,
        admin=True,
        active=True,
    )

    db_manager.update_api_key(
        key_id,
        password_hash=generate_password_hash(password),
        must_change_password=0,
    )

    logger.info(f"Setup: initial admin account created for '{email}' (key_id={key_id})")

    return jsonify({
        'message':    'Setup complete. Administrator account created.',
        'id':         key_id,
        'name':       name,
        'identifier': email,
        'admin':      True,
        'api_key':    plaintext,
    }), 201


@api.route('/ping', methods=['GET'])
def ping():
    """Public availability check — no auth required."""
    return jsonify({'status': 'ok'})


@api.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    try:
        cache_stats_data = db_manager.get_cache_stats()
        from config import Config
        from users import get_all_user_ids

        return jsonify({
            'status':                   'healthy',
            'timestamp':                datetime.now().isoformat(),
            'api_usage':                usage_tracker.get_usage_stats(),
            'celestial_bodies':         list(astronomy_service.PLANETS.keys()),
            'supported_house_systems':  list(astronomy_service.HOUSE_SYSTEMS.keys()),
            'default_output_config':    OutputConfig.as_dict(),
            'registered_users':         get_all_user_ids(),
            'cache_stats':              cache_stats_data,
            'config': {
                'database_path':         Config.DATABASE_PATH,
                'cors_origins':          Config.CORS_ORIGINS,
                'rate_limiting_enabled': Config.RATE_LIMIT_ENABLED
            }
        })
    except Exception as e:
        logger.error(f"Health check error: {str(e)}", exc_info=True)
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500





@api.route('/chart/<chart_id>/progressions', methods=['POST'])
@validate_request(ProgressionSchema)
def secondary_progressions(validated_data, chart_id):
    """
    Calculate secondary progressions for an existing natal chart.

    Day-for-a-year method: progressed JD = natal JD + days elapsed.
    Calculation performed at noon UT on the progressed day.

    URL param:  chart_id         — UUID of the natal chart
    Body param: progression_date — date to progress to (YYYY-MM-DD or ISO)
    Body param: location         — optional location for progressed ASC/MC
    Body param: house_system     — optional, overrides user cfg default
    Body param: output           — optional per-request output overrides
    """
    try:
        # Load natal chart
        natal_chart = db_manager.get_chart_by_id(chart_id)
        if not natal_chart:
            return _error(f'Chart {chart_id} not found', 404)

        progression_date_str = validated_data['progression_date']
        location             = validated_data.get('location')
        house_system         = validated_data.get('house_system')
        request_overrides    = validated_data.get('output') or {}

        # Merge output config
        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        if house_system is None:
            house_system = output_cfg.get('default_house_system')

        # Parse progression date
        prog_date = _parse_datetime(progression_date_str)
        if prog_date is None:
            return _error('Invalid progression_date format', 400)

        # Parse natal datetime
        # Strip timezone info — astronomy.py works with naive UTC datetimes
        natal_dt_utc = datetime.fromisoformat(natal_chart['datetime_utc']).replace(tzinfo=None)

        # Geocode location for progressed ASC/MC if provided
        obs_lat = obs_lon = prog_location_info = None
        if location:
            prog_location_info, error = geocoding_service.geocode_location(location)
            if error:
                return _error(error, 400)
            obs_lat = prog_location_info['latitude']
            obs_lon = prog_location_info['longitude']
        elif natal_chart.get('location'):
            # Fall back to natal location
            obs_lat = natal_chart['location']['latitude']
            obs_lon = natal_chart['location']['longitude']

        logger.info(
            f"Secondary progressions: chart={chart_id}, "
            f"natal={natal_dt_utc.date()}, target={prog_date.date()}"
        )

        result, error = astronomy_service.calculate_secondary_progressions(
            natal_dt_utc,
            prog_date,
            obs_lat,
            obs_lon,
            house_system=house_system,
            output_config=output_cfg
        )

        if error:
            return _error(error, 500)

        # Filter positions to natal bodies only
        natal_data      = natal_chart['chart_data']
        positions       = result.get('planetary_positions', {})
        natal_positions = natal_data.get('planetary_positions', {})
        if positions.get('geocentric'):
            positions['geocentric'] = _filter_to_natal_bodies(
                positions['geocentric'], natal_positions.get('geocentric', {})
            )
        if positions.get('heliocentric'):
            positions['heliocentric'] = _filter_to_natal_bodies(
                positions['heliocentric'], natal_positions.get('heliocentric', {})
            )

        # Auto-name and save as derived chart
        chart_name = (
            f"{natal_chart.get('chart_name', 'Chart')} — "
            f"Progressions {prog_date.date().isoformat()}"
        )
        derived_id = db_manager.save_derived_chart(
            chart_id=chart_id,
            chart_type='secondary_progression',
            reference_date=prog_date.date().isoformat(),
            chart_data=result,
            chart_name=chart_name,
        )

        response = {
            'derived_chart_id':   derived_id,
            'chart_id':           chart_id,
            'chart_name':         chart_name,
            'chart_type':         'secondary_progression',
            'natal_datetime_utc': natal_chart['datetime_utc'],
            'progression_date':   prog_date.date().isoformat(),
            'days_elapsed':       result['days_elapsed'],
            'progressed_jd':      result['progressed_jd'],
            'method':             'secondary_progressions',
            'house_cusps':        result.get('house_cusps'),
        }

        response['planetary_positions'] = result.get('planetary_positions')
        if prog_location_info:
            response['location'] = {k: v for k, v in prog_location_info.items() if k != 'id'}

        return jsonify(response)

    except Exception as e:
        logger.error(f"Secondary progressions error: {str(e)}", exc_info=True)
        return _error(f'Secondary progressions failed: {str(e)}', 500)


@api.route('/chart/<chart_id>/solar-arc', methods=['POST'])
@validate_request(ProgressionSchema)
def solar_arc_directions(validated_data, chart_id):
    """
    Calculate solar arc directions for an existing natal chart.

    Solar arc = progressed Sun longitude - natal Sun longitude.
    All natal planets are advanced by this arc.
    Heliocentric arc uses Earth's progressed position instead of Sun.

    URL param:  chart_id         — UUID of the natal chart
    Body param: progression_date — date to direct to (YYYY-MM-DD or ISO)
    Body param: location         — optional location for directed ASC/MC
    Body param: house_system     — optional, overrides user cfg default
    Body param: output           — optional per-request output overrides
    """
    try:
        # Load natal chart
        natal_chart = db_manager.get_chart_by_id(chart_id)
        if not natal_chart:
            return _error(f'Chart {chart_id} not found', 404)

        progression_date_str = validated_data['progression_date']
        location             = validated_data.get('location')
        house_system         = validated_data.get('house_system')
        request_overrides    = validated_data.get('output') or {}

        # Merge output config
        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        if house_system is None:
            house_system = output_cfg.get('default_house_system')

        # Parse direction date
        direction_date = _parse_datetime(progression_date_str)
        if direction_date is None:
            return _error('Invalid progression_date format', 400)

        # Parse natal datetime
        # Strip timezone info — astronomy.py works with naive UTC datetimes
        natal_dt_utc = datetime.fromisoformat(natal_chart['datetime_utc']).replace(tzinfo=None)

        # Natal positions from stored chart data — pass the inner planetary_positions
        # dict so astronomy.py can access .get('geocentric') / .get('heliocentric') directly
        natal_positions = natal_chart['chart_data'].get('planetary_positions', {})

        # Geocode location for directed ASC/MC if provided
        obs_lat = obs_lon = dir_location_info = None
        if location:
            dir_location_info, error = geocoding_service.geocode_location(location)
            if error:
                return _error(error, 400)
            obs_lat = dir_location_info['latitude']
            obs_lon = dir_location_info['longitude']
        elif natal_chart.get('location'):
            obs_lat = natal_chart['location']['latitude']
            obs_lon = natal_chart['location']['longitude']

        logger.info(
            f"Solar arc directions: chart={chart_id}, "
            f"natal={natal_dt_utc.date()}, target={direction_date.date()}"
        )

        result, error = astronomy_service.calculate_solar_arc_directions(
            natal_positions,
            natal_dt_utc,
            direction_date,
            obs_lat,
            obs_lon,
            house_system=house_system,
            output_config=output_cfg
        )

        if error:
            return _error(error, 500)

        # Filter positions to natal bodies only
        natal_data      = natal_chart['chart_data']
        positions       = result.get('planetary_positions', {})
        natal_positions = natal_data.get('planetary_positions', {})
        if positions.get('geocentric'):
            positions['geocentric'] = _filter_to_natal_bodies(
                positions['geocentric'], natal_positions.get('geocentric', {})
            )
        if positions.get('heliocentric'):
            positions['heliocentric'] = _filter_to_natal_bodies(
                positions['heliocentric'], natal_positions.get('heliocentric', {})
            )

        # Auto-name and save as derived chart
        chart_name = (
            f"{natal_chart.get('chart_name', 'Chart')} — "
            f"Solar Arc {direction_date.date().isoformat()}"
        )
        derived_id = db_manager.save_derived_chart(
            chart_id=chart_id,
            chart_type='solar_arc',
            reference_date=direction_date.date().isoformat(),
            chart_data=result,
            chart_name=chart_name,
        )

        response = {
            'derived_chart_id':   derived_id,
            'chart_id':           chart_id,
            'chart_name':         chart_name,
            'chart_type':         'solar_arc',
            'natal_datetime_utc': natal_chart['datetime_utc'],
            'direction_date':     direction_date.date().isoformat(),
            'days_elapsed':       result['days_elapsed'],
            'solar_arc_geo':      result.get('solar_arc_geo'),
            'solar_arc_helio':    result.get('solar_arc_helio'),
            'method':             'solar_arc_directions',
            'house_cusps':        result.get('house_cusps'),
        }

        response['planetary_positions'] = result.get('planetary_positions')
        if dir_location_info:
            response['location'] = {k: v for k, v in dir_location_info.items() if k != 'id'}

        return jsonify(response)

    except Exception as e:
        logger.error(f"Solar arc directions error: {str(e)}", exc_info=True)
        return _error(f'Solar arc directions failed: {str(e)}', 500)


@api.route('/chart/<chart_id>/solar-return', methods=['POST'])
@validate_request(SolarReturnSchema)
def solar_return(validated_data, chart_id):
    """
    Calculate and save a solar return chart for an existing natal chart.

    Finds the exact moment the Sun returns to its natal longitude in
    the given year. The return chart is cast for that moment at the
    supplied location (defaults to natal location if not provided).

    URL param:  chart_id     — UUID of the natal radix chart
    Body param: return_year  — year of the solar return
    Body param: location     — optional current residence location
    Body param: house_system — optional house system override
    Body param: output       — optional per-request output overrides
    """
    try:
        natal_chart = db_manager.get_chart_by_id(chart_id)
        if not natal_chart:
            return _error(f'Chart {chart_id} not found', 404)

        return_year      = validated_data['return_year']
        location         = validated_data.get('location')
        house_system     = validated_data.get('house_system')
        request_overrides = validated_data.get('output') or {}

        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        if house_system is None:
            house_system = output_cfg.get('default_house_system')

        natal_dt_utc = datetime.fromisoformat(natal_chart['datetime_utc']).replace(tzinfo=None)

        # Resolve location
        obs_lat = obs_lon = location_info = None
        if location:
            location_info, error = geocoding_service.geocode_location(location)
            if error:
                return _error(error, 400)
            obs_lat = location_info['latitude']
            obs_lon = location_info['longitude']
        elif natal_chart.get('location'):
            obs_lat = natal_chart['location']['latitude']
            obs_lon = natal_chart['location']['longitude']

        logger.info(f"Solar return: chart={chart_id}, year={return_year}")

        result, error = astronomy_service.calculate_solar_return(
            natal_dt_utc,
            return_year,
            obs_lat,
            obs_lon,
            house_system=house_system,
            output_config=output_cfg
        )
        if error:
            return _error(error, 500)

        # Auto-name
        chart_name = f"{natal_chart.get('chart_name', 'Chart')} — Solar Return {return_year}"

        # Save as derived chart
        derived_id = db_manager.save_derived_chart(
            chart_id=chart_id,
            chart_type='solar_return',
            reference_date=result['return_datetime_utc'][:10],
            chart_data=result,
            chart_name=chart_name,
        )

        response = {
            'derived_chart_id':   derived_id,
            'chart_id':           chart_id,
            'chart_name':         chart_name,
            'chart_type':         'solar_return',
            'natal_datetime_utc': natal_chart['datetime_utc'],
            'return_year':        return_year,
            'return_datetime_utc': result['return_datetime_utc'],
            'natal_sun_longitude': result['natal_sun_longitude'],
            'house_cusps':        result.get('house_cusps'),
        }

        response['planetary_positions'] = result.get('planetary_positions')
        if location_info:
            response['location'] = {k: v for k, v in location_info.items() if k != 'id'}

        return jsonify(response)

    except Exception as e:
        logger.error(f"Solar return error: {str(e)}", exc_info=True)
        return _error(f'Solar return failed: {str(e)}', 500)


@api.route('/chart/<chart_id>/lunar-return', methods=['POST'])
@validate_request(LunarReturnSchema)
def lunar_return(validated_data, chart_id):
    """
    Calculate and save a lunar return chart for an existing natal chart.

    Finds the exact moment the Moon returns to its natal longitude in
    the given month. The return chart is cast for that moment at the
    supplied location.

    URL param:  chart_id      — UUID of the natal radix chart
    Body param: return_year   — year of the lunar return
    Body param: return_month  — month of the lunar return (1-12)
    Body param: location      — optional current residence location
    Body param: house_system  — optional house system override
    Body param: output        — optional per-request output overrides
    """
    try:
        natal_chart = db_manager.get_chart_by_id(chart_id)
        if not natal_chart:
            return _error(f'Chart {chart_id} not found', 404)

        return_year      = validated_data['return_year']
        return_month     = validated_data['return_month']
        location         = validated_data.get('location')
        house_system     = validated_data.get('house_system')
        request_overrides = validated_data.get('output') or {}

        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        if house_system is None:
            house_system = output_cfg.get('default_house_system')

        natal_dt_utc = datetime.fromisoformat(natal_chart['datetime_utc']).replace(tzinfo=None)

        # Resolve location
        obs_lat = obs_lon = location_info = None
        if location:
            location_info, error = geocoding_service.geocode_location(location)
            if error:
                return _error(error, 400)
            obs_lat = location_info['latitude']
            obs_lon = location_info['longitude']
        elif natal_chart.get('location'):
            obs_lat = natal_chart['location']['latitude']
            obs_lon = natal_chart['location']['longitude']

        logger.info(
            f"Lunar return: chart={chart_id}, "
            f"year={return_year}, month={return_month}"
        )

        result, error = astronomy_service.calculate_lunar_return(
            natal_dt_utc,
            return_year,
            return_month,
            obs_lat,
            obs_lon,
            house_system=house_system,
            output_config=output_cfg
        )
        if error:
            return _error(error, 500)

        import calendar
        month_name = calendar.month_abbr[return_month]
        chart_name = (
            f"{natal_chart.get('chart_name', 'Chart')} — "
            f"Lunar Return {month_name} {return_year}"
        )

        # Save as derived chart
        derived_id = db_manager.save_derived_chart(
            chart_id=chart_id,
            chart_type='lunar_return',
            reference_date=result['return_datetime_utc'][:10],
            chart_data=result,
            chart_name=chart_name,
        )

        response = {
            'derived_chart_id':    derived_id,
            'chart_id':            chart_id,
            'chart_name':          chart_name,
            'chart_type':          'lunar_return',
            'natal_datetime_utc':  natal_chart['datetime_utc'],
            'return_year':         return_year,
            'return_month':        return_month,
            'return_datetime_utc': result['return_datetime_utc'],
            'natal_moon_longitude': result['natal_moon_longitude'],
            'house_cusps':         result.get('house_cusps'),
        }

        response['planetary_positions'] = result.get('planetary_positions')
        if location_info:
            response['location'] = {k: v for k, v in location_info.items() if k != 'id'}

        return jsonify(response)

    except Exception as e:
        logger.error(f"Lunar return error: {str(e)}", exc_info=True)
        return _error(f'Lunar return failed: {str(e)}', 500)


@api.route('/chart/<chart_id>/derived', methods=['GET'])
def get_derived_charts(chart_id):
    """
    List all derived charts for a given radix chart.
    Optional query param: type — filter by chart_type
    e.g. GET /chart/<id>/derived?type=solar_return
    """
    try:
        natal_chart = db_manager.get_chart_by_id(chart_id)
        if not natal_chart:
            return _error(f'Chart {chart_id} not found', 404)

        chart_type = request.args.get('type')
        derived    = db_manager.get_derived_charts_for_radix(chart_id, chart_type)

        return jsonify({
            'chart_id':       chart_id,
            'chart_name':     natal_chart.get('chart_name'),
            'derived_charts': derived,
            'count':          len(derived),
        })

    except Exception as e:
        logger.error(f"Get derived charts error: {str(e)}", exc_info=True)
        return _error(f'Failed to retrieve derived charts: {str(e)}', 500)


@api.route('/derived/<derived_id>', methods=['GET'])
def get_derived_chart(derived_id):
    """Retrieve a specific derived chart by its UUID."""
    try:
        derived = db_manager.get_derived_chart_by_id(derived_id)
        if not derived:
            return _error(f'Derived chart {derived_id} not found', 404)

        chart_data = derived.pop('chart_data')
        derived['planetary_positions'] = chart_data.get('planetary_positions')
        derived['house_cusps']         = chart_data.get('house_cusps')

        # Include return-specific metadata if present
        for key in ['return_datetime_utc', 'natal_sun_longitude', 'natal_moon_longitude',
                    'solar_arc_geo', 'solar_arc_helio', 'days_elapsed', 'method',
                    'return_year', 'return_month']:
            if key in chart_data:
                derived[key] = chart_data[key]

        return jsonify(derived)

    except Exception as e:
        logger.error(f"Get derived chart error: {str(e)}", exc_info=True)
        return _error(f'Failed to retrieve derived chart: {str(e)}', 500)


@api.route('/derived/<derived_id>', methods=['DELETE'])
def delete_derived_chart(derived_id):
    """Delete a derived chart by its UUID."""
    try:
        deleted = db_manager.delete_derived_chart(derived_id)
        if not deleted:
            return _error(f'Derived chart {derived_id} not found', 404)
        return jsonify({'message': f'Derived chart {derived_id} deleted'})
    except Exception as e:
        logger.error(f"Delete derived chart error: {str(e)}", exc_info=True)
        return _error(f'Failed to delete derived chart: {str(e)}', 500)



@api.route('/apsides', methods=['POST'])
@validate_request(ApsideSchema)
def apsides(validated_data):
    """
    Calculate lunar and planetary apsides for a given datetime.

    Lunar apsides:
        perigee      — Moon closest approach to Earth
        apogee       — Moon furthest point from Earth
        mean_lilith  — Mean Black Moon Lilith (if enabled in output config)
        true_lilith  — True Black Moon Lilith (if enabled in output config)

    Planetary apsides (heliocentric):
        perihelion / aphelion for each active planet

    Body param: datetime — the datetime to calculate apsides for
    Body param: output   — optional per-request output overrides
    """
    try:
        datetime_str     = validated_data['datetime']
        request_overrides = validated_data.get('output') or {}

        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        dt = _parse_datetime(datetime_str)
        if dt is None:
            return _error('Invalid datetime format', 400)

        # Convert to UTC if timezone-aware
        if dt.tzinfo is not None:
            import pytz
            dt = dt.astimezone(pytz.UTC).replace(tzinfo=None)

        logger.info(f"Apsides calculation for {dt}")

        result, error = astronomy_service.calculate_apsides(dt, output_cfg)
        if error:
            return _error(error, 500)

        return jsonify({
            'datetime_utc':      result['datetime_utc'],
            'julian_day':        result['julian_day'],
            'lunar_apsides':     result['lunar_apsides'],
            'planetary_apsides': result['planetary_apsides'],
        })

    except Exception as e:
        logger.error(f"Apsides error: {str(e)}", exc_info=True)
        return _error(f'Apsides calculation failed: {str(e)}', 500)


@api.route('/lunations', methods=['POST'])
@validate_request(LunationSchema)
def lunations(validated_data):
    """
    Find lunation events — New Moon, Full Moon, First and Last Quarter.

    Two modes:

    Next/Previous mode (default):
        Provide reference_date and direction ('next', 'previous', or 'both').
        Returns the next or previous occurrence of each requested phase.

    Range mode:
        Provide start_date and end_date.
        Returns all lunation events within the date range.

    Body param: reference_date — starting point for next/previous search
    Body param: direction      — 'next' (default), 'previous', or 'both'
    Body param: start_date     — range start (activates range mode with end_date)
    Body param: end_date       — range end
    Body param: phases         — list of phases to include, default all four
    """
    try:
        reference_date_str = validated_data['reference_date']
        direction          = validated_data.get('direction', 'next')
        start_date_str     = validated_data.get('start_date')
        end_date_str       = validated_data.get('end_date')
        phases             = validated_data.get('phases') or None

        # Parse dates
        reference_date = _parse_datetime(reference_date_str)
        if reference_date is None:
            return _error('Invalid reference_date format', 400)

        start_date = end_date = None
        if start_date_str and end_date_str:
            start_date = _parse_datetime(start_date_str)
            end_date   = _parse_datetime(end_date_str)
            if start_date is None or end_date is None:
                return _error('Invalid start_date or end_date format', 400)
            if start_date > end_date:
                return _error('start_date must be before end_date', 400)
            # Cap range at 2 years to prevent runaway calculations
            from datetime import timedelta
            if (end_date - start_date).days > 730:
                return _error('Date range cannot exceed 2 years', 400)

        # Strip timezone info
        reference_date = reference_date.replace(tzinfo=None)
        if start_date:
            start_date = start_date.replace(tzinfo=None)
            end_date   = end_date.replace(tzinfo=None)

        mode = 'range' if (start_date and end_date) else direction
        logger.info(
            f"Lunations: reference={reference_date.date()}, "
            f"mode={mode}, phases={phases or 'all'}"
        )

        result, error = astronomy_service.find_lunations(
            reference_date=reference_date,
            direction=direction,
            start_date=start_date,
            end_date=end_date,
            phases=phases,
        )
        if error:
            return _error(error, 500)

        return jsonify({
            'mode':            mode,
            'reference_date':  reference_date.date().isoformat(),
            'phases_requested': phases or ['new_moon', 'first_quarter', 'full_moon', 'last_quarter'],
            'count':           len(result),
            'lunations':       result,
        })

    except Exception as e:
        logger.error(f"Lunations error: {str(e)}", exc_info=True)
        return _error(f'Lunations search failed: {str(e)}', 500)



@api.route('/apsides/next', methods=['POST'])
@validate_request(NextApsideSchema)
def next_apsides(validated_data):
    """
    Find the next perigee/perihelion and apogee/aphelion events for
    each requested body after a reference date.

    Moon perigee/apogee:     found directly via Swiss Ephemeris (~27 day cycle)
    Planetary perihelion/aphelion: found by scanning forward for distance
                                   speed sign change then Newton refinement.

    Body param: reference_date   — search from this date forward
    Body param: bodies           — list of body names (default: all supported)
    Body param: events           — list of 'perigee'/'perihelion'/'apogee'/'aphelion'
                                   (default: both)
    Body param: max_search_years — cap on search window, 1–50 (default: 20)
    """
    try:
        reference_date_str = validated_data['reference_date']
        bodies             = validated_data.get('bodies') or None
        events             = validated_data.get('events') or None
        max_search_years   = validated_data.get('max_search_years', 20)

        reference_date = _parse_datetime(reference_date_str)
        if reference_date is None:
            return _error('Invalid reference_date format', 400)

        reference_date = reference_date.replace(tzinfo=None)

        logger.info(
            f"Next apsides: reference={reference_date.date()}, "
            f"bodies={bodies or 'all'}, events={events or 'both'}, "
            f"max_years={max_search_years}"
        )

        result, error = astronomy_service.calculate_next_apsides(
            reference_date=reference_date,
            bodies=bodies,
            events=events,
            max_search_years=max_search_years,
        )
        if error:
            return _error(error, 500)

        return jsonify({
            'reference_date':  reference_date.date().isoformat(),
            'bodies_searched': bodies or list(astronomy_service.APSIDE_EVENT_BODIES.keys()),
            'events_searched': events or ['perigee', 'apogee'],
            'max_search_years': max_search_years,
            'count':           len(result),
            'events':          result,
        })

    except Exception as e:
        logger.error(f"Next apsides error: {str(e)}", exc_info=True)
        return _error(f'Next apsides search failed: {str(e)}', 500)



@api.route('/ephemeris', methods=['POST'])
@validate_request(EphemerisSchema)
def ephemeris(validated_data):
    """
    Calculate planetary positions at noon UT for every day of a given month.

    Returns geocentric and heliocentric positions for all active bodies
    as configured for the requesting user. No location is required —
    house cusps and ASC/MC are not included.

    Body param: year   — the year (1800–2200)
    Body param: month  — the month (1–12)
    Body param: output — optional per-request output overrides
    """
    try:
        year             = validated_data['year']
        month            = validated_data['month']
        request_overrides = validated_data.get('output') or {}

        user = getattr(g, 'user', {})
        output_cfg = OutputConfig.merge(user.get('output', {}))
        output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)

        import calendar
        month_name = calendar.month_name[month]

        logger.info(
            f"[{user.get('name', 'unknown')}] "
            f"Ephemeris: {month_name} {year}"
        )

        result, error = astronomy_service.calculate_monthly_ephemeris(
            year, month, output_config=output_cfg
        )
        if error:
            return _error(error, 500)

        return jsonify({
            'year':       result['year'],
            'month':      result['month'],
            'month_name': month_name,
            'days':       result['days'],
        })

    except Exception as e:
        logger.error(f"Ephemeris error: {str(e)}", exc_info=True)
        return _error(f'Ephemeris calculation failed: {str(e)}', 500)



@api.route('/eclipses', methods=['POST'])
@validate_request(EclipseSchema)
def eclipses(validated_data):
    """
    Find all solar and lunar eclipses within a given time window.

    Body param: reference_date — start of the search window (YYYY-MM-DD or ISO)
    Body param: years_ahead    — how many years forward to search (1–50, default 5)

    Returns a chronological list of eclipses, each with:
      type, eclipse_type, datetime_utc, julian_day,
      magnitude, obscuration, saros_series, saros_member

    Solar eclipse types:  total, annular, hybrid, partial
    Lunar eclipse types:  total, partial, penumbral

    obscuration is the fraction of the disc covered at maximum (0.0–1.0).
    """
    reference_date_str = validated_data['reference_date']
    years_ahead        = validated_data.get('years_ahead', 5)

    reference_date = _parse_datetime(str(reference_date_str))
    if reference_date is None:
        return _error('Invalid reference_date format', 400)
    reference_date = reference_date.replace(tzinfo=None)

    result, error = astronomy_service.calculate_eclipses(
        reference_date=reference_date,
        years_ahead=years_ahead,
    )
    if error:
        return _error(error, 500)

    return jsonify({
        'reference_date': reference_date.date().isoformat(),
        'years_ahead':    years_ahead,
        'count':          len(result),
        'eclipses':       result,
    })


# ---------------------------------------------------------------------------
# Views — opaque JSON blob storage, retrieved by UUID
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Views — opaque JSON blob storage, retrieved by UUID
# ---------------------------------------------------------------------------

@api.route('/views', methods=['POST'])
@validate_request(SaveViewSchema)
def save_view(validated_data):
    """
    Save a new opaque JSON blob. Always generates a fresh UUID.
    Returns: { "view_id": "<uuid>" }

    Body: { "data": { ...any JSON... } }
    """
    import uuid as _uuid
    import json

    user   = getattr(g, 'user', {})
    key_id = int(user.get('id', 0))

    view_id  = str(_uuid.uuid4())
    data_str = json.dumps(validated_data['data'], separators=(',', ':'))

    try:
        db_manager.save_view(view_id, key_id, data_str)
    except Exception as e:
        logger.error(f"save_view error: {e}")
        return _error('Failed to save view', 500)

    logger.info(f"View created: {view_id} (key_id={key_id})")
    return jsonify({'view_id': view_id}), 201


@api.route('/views/<view_id>', methods=['PUT'])
@validate_request(SaveViewSchema)
def update_view(validated_data, view_id):
    """
    Update an existing view blob in place.
    Returns 404 if the view_id does not exist.

    Body: { "data": { ...any JSON... } }
    """
    import json

    user   = getattr(g, 'user', {})
    key_id = int(user.get('id', 0))

    existing = db_manager.get_view(view_id)
    if existing is None:
        return _error(f"View '{view_id}' not found", 404)

    data_str = json.dumps(validated_data['data'], separators=(',', ':'))

    try:
        db_manager.save_view(view_id, key_id, data_str)
    except Exception as e:
        logger.error(f"update_view error: {e}")
        return _error('Failed to update view', 500)

    logger.info(f"View updated: {view_id} (key_id={key_id})")
    return jsonify({'view_id': view_id})


@api.route('/views', methods=['GET'])
def get_view():
    """
    Retrieve a saved view by UUID. No authentication required.
    Query string: ?v=<uuid>
    """
    import json

    view_id = request.args.get('v', '').strip()
    if not view_id:
        return _error('Query parameter v (view UUID) is required', 400)

    record = db_manager.get_view(view_id)
    if record is None:
        return _error(f"View '{view_id}' not found", 404)

    try:
        data = json.loads(record['data'])
    except Exception:
        return _error('Stored view data is malformed', 500)

    return jsonify({
        'view_id':    record['view_id'],
        'data':       data,
        'created_at': record['created_at'],
        'updated_at': record['updated_at'],
    })



# ---------------------------------------------------------------------------
# Chart archive and recalculation history
# ---------------------------------------------------------------------------

@api.route('/archive', methods=['GET'])
def search_archive():
    # Search the permanent chart archive.
    # Query parameters (all optional):
    #   chart_name — partial name match (case-insensitive)
    #   location   — partial location match (case-insensitive)
    #   limit      — max results (default 50, max 200)
    # Returns records ordered by first_calculated_at DESC.
    # Use chart_id from any result to fetch its full recalculation
    # history via GET /archive/<chart_id>.
    chart_name = request.args.get('chart_name', '').strip() or None
    location   = request.args.get('location',   '').strip() or None
    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    results = db_manager.search_archive(
        chart_name=chart_name,
        location=location,
        limit=limit,
    )
    return jsonify({
        'count':   len(results),
        'results': results,
    })


@api.route('/archive/<chart_id>', methods=['GET'])
def get_archive_entry(chart_id):
    # Retrieve a single archive entry and its full recalculation history.
    # Returns the original chart record plus every recalculation ever
    # performed, ordered oldest to newest.
    with db_manager.get_connection() as conn:
        row = conn.execute(
            'SELECT chart_id, chart_name, datetime_utc, datetime_local, '
            'location, first_calculated_at '
            'FROM chart_archive WHERE chart_id = ?',
            (chart_id,)
        ).fetchone()

    if not row:
        return _error(f"Archive entry '{chart_id}' not found", 404)

    recalculations = db_manager.get_recalculations(chart_id)

    return jsonify({
        'chart_id':            row['chart_id'],
        'chart_name':          row['chart_name'],
        'datetime_utc':        row['datetime_utc'],
        'datetime_local':      row['datetime_local'],
        'location':            row['location'],
        'first_calculated_at': row['first_calculated_at'],
        'recalculation_count': len(recalculations),
        'recalculations':      recalculations,
    })



# ---------------------------------------------------------------------------
# Registration — self-serve, email verification
# ---------------------------------------------------------------------------

@api.route('/register', methods=['POST'])
@validate_request(RegisterSchema)
def register(validated_data):
    """
    Register for an account.

    Accepts a name and email address. Sends a verification email containing
    a link the user must click to confirm their address. Once verified, the
    user is emailed a link to set a password — the account becomes usable
    once that's done. The underlying API key is generated immediately but
    is never shown to the user; it is decrypted transparently on login.

    Body: { "name": "...", "email": "..." }
    """
    import secrets as _secrets

    email = validated_data['email'].strip().lower()
    name  = validated_data['name'].strip()

    # Don't reveal whether the email is already registered
    all_keys = db_manager.get_all_api_keys(include_inactive=True)
    if any(k['identifier'] == email for k in all_keys):
        return jsonify({
            'message': 'If this email is not already registered, a verification email has been sent.'
        })

    token = _secrets.token_urlsafe(32)

    from key_crypto import KeyCrypto
    from config import Config
    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    key_id = db_manager.create_api_key(
        name=name,
        identifier=email,
        key_enc=key_enc,
        key_prefix=prefix,
        admin=False,
        active=False,
    )

    db_manager.create_email_verification(
        api_key_id=key_id,
        email=email,
        token=token,
    )

    email_svc = EmailService()
    email_svc.send_registration_verification(
        email, name, token,
        template=_resolve_template('registration-verification')
    )

    logger.info(f"Registration: '{email}' (key_id={key_id}) — verification sent")

    return jsonify({
        'message': 'Verification email sent. Click the link in the email to verify your address.',
        'email':   email,
    }), 201


@api.route('/register/verify', methods=['GET'])
def verify_email():
    """
    Verify an email address from the link in the registration email.

    Query param: t — the verification token

    Activates the account and sends a follow-up email containing a link
    to the "set your password" page. The API key itself is never emailed —
    it remains encrypted server-side until the user authenticates via the
    portal (email + password + 2FA).
    """
    import secrets as _secrets

    token = request.args.get('t', '').strip()
    if not token:
        return _error('Verification token is required', 400)

    record = db_manager.get_email_verification(token)
    if not record:
        return _error('Invalid, expired, or already used verification link', 400)

    key_id = record['api_key_id']
    email  = record['email']

    key_record = db_manager.get_api_key_by_id(key_id)
    if not key_record:
        return _error('Account not found', 500)

    # Activate the account. The plaintext key stays encrypted in key_enc —
    # it never needs to be shown to the user, since login decrypts it
    # transparently once a password has been set.
    db_manager.update_api_key(key_id, active=1)
    db_manager.mark_email_verification_used(token)

    logger.info(f"Email verified: '{email}' (key_id={key_id}) — account activated")

    # Issue a set-password token (reuses the email_verifications table)
    set_password_token = _secrets.token_urlsafe(32)
    db_manager.create_email_verification(
        api_key_id=key_id,
        email=email,
        token=set_password_token,
    )

    email_svc = EmailService()
    email_svc.send_set_password(
        email, key_record.get('name', ''), set_password_token,
        template=_resolve_template('set-password')
    )
    logger.info(f"Set-password link emailed to '{email}' (key_id={key_id})")

    return jsonify({
        'message':    'Email verified. Check your email for a link to set your password.',
        'email':      email,
        'key_active': True,
    })


# ---------------------------------------------------------------------------
# Login — email + password, 2FA, trusted devices
# ---------------------------------------------------------------------------

def _user_identity_response(key_record, plaintext_key):
    """Build the identity + decrypted API key payload returned on full login."""
    class_limits = db_manager.get_key_class_limits('user')
    rate_limits = {
        'per_minute': key_record.get('rate_per_minute') or class_limits['rate_per_minute'],
        'per_hour':   key_record.get('rate_per_hour')   or class_limits['rate_per_hour'],
        'per_day':    key_record.get('rate_per_day')    or class_limits['rate_per_day'],
    }
    if key_record.get('admin'):
        rate_limits = {'per_minute': None, 'per_hour': None, 'per_day': None}

    return {
        'id':          key_record['id'],
        'name':        key_record['name'],
        'identifier':  key_record['identifier'],
        'admin':       bool(key_record.get('admin')),
        'active':      bool(key_record.get('active')),
        'rate_limits': rate_limits,
        'output':      key_record.get('output_config') or {},
        'api_key':     plaintext_key,
    }


@api.route('/login', methods=['POST'])
@validate_request(LoginSchema)
def login(validated_data):
    """
    Log in with email and password.

    Body: { "email": "...", "password": "...", "device_token": "..." (optional) }

    Responses:
      - { "must_change_password": true, "email": "..." }
        — the account has no password set yet, or an admin has required a
          reset. The client should direct the user to /password/set.

      - { "2fa_required": true, "email": "..." }
        — credentials are correct but no valid trusted-device token was
          supplied. A verification code has been emailed; call /login/2fa
          to complete the login.

      - { identity fields..., "api_key": "..." }
        — login complete (credentials correct AND a valid device_token was
          supplied). The decrypted API key is included for the portal
          session; it is not stored anywhere new.
    """
    email    = validated_data['email'].strip().lower()
    password = validated_data['password']
    device_token = validated_data.get('device_token')

    # Generic error to avoid revealing whether an email is registered
    invalid = lambda: _error('Invalid email or password', 401)

    key_record = db_manager.get_api_key_by_identifier(email)
    if not key_record or not key_record.get('active'):
        return invalid()

    if not key_record.get('password_hash'):
        # No password set yet — this account is awaiting /password/set
        # (either fresh registration, or admin-forced reset already
        # cleared the old hash).
        return invalid()

    from werkzeug.security import check_password_hash
    if not check_password_hash(key_record['password_hash'], password):
        return invalid()

    key_id = key_record['id']

    if key_record.get('must_change_password'):
        return jsonify({
            'must_change_password': True,
            'email': email,
        })

    from key_crypto import KeyCrypto
    from config import Config
    crypto = KeyCrypto(Config.SECRET_KEY)
    plaintext_key = crypto.decrypt(key_record['key_enc'])

    # Check trusted-device token
    if device_token:
        device = db_manager.get_trusted_device(device_token)
        if device and device['api_key_id'] == key_id:
            logger.info(f"Login: '{email}' (key_id={key_id}) — trusted device, 2FA skipped")
            return jsonify(_user_identity_response(key_record, plaintext_key))

    # Skip 2FA for admins when SMTP is not configured — avoids a catch-22
    # where the admin cannot log in to set up SMTP because 2FA needs SMTP.
    if key_record.get('admin'):
        smtp_cfg = db_manager.get_smtp_config()
        if not smtp_cfg.get('host'):
            logger.info(
                f"Login: '{email}' (key_id={key_id}) — admin, SMTP not configured, 2FA skipped"
            )
            return jsonify(_user_identity_response(key_record, plaintext_key))

    # No valid trusted device — send 2FA code
    import secrets as _secrets
    code = f"{_secrets.randbelow(1000000):06d}"

    db_manager.invalidate_2fa_codes(key_id)
    db_manager.create_2fa_code(key_id, code, expiry_minutes=Config.TWO_FACTOR_CODE_EXPIRY_MINUTES)

    email_svc = EmailService()
    email_svc.send_2fa_code(
        email, key_record.get('name', ''), code,
        expiry_minutes=Config.TWO_FACTOR_CODE_EXPIRY_MINUTES,
        template=_resolve_template('2fa-code')
    )
    logger.info(f"Login: '{email}' (key_id={key_id}) — 2FA code sent")

    return jsonify({
        '2fa_required': True,
        'email': email,
    })


@api.route('/login/2fa', methods=['POST'])
@validate_request(Login2FASchema)
def login_2fa(validated_data):
    """
    Complete login by verifying a 2FA code.

    Body: { "email": "...", "code": "...", "remember_device": true|false }

    On success, returns identity fields plus the decrypted API key. If
    remember_device is true, also returns a device_token to be stored as a
    long-lived cookie (TRUSTED_DEVICE_DAYS, default 28) — supplying this
    token on a future /login call will skip the 2FA step.
    """
    email = validated_data['email'].strip().lower()
    code  = validated_data['code'].strip()
    remember_device = validated_data.get('remember_device', False)

    key_record = db_manager.get_api_key_by_identifier(email)
    if not key_record or not key_record.get('active'):
        return _error('Invalid email or code', 401)

    key_id = key_record['id']

    code_record = db_manager.get_valid_2fa_code(key_id, code)
    if not code_record:
        return _error('Invalid or expired verification code', 401)

    db_manager.mark_2fa_code_used(code_record['id'])

    from key_crypto import KeyCrypto
    from config import Config
    crypto = KeyCrypto(Config.SECRET_KEY)
    plaintext_key = crypto.decrypt(key_record['key_enc'])

    response = _user_identity_response(key_record, plaintext_key)

    if remember_device:
        import secrets as _secrets
        device_token = _secrets.token_urlsafe(32)
        db_manager.create_trusted_device(key_id, device_token, expiry_days=Config.TRUSTED_DEVICE_DAYS)
        response['device_token'] = device_token
        response['device_token_expires_days'] = Config.TRUSTED_DEVICE_DAYS

    logger.info(f"Login: '{email}' (key_id={key_id}) — 2FA verified")
    return jsonify(response)


@api.route('/password/forgot', methods=['POST'])
def password_forgot():
    """
    Request a password reset email.

    Body: { "email": "..." }

    Always returns the same success response regardless of whether the
    email is registered — prevents account enumeration.

    If the email is found and active, a reset token is generated and the
    password-reset-required email is sent containing a link to
    /set-password.php?t=TOKEN on the portal.
    """
    import secrets as _secrets

    data  = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()

    # Validate email format minimally
    if not email or '@' not in email:
        return _error('A valid email address is required', 400)

    _generic_response = jsonify({
        'message': 'If that email address is registered, a password reset link has been sent.'
    })

    key_record = db_manager.get_api_key_by_identifier(email)
    if not key_record or not key_record.get('active'):
        return _generic_response

    key_id = key_record['id']

    token = _secrets.token_urlsafe(32)
    db_manager.create_email_verification(
        api_key_id=key_id,
        email=email,
        token=token,
    )

    try:
        email_svc = EmailService()
        email_svc.send_password_reset_required(
            email,
            key_record.get('name', ''),
            token,
            template=_resolve_template('password-reset-required'),
        )
        logger.info(f"Password reset requested for '{email}' (key_id={key_id})")
    except Exception as e:
        logger.error(f"Failed to send password reset email to '{email}': {e}")

    return _generic_response


@api.route('/password/set', methods=['POST'])
@validate_request(SetPasswordSchema)
def set_password(validated_data):
    """
    Set or change a password.

    Either:
      - token        — from a set-password / password-reset email
                       (used for first-time setup or admin-forced resets)
      - email + current_password — normal in-portal password change

    Body always includes new_password (min 8 characters).

    On success, clears must_change_password and invalidates any trusted
    devices for the account (a password change should require fresh 2FA).
    """
    from werkzeug.security import generate_password_hash, check_password_hash

    token            = validated_data.get('token')
    email            = validated_data.get('email')
    current_password = validated_data.get('current_password')
    new_password     = validated_data['new_password']

    key_record = None

    if token:
        record = db_manager.get_email_verification(token)
        if not record:
            return _error('Invalid or expired token', 400)
        key_record = db_manager.get_api_key_by_id(record['api_key_id'])
        if not key_record:
            return _error('Key record not found', 500)
        db_manager.mark_email_verification_used(token)

    elif email and current_password:
        email = email.strip().lower()
        key_record = db_manager.get_api_key_by_identifier(email)
        if not key_record or not key_record.get('active'):
            return _error('Invalid email or password', 401)
        if not key_record.get('password_hash') or not check_password_hash(key_record['password_hash'], current_password):
            return _error('Invalid email or password', 401)

    else:
        return _error('Either token, or email and current_password, are required', 400)

    key_id          = key_record['id']
    is_new_account  = token is not None  # token path = first-time setup

    new_hash = generate_password_hash(new_password)
    db_manager.update_api_key(key_id, password_hash=new_hash, must_change_password=0)
    db_manager.delete_trusted_devices_for_key(key_id)

    logger.info(f"Password set for key_id={key_id} ('{key_record.get('identifier')}')")

    # For first-time account setup (token path), send the Account Activated email
    # containing the decrypted API key. This is the one and only time the key is
    # delivered to the user — after this it lives encrypted in the database and is
    # decrypted transparently on login.
    if is_new_account:
        try:
            from key_crypto import KeyCrypto
            from config import Config
            crypto    = KeyCrypto(Config.SECRET_KEY)
            plaintext = crypto.decrypt(key_record['key_enc'])

            email_svc = EmailService()
            email_svc.send_user_key_activated(
                key_record['identifier'],
                key_record.get('name', ''),
                plaintext,
                template=_resolve_template('user-activated'),
            )
            logger.info(f"Account activated email sent to '{key_record['identifier']}' (key_id={key_id})")
        except Exception as e:
            logger.error(f"Failed to send account activated email (key_id={key_id}): {e}")

    return jsonify({
        'message': 'Password set successfully. Please log in.',
    })


# ---------------------------------------------------------------------------
# Admin — Email templates
# ---------------------------------------------------------------------------

_TEMPLATE_CONTENT_DEFAULTS = {
    'test': {
        'subject':     'Test email from ephemeralREST',
        'header_text': 'Test Email',
        'body_text':   'This is a test email from ephemeralREST.\n\nYour SMTP configuration is working correctly.',
        'footer_text': 'ephemeralREST',
    },
    'registration-verification': {
        'subject':     'Verify your email address',
        'header_text': 'Verify Your Email',
        'body_text':   'Hi {name},\n\nThank you for registering. Click the link below to verify your email address:\n\n{verify_url}\n\nThis link expires in 24 hours.\n\nIf you did not request this, you can safely ignore this email.',
        'footer_text': 'ephemeralREST',
    },
    'key-rotated': {
        'subject':     'Your API key has been rotated',
        'header_text': 'API Key Rotated',
        'body_text':   'Hi {name},\n\nYour API key for {identifier} has been rotated.\n\nNew key:\n\n{api_key}\n\nSave this key — it will not be shown again.',
        'footer_text': 'ephemeralREST',
    },
    'user-activated': {
        'subject':     'Your API key is ready',
        'header_text': 'API Key Activated',
        'body_text':   'Hi {name},\n\nYour email has been verified and your API key is now active.\n\nYour API key:\n\n{api_key}\n\nSave this key — it will not be shown again.',
        'footer_text': 'ephemeralREST',
    },
    'set-password': {
        'subject':     'Set your password — ephemeralREST',
        'header_text': 'Set Your Password',
        'body_text':   'Hi {name},\n\nYour email has been verified. Click the link below to set a password for your account:\n\n{set_password_url}\n\nThis link expires in 24 hours.',
        'footer_text': 'ephemeralREST',
    },
    'password-reset-required': {
        'subject':     'Please reset your password — ephemeralREST',
        'header_text': 'Password Reset Required',
        'body_text':   'Hi {name},\n\nAn administrator has requested that you set a new password for your account.\n\nClick the link below to set a new password:\n\n{set_password_url}\n\nThis link expires in 24 hours.',
        'footer_text': 'ephemeralREST',
    },
    '2fa-code': {
        'subject':     'Your login verification code',
        'header_text': 'Verification Code',
        'body_text':   'Hi {name},\n\nYour verification code is:\n\n{code}\n\nThis code expires in {expiry_minutes} minutes. If you did not attempt to log in, you can ignore this email.',
        'footer_text': 'ephemeralREST',
    },
}

_TEMPLATE_APPEARANCE_DEFAULTS = {
    'bg_color':      '#f4f4f4',
    'panel_color':   '#ffffff',
    'text_color':    '#1a1a1a',
    'content_width': 600,
    'header_align':  'left',
}


def _resolve_template(name: str) -> dict:
    """Merge DB overrides onto hardcoded defaults for a named template."""
    defaults = {**_TEMPLATE_APPEARANCE_DEFAULTS, **_TEMPLATE_CONTENT_DEFAULTS.get(name, {})}
    stored   = db_manager.get_email_template(name)
    if stored:
        for k, v in stored.items():
            if k not in ('id', 'name', 'updated_at') and v is not None:
                defaults[k] = v
    return defaults


@api.route('/admin/email-templates/<name>', methods=['GET'])
def admin_get_email_template(name):
    """Return the resolved template (DB overrides merged onto defaults)."""
    if name not in _TEMPLATE_CONTENT_DEFAULTS:
        return _error(f"Unknown template '{name}'", 404)
    return jsonify(_resolve_template(name))


@api.route('/admin/email-templates/<name>', methods=['POST'])
def admin_set_email_template(name):
    """Save appearance and content overrides for a named template."""
    if name not in _TEMPLATE_CONTENT_DEFAULTS:
        return _error(f"Unknown template '{name}'", 404)
    data = request.get_json(silent=True) or {}
    ok   = db_manager.set_email_template(name, data)
    if not ok:
        return _error('No valid fields provided', 400)
    return jsonify({'message': f"Template '{name}' saved", 'template': _resolve_template(name)})


@api.route('/admin/email-templates/<name>/reset', methods=['POST'])
def admin_reset_email_template(name):
    """Delete DB overrides for a named template, reverting to code defaults."""
    if name not in _TEMPLATE_CONTENT_DEFAULTS:
        return _error(f"Unknown template '{name}'", 404)
    db_manager.reset_email_template(name)
    return jsonify({'message': f"Template '{name}' reset to defaults", 'template': _resolve_template(name)})


# ---------------------------------------------------------------------------
# Admin — Key admin promotion
# ---------------------------------------------------------------------------

@api.route('/admin/keys/<int:key_id>/set-admin', methods=['POST'])
def admin_set_key_admin(key_id):
    """Grant or revoke admin status on a key."""
    user = getattr(g, 'user', None)
    if not user or not user.get('admin'):
        return _error('Admin access required', 403)

    data  = request.get_json(silent=True) or {}
    admin = bool(data.get('admin', False))

    if key_id == int(user.get('id', 0)):
        return _error('You cannot modify your own admin status', 400)

    if not admin and db_manager.count_admin_keys() <= 1:
        return _error('Cannot remove the last admin key', 400)

    key_record = db_manager.get_api_key_by_id(key_id)
    if not key_record:
        return _error('Key not found', 404)

    db_manager.set_key_admin(key_id, admin)
    logger.info(
        f"Admin [{user.get('identifier')}] {'granted' if admin else 'revoked'} "
        f"admin on key_id={key_id}"
    )
    return jsonify({'message': f"Admin {'granted' if admin else 'revoked'}", 'key_id': key_id, 'admin': admin})



@api.route('/me', methods=['GET'])
def me():
    """
    Return the identity and role of the authenticated key.
    Used by the admin portal to determine which role portal to show.

    Returns: id, name, identifier, key_type, admin, active, rate_limits
    """
    user = getattr(g, 'user', None)
    if not user:
        return _error('Unauthorized', 401)

    return jsonify({
        'id':          user.get('id'),
        'name':        user.get('name'),
        'identifier':  user.get('identifier'),
        'admin':       user.get('admin'),
        'active':      user.get('active'),
        'rate_limits': user.get('rate_limits'),
    })



# ---------------------------------------------------------------------------
# Admin — Key management endpoints
# ---------------------------------------------------------------------------

@api.route('/admin/keys', methods=['GET'])
def admin_list_keys():
    """
    List all API keys. Admin only.
    Optional query param: inactive — include disabled keys if set to '1'
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    include_inactive = request.args.get('inactive', '0') == '1'
    keys = db_manager.get_all_api_keys(include_inactive=include_inactive)

    # Strip key_enc from response — never expose ciphertext
    for k in keys:
        k.pop('key_enc', None)

    return jsonify({
        'count': len(keys),
        'keys':  keys,
    })


@api.route('/admin/keys/<int:key_id>', methods=['GET'])
def admin_get_key(key_id):
    """Get a single key record by ID. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    all_keys = db_manager.get_all_api_keys(include_inactive=True)
    record   = next((k for k in all_keys if k['id'] == key_id), None)

    if not record:
        return _error(f'Key {key_id} not found', 404)

    record.pop('key_enc', None)
    return jsonify(record)


@api.route('/admin/keys/<int:key_id>/disable', methods=['POST'])
def admin_disable_key(key_id):
    """Deactivate an API key. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    updated = db_manager.update_api_key(key_id, active=0)
    if not updated:
        return _error(f'Key {key_id} not found', 404)

    logger.info(f"Admin disabled key {key_id}")
    return jsonify({'message': f'Key {key_id} disabled'})


@api.route('/admin/keys/<int:key_id>/enable', methods=['POST'])
def admin_enable_key(key_id):
    """Reactivate a disabled API key. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    updated = db_manager.update_api_key(key_id, active=1)
    if not updated:
        return _error(f'Key {key_id} not found', 404)

    logger.info(f"Admin enabled key {key_id}")
    return jsonify({'message': f'Key {key_id} enabled'})


@api.route('/admin/keys/<int:key_id>/force-password-reset', methods=['POST'])
def admin_force_password_reset(key_id):
    """
    Require a user to set a new password before they can log in again.

    Sets must_change_password=1, clears any trusted-device tokens, and
    emails the user a link to /password/set. Admin only.
    """
    import secrets as _secrets

    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    key_record = db_manager.get_api_key_by_id(key_id)
    if not key_record:
        return _error(f'Key {key_id} not found', 404)

    db_manager.update_api_key(key_id, must_change_password=1)
    db_manager.delete_trusted_devices_for_key(key_id)

    token = _secrets.token_urlsafe(32)
    db_manager.create_email_verification(
        api_key_id=key_id,
        email=key_record['identifier'],
        token=token,
    )

    email_svc = EmailService()
    sent = email_svc.send_password_reset_required(
        key_record['identifier'], key_record.get('name', ''), token,
        template=_resolve_template('password-reset-required')
    )

    logger.info(f"Admin [{user.get('identifier')}] forced password reset for key_id={key_id}")

    return jsonify({
        'message':    f'Password reset required for key {key_id}',
        'key_id':     key_id,
        'email_sent': bool(sent),
    })


@api.route('/admin/keys/<int:key_id>/rotate', methods=['POST'])
def admin_rotate_key(key_id):
    """
    Generate a new plaintext key for an existing record. Admin only.
    Returns the new plaintext key once — it cannot be retrieved again.
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    record = db_manager.get_api_key_by_id(key_id)
    if not record:
        return _error(f'Key {key_id} not found', 404)

    from key_crypto import KeyCrypto
    from config import Config

    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    updated = db_manager.update_api_key(key_id, key_enc=key_enc, key_prefix=prefix)
    if not updated:
        return _error(f'Failed to update key {key_id}', 500)

    logger.info(f"Admin rotated key {key_id} (identifier={record['identifier']})")

    # Determine the contact email for this key
    # Domain keys: use registration contact_email; user keys: identifier is the email
    to_email = None
    to_email = record.get('identifier')  # identifier is always the email address

    if to_email:
        email_svc = EmailService()
        email_svc.send_key_rotated(to_email, record.get('name', ''), record['identifier'], plaintext, template=_resolve_template('key-rotated'))
        logger.info(f"Key rotation email sent to '{to_email}' (key_id={key_id})")

    return jsonify({
        'message':    f'Key rotated for {record["identifier"]}',
        'key_id':     key_id,
        'identifier': record['identifier'],
        'key_prefix': prefix,
        'api_key':    plaintext,
        'warning':    'Save this key — it will not be shown again',
    })


@api.route('/admin/keys/<int:key_id>/limits', methods=['POST'])
def admin_set_key_limits(key_id):
    """
    Set rate limits for a specific key. Admin only.
    Pass null for any field to revert to class default.

    Body: rate_per_minute, rate_per_hour, rate_per_day
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data = request.get_json(silent=True) or {}
    updates = {}
    for field in ('rate_per_minute', 'rate_per_hour', 'rate_per_day'):
        if field in data:
            val = data[field]
            updates[field] = int(val) if val is not None else None

    if not updates:
        return _error('No limit fields provided', 400)

    updated = db_manager.update_api_key(key_id, **updates)
    if not updated:
        return _error(f'Key {key_id} not found', 404)

    return jsonify({'message': 'Rate limits updated', 'updates': updates})


@api.route('/admin/keys/<int:key_id>/output', methods=['POST'])
def admin_set_key_output(key_id):
    """
    Set or clear the output configuration for a key. Admin only.
    Pass null for output_config to revert to server defaults.

    Body: { "output_config": { ... } | null }
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data       = request.get_json(silent=True) or {}
    output_cfg = data.get('output_config')

    updated = db_manager.update_api_key(key_id, output_config=output_cfg)
    if not updated:
        return _error(f'Key {key_id} not found', 404)

    return jsonify({'message': 'Output config updated'})


@api.route('/admin/keys/<int:key_id>', methods=['DELETE'])
def admin_delete_key(key_id):
    """Permanently delete a key record. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    deleted = db_manager.delete_api_key(key_id)
    if not deleted:
        return _error(f'Key {key_id} not found', 404)

    logger.info(f"Admin deleted key {key_id}")
    return jsonify({'message': f'Key {key_id} permanently deleted'})



@api.route('/admin/class-limits', methods=['GET'])
def admin_get_class_limits():
    """Get the default rate limits applied to all keys. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    limits = db_manager.get_key_class_limits('user')
    return jsonify(limits)


@api.route('/admin/class-limits', methods=['POST'])
def admin_set_class_limits():
    """
    Set rate limits for a key class. Admin only.
    Body: key_type, rate_per_minute, rate_per_hour, rate_per_day
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data = request.get_json(silent=True) or {}

    try:
        rpm = int(data['rate_per_minute'])
        rph = int(data['rate_per_hour'])
        rpd = int(data['rate_per_day'])
    except (KeyError, TypeError, ValueError):
        return _error('rate_per_minute, rate_per_hour, and rate_per_day are required integers', 400)

    db_manager.set_key_class_limits('user', rpm, rph, rpd)
    logger.info(f"Admin updated class limits: {rpm}/min {rph}/hr {rpd}/day")

    return jsonify({
        'message':         'Class limits updated',
        'rate_per_minute': rpm,
        'rate_per_hour':   rph,
        'rate_per_day':    rpd,
    })




@api.route('/me/output', methods=['GET'])
def me_get_output():
    """
    Return the current output configuration for the authenticated key.
    Returns the stored per-key config merged onto server defaults so the
    caller sees the full effective config, not just the overrides.
    """
    user = getattr(g, 'user', None)
    if not user:
        return _error('Unauthorized', 401)

    key_id = int(user.get('id', 0))
    record = db_manager.get_api_key_by_id(key_id)
    if not record:
        return _error('Key record not found', 404)

    stored   = record.get('output_config') or {}
    effective = OutputConfig.merge(stored)

    return jsonify({
        'key_id':     key_id,
        'identifier': user.get('identifier'),
        'stored':     stored,       # only the overrides saved against this key
        'effective':  effective,    # full resolved config (defaults + overrides)
        'defaults':   OutputConfig.as_dict(),
    })


@api.route('/admin/keys/<int:key_id>/output', methods=['GET'])
def admin_get_key_output(key_id):
    """
    Return the output configuration for a specific key. Admin only.
    Returns stored overrides, effective merged config, and server defaults.
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    record = db_manager.get_api_key_by_id(key_id)
    if not record:
        return _error(f'Key {key_id} not found', 404)

    stored    = record.get('output_config') or {}
    effective = OutputConfig.merge(stored)

    return jsonify({
        'key_id':     key_id,
        'identifier': record.get('identifier'),
        'stored':     stored,
        'effective':  effective,
        'defaults':   OutputConfig.as_dict(),
    })


@api.route('/me/forget-device', methods=['POST'])
def me_forget_device():
    """
    Forget a trusted-device token for the currently authenticated user.

    Body: { "device_token": "..." }

    Used by the portal on logout to revoke the "remember this device"
    cookie. Always returns 200 even if the token was already invalid —
    forgetting an already-forgotten device is not an error.
    """
    user = getattr(g, 'user', None)
    if not user:
        return _error('Unauthorized', 401)

    data         = request.get_json(silent=True) or {}
    device_token = data.get('device_token', '').strip()

    if device_token:
        db_manager.delete_trusted_device(device_token)

    return jsonify({'message': 'Device forgotten'})


@api.route('/me/rotate', methods=['POST'])
def me_rotate():
    """
    Rotate the API key for the currently authenticated user.
    Generates a new key, updates the record, and returns the plaintext once.
    """
    user = getattr(g, 'user', None)
    if not user:
        return _error('Unauthorized', 401)

    key_id = int(user.get('id', 0))
    if not key_id:
        return _error('Could not determine key ID from session', 500)

    record = db_manager.get_api_key_by_id(key_id)
    if not record:
        return _error('Key record not found', 404)

    from key_crypto import KeyCrypto
    from config import Config

    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    updated = db_manager.update_api_key(key_id, key_enc=key_enc, key_prefix=prefix)
    if not updated:
        return _error('Failed to rotate key', 500)

    logger.info(f"Self-rotated key {key_id} (identifier={record['identifier']})")

    # Send the new key by email
    to_email = record.get('identifier')  # identifier is always the email address

    if to_email:
        email_svc = EmailService()
        email_svc.send_key_rotated(to_email, record.get('name', ''), record['identifier'], plaintext, template=_resolve_template('key-rotated'))
        logger.info(f"Key rotation email sent to '{to_email}' (key_id={key_id})")

    return jsonify({
        'message':    'Key rotated successfully',
        'key_id':     key_id,
        'identifier': record['identifier'],
        'key_prefix': prefix,
        'api_key':    plaintext,
        'warning':    'Save this key — it will not be shown again',
    })


@api.route('/me/output', methods=['POST'])
def me_output():
    """
    Update the output configuration for the currently authenticated key.
    Body: { "output_config": { ... } | null }
    """
    user = getattr(g, 'user', None)
    if not user:
        return _error('Unauthorized', 401)

    key_id = int(user.get('id', 0))
    if not key_id:
        return _error('Could not determine key ID from session', 500)

    data       = request.get_json(silent=True) or {}
    output_cfg = data.get('output_config')

    updated = db_manager.update_api_key(key_id, output_config=output_cfg)
    if not updated:
        return _error('Failed to update output config', 500)

    return jsonify({'message': 'Output configuration updated'})



# ---------------------------------------------------------------------------
# Admin — SMTP configuration
# ---------------------------------------------------------------------------

@api.route('/admin/smtp', methods=['GET'])
def admin_get_smtp():
    """Return current SMTP configuration. Password is masked. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    cfg = db_manager.get_smtp_config()

    # Mask password — return True/False to indicate whether it is set
    cfg['password_set'] = bool(cfg.get('password', '').strip())
    cfg.pop('password', None)

    return jsonify({'config': cfg, 'configured': bool(cfg.get('host') and cfg.get('user'))})


@api.route('/admin/smtp', methods=['POST'])
def admin_set_smtp():
    """
    Save SMTP configuration. Admin only.

    Body fields (all optional — only supplied fields are updated):
        host, port, user, password, from_addr,
        use_tls, use_ssl, admin_email, base_url
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data = request.get_json(silent=True) or {}

    allowed = {
        'host', 'port', 'user', 'password', 'from_addr',
        'use_tls', 'use_ssl', 'admin_email', 'base_url', 'portal_url',
    }
    config = {k: str(v) for k, v in data.items() if k in allowed}

    if not config:
        return _error('No valid SMTP fields provided', 400)

    db_manager.set_smtp_config(config)
    logger.info(f"SMTP config updated by admin (host={config.get('host', '—')})")

    return jsonify({'message': 'SMTP configuration saved'})


@api.route('/admin/smtp/test', methods=['POST'])
def admin_test_smtp():
    """
    Send a test email using the current SMTP configuration. Admin only.
    Body: { "to": "email@example.com" }
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data     = request.get_json(silent=True) or {}
    to_email = data.get('to', '').strip()

    if not to_email:
        return _error('to email address is required', 400)

    from email_service import EmailService
    svc = EmailService()

    if not svc.enabled:
        return _error(
            'SMTP is not configured. Set host, user, and password first.', 400
        )

    sent = svc.send_test_email(to_email, template=_resolve_template('test'))

    if sent:
        return jsonify({'message': f'Test email sent to {to_email}'})
    else:
        return _error('Failed to send test email — check server logs for details', 500)


@api.route('/admin/smtp', methods=['DELETE'])
def admin_clear_smtp():
    """Clear all SMTP configuration from the database. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    db_manager.clear_smtp_config()
    logger.info("SMTP config cleared by admin")
    return jsonify({'message': 'SMTP configuration cleared'})


# ---------------------------------------------------------------------------
# Admin — Portal settings
# ---------------------------------------------------------------------------

@api.route('/admin/portal-settings', methods=['GET'])
def admin_get_portal_settings():
    """
    Return all portal settings with their current values and defaults.
    Admin only.
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    settings  = db_manager.get_portal_settings()
    defaults  = db_manager.PORTAL_SETTINGS_DEFAULTS

    return jsonify({
        'settings': settings,
        'defaults': defaults,
    })


@api.route('/admin/portal-settings', methods=['POST'])
def admin_set_portal_settings():
    """
    Update one or more portal settings. Admin only.

    Body: { "setting_key": value, ... }

    Allowed keys: site_name, site_version, session_timeout,
    logout_redirect_url, allow_admin_promotion, trusted_device_days,
    portal_url
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data = request.get_json(silent=True) or {}
    if not data:
        return _error('No settings provided', 400)

    allowed = set(db_manager.PORTAL_SETTINGS_DEFAULTS.keys())
    unknown = set(data.keys()) - allowed
    if unknown:
        return _error(f"Unknown settings: {', '.join(sorted(unknown))}", 400)

    db_manager.set_portal_settings(data)
    logger.info(f"Admin [{user.get('identifier')}] updated portal settings: {list(data.keys())}")

    return jsonify({
        'message':  'Portal settings updated',
        'settings': db_manager.get_portal_settings(),
    })


@api.route('/admin/portal-settings/<key>', methods=['DELETE'])
def admin_reset_portal_setting(key):
    """Reset a single portal setting to its built-in default. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    if not db_manager.reset_portal_setting(key):
        return _error(f"Unknown setting '{key}'", 400)

    logger.info(f"Admin [{user.get('identifier')}] reset portal setting '{key}' to default")
    return jsonify({
        'message':  f"Setting '{key}' reset to default",
        'settings': db_manager.get_portal_settings(),
    })


@api.route('/locations/resolve', methods=['POST'])
def locations_resolve():
    """Resolve a place name to its canonical place record with lat/lon and timezone."""
    try:
        data = request.get_json(silent=True)
        if not data or not data.get('place_name'):
            return _error('place_name is required', 400)

        place_name = str(data['place_name']).strip()
        if len(place_name) < 2:
            return _error('place_name must be at least 2 characters', 400)

        place, error = geocoding_service.resolve_place(place_name)
        if error:
            return jsonify({'success': False, 'error': error}), 400

        return jsonify({'success': True, 'place': place})

    except Exception as e:
        logger.error(f"Location resolve error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Resolution failed: {str(e)}'}), 500




# ---------------------------------------------------------------------------
# Progression / solar arc output helpers
# ---------------------------------------------------------------------------

def _filter_to_natal_bodies(result_positions: dict, natal_positions: dict) -> dict:
    """
    Filter result_positions to only include bodies that are present
    (non-null) in the natal chart. Ensures progressions and directions
    never return bodies that weren't in the original calculation.
    """
    if not natal_positions or not result_positions:
        return result_positions
    natal_bodies = {k for k, v in natal_positions.items() if v is not None}
    return {k: v for k, v in result_positions.items() if k in natal_bodies}


# Helper functions

def _parse_datetime(datetime_str: str):
    """Parse datetime string in various formats"""
    try:
        return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
    except ValueError:
        try:
            return datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                return datetime.strptime(datetime_str, '%Y-%m-%d')
            except ValueError:
                return None


def _convert_to_utc(dt: datetime, timezone_str: str):
    """Convert datetime to UTC and return both UTC and local versions"""
    if dt.tzinfo is None:
        local_tz = pytz.timezone(timezone_str)
        dt_local  = local_tz.localize(dt)
        dt_utc    = dt_local.astimezone(pytz.UTC)
    else:
        dt_utc    = dt.astimezone(pytz.UTC)
        local_tz  = pytz.timezone(timezone_str)
        dt_local  = dt_utc.astimezone(local_tz)

    return dt_utc, dt_local
