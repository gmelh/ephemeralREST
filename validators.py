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
# validators.py                                                               #
################################################################################

"""
Input validation schemas for Astro API
Uses marshmallow for request validation
"""
from marshmallow import Schema, fields, validate, ValidationError

# Valid house system names — must match keys in AstronomyService.HOUSE_SYSTEMS
VALID_HOUSE_SYSTEMS = [
    'placidus',
    'koch',
    'porphyrius',
    'regiomontanus',
    'campanus',
    'equal',
    'vehlow_equal',
    'whole_sign',
    'meridian',
    'azimuthal',
    'topocentric',
    'alcabitus',
    'morinus',
    'krusinski',
    'gauquelin',
]


class AnglesOutputSchema(Schema):
    """Per-request overrides for angle output"""
    asc        = fields.Bool(load_default=None)
    mc         = fields.Bool(load_default=None)
    vertex     = fields.Bool(load_default=None)
    east_point = fields.Bool(load_default=None)
    armc       = fields.Bool(load_default=None)


class BodiesOutputSchema(Schema):
    """Per-request overrides for celestial body output"""
    # Standard planets
    sun     = fields.Bool(load_default=None)
    moon    = fields.Bool(load_default=None)
    mercury = fields.Bool(load_default=None)
    venus   = fields.Bool(load_default=None)
    mars    = fields.Bool(load_default=None)
    jupiter = fields.Bool(load_default=None)
    saturn  = fields.Bool(load_default=None)
    uranus  = fields.Bool(load_default=None)
    neptune = fields.Bool(load_default=None)
    pluto   = fields.Bool(load_default=None)
    earth   = fields.Bool(load_default=None)
    # Asteroids
    asteroids = fields.Bool(load_default=None)   # master switch
    ceres     = fields.Bool(load_default=None)
    pallas    = fields.Bool(load_default=None)
    juno      = fields.Bool(load_default=None)
    vesta     = fields.Bool(load_default=None)
    chiron    = fields.Bool(load_default=None)
    # Nodes
    mean_node  = fields.Bool(load_default=None)
    true_node  = fields.Bool(load_default=None)
    south_node = fields.Bool(load_default=None)
    # Lunar apsides
    mean_lilith = fields.Bool(load_default=None)
    true_lilith = fields.Bool(load_default=None)
    # Special points
    part_of_fortune = fields.Bool(load_default=None)


class MetaOutputSchema(Schema):
    """Per-request overrides for response metadata"""
    api_usage  = fields.Bool(load_default=None)
    from_cache = fields.Bool(load_default=None)


class OutputSchema(Schema):
    """
    Per-request output configuration.
    Any field omitted here retains its server-side default from OutputConfig.
    """
    geocentric        = fields.Bool(load_default=None)
    heliocentric      = fields.Bool(load_default=None)
    right_ascension   = fields.Bool(load_default=None)
    declination       = fields.Bool(load_default=None)
    longitude_speed   = fields.Bool(load_default=None)
    latitude_speed    = fields.Bool(load_default=None)
    declination_speed = fields.Bool(load_default=None)
    retrograde        = fields.Bool(load_default=None)
    default_house_system = fields.Str(
        load_default=None,
        validate=validate.OneOf(
            VALID_HOUSE_SYSTEMS,
            error=f"Invalid house system. Valid options: {', '.join(VALID_HOUSE_SYSTEMS)}"
        ),
        allow_none=True
    )
    angles  = fields.Nested(AnglesOutputSchema, load_default=None)
    bodies  = fields.Nested(BodiesOutputSchema, load_default=None)
    meta    = fields.Nested(MetaOutputSchema,   load_default=None)


