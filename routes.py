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
from validators import validate_request, CalculateSchema, AutocompleteSchema, ProgressionSchema, SolarReturnSchema, LunarReturnSchema, ApsideSchema, LunationSchema, NextApsideSchema, EphemerisSchema, EclipseSchema, RegisterDomainSchema, RegisterUserSchema, AdminReviewSchema
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
            chart_id = recalc_chart_id
            logger.info(
                f"[{user.get('name', 'unknown')}] Recalculated chart {chart_id}"
            )
        else:
            chart_id = db_manager.save_chart_to_cache(
                dt_utc, dt_local, location_info['id'], result, chart_name,
                house_system=house_system
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

        if output_cfg.get('geocentric', True):
            response['planetary_positions'] = result.get('geocentric')
        if output_cfg.get('heliocentric', True):
            response['heliocentric'] = result.get('heliocentric')

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
            'planetary_positions': stored.get('geocentric'),
            'heliocentric':        stored.get('heliocentric'),
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
        natal_data = natal_chart['chart_data']
        if result.get('geocentric'):
            result['geocentric'] = _filter_to_natal_bodies(
                result['geocentric'], natal_data.get('geocentric', {})
            )
        if result.get('heliocentric'):
            result['heliocentric'] = _filter_to_natal_bodies(
                result['heliocentric'], natal_data.get('heliocentric', {})
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

        if output_cfg.get('geocentric', True):
            response['planetary_positions'] = result.get('geocentric')
        if output_cfg.get('heliocentric', True):
            response['heliocentric'] = result.get('heliocentric')
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

        # Natal positions from stored chart data
        natal_positions = natal_chart['chart_data']

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
        natal_data = natal_chart['chart_data']
        if result.get('geocentric'):
            result['geocentric'] = _filter_to_natal_bodies(
                result['geocentric'], natal_data.get('geocentric', {})
            )
        if result.get('heliocentric'):
            result['heliocentric'] = _filter_to_natal_bodies(
                result['heliocentric'], natal_data.get('heliocentric', {})
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

        if output_cfg.get('geocentric', True):
            response['planetary_positions'] = result.get('geocentric')
        if output_cfg.get('heliocentric', True):
            response['heliocentric'] = result.get('heliocentric')
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

        if output_cfg.get('geocentric', True):
            response['planetary_positions'] = result.get('geocentric')
        if output_cfg.get('heliocentric', True):
            response['heliocentric'] = result.get('heliocentric')
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

        if output_cfg.get('geocentric', True):
            response['planetary_positions'] = result.get('geocentric')
        if output_cfg.get('heliocentric', True):
            response['heliocentric'] = result.get('heliocentric')
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
        derived['planetary_positions'] = chart_data.get('geocentric')
        derived['heliocentric']        = chart_data.get('heliocentric')
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
# Registration — Domain
# ---------------------------------------------------------------------------

@api.route('/register/domain', methods=['POST'])
@validate_request(RegisterDomainSchema)
def register_domain(validated_data):
    """
    Submit a domain API key registration request.

    The key is created immediately but left inactive (active=0).
    An admin must approve it via POST /admin/registrations/<id>/approve.
    The contact_email receives a confirmation that the request was received,
    and a second email when approved or rejected.

    Body: domain, name, contact_email, reason (optional)
    """
    domain        = validated_data['domain'].lower().strip()
    name          = validated_data['name'].strip()
    contact_email = validated_data['contact_email'].strip()
    reason        = validated_data.get('reason')

    # Reject duplicate domain
    if db_manager.domain_registration_exists(domain):
        return _error(f"A registration request for '{domain}' already exists", 409)

    # Also check api_keys table for an existing active/inactive key
    all_keys = db_manager.get_all_api_keys(include_inactive=True)
    if any(k['identifier'] == domain for k in all_keys):
        return _error(f"An API key for '{domain}' already exists", 409)

    from key_crypto import KeyCrypto
    from config import Config

    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    # Create key — inactive until approved
    key_id = db_manager.create_api_key(
        key_type='domain',
        name=name,
        identifier=domain,
        key_enc=key_enc,
        key_prefix=prefix,
        admin=False,
        active=False,
    )

    # Create registration request record
    request_id = db_manager.create_registration_request(
        api_key_id=key_id,
        domain=domain,
        name=name,
        reason=reason,
        contact_email=contact_email,
    )

    # Emails
    email_svc = EmailService()
    email_svc.send_domain_registration_received(contact_email, name, domain, template=_resolve_template('register-domain'))
    email_svc.send_admin_new_registration(domain, name, contact_email, reason, request_id)

    logger.info(f"Domain registration request: '{domain}' (request_id={request_id}, key_id={key_id})")

    return jsonify({
        'message':    'Registration request received and pending admin review',
        'domain':     domain,
        'request_id': request_id,
        'status':     'pending',
    }), 201


# ---------------------------------------------------------------------------
# Registration — User
# ---------------------------------------------------------------------------

@api.route('/register/user', methods=['POST'])
@validate_request(RegisterUserSchema)
def register_user(validated_data):
    """
    Submit a user API key registration request.

    The key is created inactive. A verification email is sent — clicking
    the link activates the key and returns the plaintext key once.

    Body: email, name
    """
    email = validated_data['email'].strip().lower()
    name  = validated_data['name'].strip()

    # Reject duplicates
    all_keys = db_manager.get_all_api_keys(include_inactive=True)
    if any(k['identifier'] == email for k in all_keys):
        # Return 200 with generic message — don't leak whether email exists
        return jsonify({
            'message': 'If this email is not already registered, a verification email has been sent'
        })

    from key_crypto import KeyCrypto
    from config import Config

    crypto    = KeyCrypto(Config.SECRET_KEY)
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    # Store plaintext encrypted separately so we can return it after verification
    # We re-encrypt the plaintext under a one-time token for safe retrieval
    key_id = db_manager.create_api_key(
        key_type='user',
        name=name,
        identifier=email,
        key_enc=key_enc,
        key_prefix=prefix,
        admin=False,
        active=False,
    )

    # Store plaintext encrypted in output_config temporarily for post-verify reveal
    # It is cleared after verification completes
    reveal_enc = crypto.encrypt(plaintext)
    db_manager.update_api_key(key_id, output_config={'_pending_reveal': reveal_enc})

    # Create verification token
    token = _secrets.token_urlsafe(32)
    db_manager.create_email_verification(
        api_key_id=key_id,
        email=email,
        token=token,
    )

    email_svc = EmailService()
    email_svc.send_user_verification(email, name, token, template=_resolve_template('user-verify'))

    logger.info(f"User registration: '{email}' (key_id={key_id}) — verification sent")

    return jsonify({
        'message': 'Verification email sent. Click the link in the email to activate your API key.',
        'email':   email,
    }), 201


@api.route('/register/verify', methods=['GET'])
def verify_email():
    """
    Activate a user API key from an email verification link.

    Query param: t — the verification token from the email

    The plaintext API key is returned once in the response.
    The token is marked used and the key activated.
    """
    token = request.args.get('t', '').strip()
    if not token:
        return _error('Verification token is required', 400)

    record = db_manager.get_email_verification(token)
    if not record:
        return _error('Invalid, expired, or already used verification link', 400)

    key_id = record['api_key_id']
    email  = record['email']

    # Retrieve and decrypt the pending plaintext key
    all_keys    = db_manager.get_all_api_keys(include_inactive=True)
    key_record  = next((k for k in all_keys if k['id'] == key_id), None)
    if not key_record:
        return _error('Key record not found', 500)

    from key_crypto import KeyCrypto
    from config import Config
    crypto = KeyCrypto(Config.SECRET_KEY)

    # Retrieve pending reveal, activate key, clear reveal field
    output_cfg  = key_record.get('output_config') or {}
    reveal_enc  = output_cfg.pop('_pending_reveal', None)
    plaintext   = crypto.decrypt(reveal_enc) if reveal_enc else None

    db_manager.update_api_key(key_id, active=1, output_config=output_cfg or None)
    db_manager.mark_email_verification_used(token)

    logger.info(f"Email verified: '{email}' (key_id={key_id}) — key activated")

    # Email the API key to the user — this is the one and only time it is delivered
    if plaintext:
        email_svc = EmailService()
        email_svc.send_user_key_activated(email, key_record.get('name', ''), plaintext, template=_resolve_template('user-activated'))
        logger.info(f"API key emailed to '{email}' (key_id={key_id})")

    return jsonify({
        'message':    'Email verified. Your API key has been sent to your email address.',
        'email':      email,
        'key_active': True,
    })


# ---------------------------------------------------------------------------
# Admin — Registration management
# ---------------------------------------------------------------------------

@api.route('/admin/registrations', methods=['GET'])
def admin_list_registrations():
    """
    List domain registration requests.
    Optional query param: status — pending | approved | rejected
    Requires admin API key.
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    status   = request.args.get('status')
    requests = db_manager.get_registration_requests(status=status)
    return jsonify({
        'count':    len(requests),
        'status':   status or 'all',
        'requests': requests,
    })


@api.route('/admin/registrations/<int:request_id>/approve', methods=['POST'])
@validate_request(AdminReviewSchema)
def admin_approve_registration(validated_data, request_id):
    """
    Approve a domain registration request.
    Activates the key and emails the plaintext key to the contact.
    Requires admin API key.

    Body: admin_note (optional)
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    reg = db_manager.get_registration_request_by_id(request_id)
    if not reg:
        return _error(f'Registration request {request_id} not found', 404)

    if reg['status'] != 'pending':
        return _error(f"Request is already '{reg['status']}'", 409)

    admin_note = validated_data.get('admin_note')
    key_id     = reg['api_key_id']

    # Activate the key
    db_manager.update_api_key(key_id, active=1)
    db_manager.update_registration_request(request_id, status='approved', admin_note=admin_note)

    # Decrypt and email the plaintext key to the registrant
    from key_crypto import KeyCrypto
    from config import Config
    crypto = KeyCrypto(Config.SECRET_KEY)

    all_keys   = db_manager.get_all_api_keys(include_inactive=False)
    key_record = next((k for k in all_keys if k['id'] == key_id), None)
    plaintext  = None

    if key_record:
        # We need key_enc — fetch directly
        with db_manager.get_connection() as conn:
            row = conn.execute(
                'SELECT key_enc FROM api_keys WHERE id = ?', (key_id,)
            ).fetchone()
            if row:
                plaintext = crypto.decrypt(row['key_enc'])

    email_svc = EmailService()
    if plaintext and reg.get('contact_email'):
        email_svc.send_domain_approved(
            reg['contact_email'], reg['name'], reg['domain'],
            plaintext, admin_note, template=_resolve_template('register-approved')
        )

    logger.info(f"Domain registration approved: '{reg['domain']}' (request_id={request_id})")

    return jsonify({
        'message':   f"Registration for '{reg['domain']}' approved and key activated",
        'domain':    reg['domain'],
        'key_id':    key_id,
        'email_sent': bool(plaintext and reg.get('contact_email')),
    })


@api.route('/admin/registrations/<int:request_id>/reject', methods=['POST'])
@validate_request(AdminReviewSchema)
def admin_reject_registration(validated_data, request_id):
    """
    Reject a domain registration request.
    The key remains inactive. An email is sent to the contact.
    Requires admin API key.

    Body: admin_note (optional)
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    reg = db_manager.get_registration_request_by_id(request_id)
    if not reg:
        return _error(f'Registration request {request_id} not found', 404)

    if reg['status'] != 'pending':
        return _error(f"Request is already '{reg['status']}'", 409)

    admin_note = validated_data.get('admin_note')

    db_manager.update_registration_request(request_id, status='rejected', admin_note=admin_note)

    email_svc = EmailService()
    if reg.get('contact_email'):
        email_svc.send_domain_rejected(
            reg['contact_email'], reg['name'], reg['domain'],
            admin_note, template=_resolve_template('register-rejected')
        )

    logger.info(f"Domain registration rejected: '{reg['domain']}' (request_id={request_id})")

    return jsonify({
        'message':    f"Registration for '{reg['domain']}' rejected",
        'domain':     reg['domain'],
        'email_sent': bool(reg.get('contact_email')),
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
    'register-domain': {
        'subject':     'Domain registration received — {domain}',
        'header_text': 'Registration Received',
        'body_text':   'Hi {name},\n\nThank you for registering {domain}. Your request is under review.',
        'footer_text': 'ephemeralREST',
    },
    'register-approved': {
        'subject':     'Domain registration approved — {domain}',
        'header_text': 'Registration Approved',
        'body_text':   'Hi {name},\n\nYour registration for {domain} has been approved.\n\nYour API key:\n\n{api_key}\n\nSave this key — it will not be shown again.\n\n{admin_note}',
        'footer_text': 'ephemeralREST',
    },
    'register-rejected': {
        'subject':     'Domain registration update — {domain}',
        'header_text': 'Registration Update',
        'body_text':   'Hi {name},\n\nYour registration for {domain} was not approved at this time.\n\n{admin_note}',
        'footer_text': 'ephemeralREST',
    },
    'user-verify': {
        'subject':     'Verify your email address',
        'header_text': 'Verify Your Email',
        'body_text':   'Hi {name},\n\nPlease verify your email address to activate your API key:\n\n{verify_url}\n\nThis link expires in 24 hours.',
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
        'id':         user.get('id'),
        'name':       user.get('name'),
        'identifier': user.get('identifier'),
        'key_type':   user.get('key_type'),
        'is_domain':  user.get('is_domain'),
        'is_user':    user.get('is_user'),
        'admin':      user.get('admin'),
        'active':     user.get('active'),
        'rate_limits': user.get('rate_limits'),
    })



# ---------------------------------------------------------------------------
# Admin — Key management endpoints
# ---------------------------------------------------------------------------

@api.route('/admin/keys/<int:key_id>/set-type', methods=['POST'])
def admin_set_key_type(key_id):
    """Change the key_type of an API key between 'domain' and 'user'. Admin only."""
    user = getattr(g, 'user', None)
    if not user or not user.get('admin'):
        return _error('Admin access required', 403)

    data     = request.get_json(silent=True) or {}
    key_type = data.get('key_type', '').strip().lower()

    if key_type not in ('domain', 'user'):
        return _error("key_type must be 'domain' or 'user'", 400)

    key_record = db_manager.get_api_key_by_id(key_id)
    if not key_record:
        return _error('Key not found', 404)

    if key_record.get('key_type') == key_type:
        return _error(f'Key is already of type {key_type!r}', 400)

    db_manager.update_api_key(key_id, key_type=key_type)
    logger.info(
        f"Admin [{user.get('identifier')}] changed key_id={key_id} "
        f"type: {key_record.get('key_type')} → {key_type}"
    )
    return jsonify({'message': f"Key type updated to '{key_type}'", 'key_id': key_id, 'key_type': key_type})


@api.route('/admin/keys', methods=['GET'])
def admin_list_keys():
    """
    List all API keys. Admin only.
    Optional query params:
        type     — filter by 'domain' or 'user'
        inactive — include disabled keys if set to '1'
    """
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    include_inactive = request.args.get('inactive', '0') == '1'
    type_filter      = request.args.get('type')

    keys = db_manager.get_all_api_keys(include_inactive=include_inactive)

    if type_filter:
        keys = [k for k in keys if k.get('key_type') == type_filter]

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
    if record.get('key_type') == 'user':
        to_email = record.get('identifier')
    else:
        reg = next(
            (r for r in db_manager.get_registration_requests()
             if r.get('api_key_id') == key_id),
            None
        )
        if reg:
            to_email = reg.get('contact_email')

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



@api.route('/admin/class-limits/<key_type>', methods=['GET'])
def admin_get_class_limits(key_type):
    """Get rate limits for a specific key class. Admin only."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    if key_type not in ('domain', 'user', 'wildcard'):
        return _error('Invalid key type. Valid: domain, user, wildcard', 400)

    limits = db_manager.get_key_class_limits(key_type)
    limits['key_type'] = key_type
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

    data     = request.get_json(silent=True) or {}
    key_type = data.get('key_type', '')

    if key_type not in ('domain', 'user', 'wildcard'):
        return _error('Invalid key_type. Valid: domain, user, wildcard', 400)

    try:
        rpm = int(data['rate_per_minute'])
        rph = int(data['rate_per_hour'])
        rpd = int(data['rate_per_day'])
    except (KeyError, TypeError, ValueError):
        return _error('rate_per_minute, rate_per_hour, and rate_per_day are required integers', 400)

    db_manager.set_key_class_limits(key_type, rpm, rph, rpd)
    logger.info(f"Admin updated class limits for '{key_type}': {rpm}/min {rph}/hr {rpd}/day")

    return jsonify({
        'message':        f"Class limits for '{key_type}' updated",
        'key_type':       key_type,
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
    to_email = record.get('identifier') if record.get('key_type') == 'user' else None
    if not to_email:
        # Domain key — look up the registration contact email
        reg = next(
            (r for r in db_manager.get_registration_requests()
             if r.get('api_key_id') == key_id),
            None
        )
        if reg:
            to_email = reg.get('contact_email')

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


@api.route('/cors-test', methods=['GET', 'POST', 'OPTIONS'])
def cors_test():
    """Test endpoint for CORS functionality"""
    if request.method == 'OPTIONS':
        return jsonify({'message': 'CORS preflight successful'}), 200

    return jsonify({
        'message':    'CORS is working correctly',
        'method':     request.method,
        'origin':     request.headers.get('Origin', 'No origin header'),
        'user_agent': request.headers.get('User-Agent', 'No user agent'),
        'timestamp':  datetime.now().isoformat()
    })



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