class CalculateSchema(Schema):
    """Schema for calculation endpoint"""
    chart_name = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=100),
        error_messages={'required': 'Chart name is required'}
    )
    datetime = fields.Str(
        required=True,
        error_messages={'required': 'datetime field is required'}
    )
    location = fields.Str(
        required=True,
        validate=validate.Length(min=2, max=200),
        error_messages={'required': 'location field is required'}
    )
    house_system = fields.Str(
        load_default=None,
        validate=validate.OneOf(
            VALID_HOUSE_SYSTEMS,
            error=f"Invalid house system. Valid options: {', '.join(VALID_HOUSE_SYSTEMS)}"
        ),
        allow_none=True
    )
    output = fields.Nested(OutputSchema, load_default=None)
    # Recalculation fields
    # Set recalc=True to force fresh calculation and update an existing chart record
    # in place, preserving its UUID. chart_id is required when recalc=True.
    recalc   = fields.Bool(load_default=False)
    chart_id = fields.Str(load_default=None, allow_none=True)


class AutocompleteSchema(Schema):
    """Schema for autocomplete endpoint"""
    q = fields.Str(
        required=True,
        validate=validate.Length(min=2, max=200),
        error_messages={'required': 'Query parameter "q" is required'}
    )


class ChartIdSchema(Schema):
    """Schema for chart ID parameter"""
    chart_id = fields.UUID(
        required=True,
        error_messages={'required': 'chart_id is required'}
    )




class ProgressionSchema(Schema):
    """Schema for secondary progressions and solar arc directions endpoints"""
    progression_date = fields.Str(
        required=True,
        error_messages={'required': 'progression_date is required'}
    )
    location = fields.Str(
        load_default=None,
        validate=validate.Length(min=2, max=200),
        allow_none=True
    )
    house_system = fields.Str(
        load_default=None,
        validate=validate.OneOf(
            VALID_HOUSE_SYSTEMS,
            error=f"Invalid house system. Valid options: {', '.join(VALID_HOUSE_SYSTEMS)}"
        ),
        allow_none=True
    )
    output = fields.Nested(OutputSchema, load_default=None)



class SolarReturnSchema(Schema):
    """Schema for solar return endpoint"""
    return_year  = fields.Int(
        required=True,
        validate=validate.Range(min=1800, max=2200),
        error_messages={'required': 'return_year is required'}
    )
    location     = fields.Str(load_default=None, validate=validate.Length(min=2, max=200), allow_none=True)
    house_system = fields.Str(
        load_default=None,
        validate=validate.OneOf(VALID_HOUSE_SYSTEMS,
            error=f"Invalid house system. Valid options: {', '.join(VALID_HOUSE_SYSTEMS)}"),
        allow_none=True
    )
    output       = fields.Nested(OutputSchema, load_default=None)


class LunarReturnSchema(Schema):
    """Schema for lunar return endpoint"""
    return_year  = fields.Int(
        required=True,
        validate=validate.Range(min=1800, max=2200),
        error_messages={'required': 'return_year is required'}
    )
    return_month = fields.Int(
        required=True,
        validate=validate.Range(min=1, max=12),
        error_messages={'required': 'return_month is required'}
    )
    location     = fields.Str(load_default=None, validate=validate.Length(min=2, max=200), allow_none=True)
    house_system = fields.Str(
        load_default=None,
        validate=validate.OneOf(VALID_HOUSE_SYSTEMS,
            error=f"Invalid house system. Valid options: {', '.join(VALID_HOUSE_SYSTEMS)}"),
        allow_none=True
    )
    output       = fields.Nested(OutputSchema, load_default=None)



VALID_LUNATION_PHASES = ['new_moon', 'first_quarter', 'full_moon', 'last_quarter']
VALID_LUNATION_DIRECTIONS = ['next', 'previous', 'both']


class ApsideSchema(Schema):
    """Schema for apsides endpoint"""
    datetime     = fields.Str(
        required=True,
        error_messages={'required': 'datetime is required'}
    )
    output       = fields.Nested(OutputSchema, load_default=None)


class LunationSchema(Schema):
    """Schema for lunations endpoint"""
    reference_date = fields.Str(
        required=True,
        error_messages={'required': 'reference_date is required'}
    )
    direction = fields.Str(
        load_default='next',
        validate=validate.OneOf(
            VALID_LUNATION_DIRECTIONS,
            error=f"Invalid direction. Valid options: {', '.join(VALID_LUNATION_DIRECTIONS)}"
        )
    )
    # Range mode — provide both to search a date range
    start_date = fields.Str(load_default=None, allow_none=True)
    end_date   = fields.Str(load_default=None, allow_none=True)
    # Filter to specific phases — default returns all four
    phases     = fields.List(
        fields.Str(validate=validate.OneOf(
            VALID_LUNATION_PHASES,
            error=f"Invalid phase. Valid: {', '.join(VALID_LUNATION_PHASES)}"
        )),
        load_default=None,
        allow_none=True
    )



VALID_APSIDE_BODIES = [
    'moon', 'mercury', 'venus', 'mars', 'jupiter', 'saturn',
    'uranus', 'neptune', 'pluto', 'ceres', 'pallas', 'juno', 'vesta', 'chiron',
]
VALID_APSIDE_EVENTS = ['perigee', 'perihelion', 'apogee', 'aphelion']


class NextApsideSchema(Schema):
    """Schema for next apsides event finder endpoint"""
    reference_date    = fields.Str(
        required=True,
        error_messages={'required': 'reference_date is required'}
    )
    bodies            = fields.List(
        fields.Str(validate=validate.OneOf(
            VALID_APSIDE_BODIES,
            error=f"Invalid body. Valid: {', '.join(VALID_APSIDE_BODIES)}"
        )),
        load_default=None,
        allow_none=True
    )
    events            = fields.List(
        fields.Str(validate=validate.OneOf(
            VALID_APSIDE_EVENTS,
            error=f"Invalid event. Valid: {', '.join(VALID_APSIDE_EVENTS)}"
        )),
        load_default=None,
        allow_none=True
    )
    max_search_years  = fields.Int(
        load_default=20,
        validate=validate.Range(min=1, max=50)
    )



class EphemerisSchema(Schema):
    """Schema for monthly ephemeris endpoint"""
    year  = fields.Int(
        required=True,
        validate=validate.Range(min=1800, max=2200),
        error_messages={'required': 'year is required'}
    )
    month = fields.Int(
        required=True,
        validate=validate.Range(min=1, max=12),
        error_messages={'required': 'month is required'}
    )
    output = fields.Nested(OutputSchema, load_default=None)



class RegisterDomainSchema(Schema):
    domain         = fields.Str(required=True, validate=validate.Length(min=3, max=200))
    name           = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    contact_email  = fields.Email(required=True)
    reason         = fields.Str(load_default=None, validate=validate.Length(max=1000), allow_none=True)


class RegisterUserSchema(Schema):
    email          = fields.Email(required=True)
    name           = fields.Str(required=True, validate=validate.Length(min=1, max=100))


class AdminReviewSchema(Schema):
    admin_note     = fields.Str(load_default=None, allow_none=True, validate=validate.Length(max=500))

def validate_request(schema_class):
    """Decorator to validate request data against a schema"""

    def decorator(f):
        from functools import wraps
        from flask import request, jsonify

        @wraps(f)
        def decorated_function(*args, **kwargs):
            schema = schema_class()

            if request.method in ['GET', 'DELETE']:
                data = request.args.to_dict()
            else:
                data = request.get_json(silent=True)
                if data is None:
                    return jsonify({'error': 'Invalid JSON payload', 'status': 400}), 400

            try:
                validated_data = schema.load(data)
                kwargs['validated_data'] = validated_data
                return f(*args, **kwargs)
            except ValidationError as err:
                return jsonify({
                    'error': 'Validation failed',
                    'status': 400,
                    'details': err.messages
                }), 400

        return decorated_function

    return decorator