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
# output_config.py                                                            #
################################################################################

"""
Server-side output configuration for Astro API.

These are the default values used for all requests. Individual requests can
override any of these by including an 'output' object in the request body.

Example per-request override:
    {
        "chart_name": "Test",
        "datetime": "1985-06-12 14:30:00",
        "location": "London",
        "output": {
            "heliocentric": false,
            "bodies": {
                "asteroids": false
            }
        }
    }
"""


class OutputConfig:
    """
    Default output configuration.
    All values can be overridden per-request via the 'output' request body field.
    """

    # -------------------------------------------------------------------------
    # Coordinate systems
    # -------------------------------------------------------------------------
    GEOCENTRIC   = True
    HELIOCENTRIC = True

    # -------------------------------------------------------------------------
    # Equatorial coordinates (returned alongside ecliptic for all bodies)
    # -------------------------------------------------------------------------
    RIGHT_ASCENSION = True
    DECLINATION     = True

    # -------------------------------------------------------------------------
    # Speed / motion data
    # -------------------------------------------------------------------------
    LONGITUDE_SPEED   = True
    LATITUDE_SPEED    = True
    DECLINATION_SPEED = True

    # -------------------------------------------------------------------------
    # Retrograde flag (geocentric only)
    # -------------------------------------------------------------------------
    RETROGRADE = True

    # -------------------------------------------------------------------------
    # Houses
    # -------------------------------------------------------------------------
    # Default house system used when the request does not specify one.
    # Set to None to return no house cusps by default (ASC/MC still returned).
    DEFAULT_HOUSE_SYSTEM = None

    # -------------------------------------------------------------------------
    # Angles (geocentric only)
    # -------------------------------------------------------------------------
    ASC        = True
    MC         = True
    VERTEX     = True   # included in house_cusps when a house system is used
    EAST_POINT = True   # included in house_cusps when a house system is used
    ARMC       = False  # Sidereal time angle — niche, off by default

    # -------------------------------------------------------------------------
    # Standard planets
    # -------------------------------------------------------------------------
    SUN     = True
    MOON    = True
    MERCURY = True
    VENUS   = True
    MARS    = True
    JUPITER = True
    SATURN  = True
    URANUS  = True
    NEPTUNE = True
    PLUTO   = True

    # -------------------------------------------------------------------------
    # Asteroids (as a group and individually)
    # -------------------------------------------------------------------------
    ASTEROIDS = True   # Master switch — overrides individual settings below if False
    CERES     = True
    PALLAS    = True
    JUNO      = True
    VESTA     = True
    CHIRON    = True

    # -------------------------------------------------------------------------
    # Earth (heliocentric only)
    # -------------------------------------------------------------------------
    EARTH = True

    # -------------------------------------------------------------------------
    # Nodes
    # -------------------------------------------------------------------------
    MEAN_NODE  = True
    TRUE_NODE  = False  # Calculated separately from Mean Node
    SOUTH_NODE = False  # Derived: Mean Node + 180°

    # -------------------------------------------------------------------------
    # Special calculated points
    # -------------------------------------------------------------------------
    PART_OF_FORTUNE = False  # ASC + Moon - Sun (mod 360)

    # -------------------------------------------------------------------------
    # Lunar apsides (Black Moon Lilith)
    # -------------------------------------------------------------------------
    MEAN_LILITH = False  # Mean Black Moon Lilith (swe.MEAN_APOG) — off by default
    TRUE_LILITH = False  # True/Oscillating Black Moon Lilith (swe.OSCU_APOG)

    # -------------------------------------------------------------------------
    # Response metadata
    # -------------------------------------------------------------------------
    INCLUDE_API_USAGE  = True
    INCLUDE_FROM_CACHE = True

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @classmethod
    def as_dict(cls) -> dict:
        """Return the full default config as a plain dict."""
        return {
            'geocentric':        cls.GEOCENTRIC,
            'heliocentric':      cls.HELIOCENTRIC,
            'right_ascension':   cls.RIGHT_ASCENSION,
            'declination':       cls.DECLINATION,
            'longitude_speed':   cls.LONGITUDE_SPEED,
            'latitude_speed':    cls.LATITUDE_SPEED,
            'declination_speed': cls.DECLINATION_SPEED,
            'retrograde':        cls.RETROGRADE,
            'default_house_system': cls.DEFAULT_HOUSE_SYSTEM,
            'angles': {
                'asc':        cls.ASC,
                'mc':         cls.MC,
                'vertex':     cls.VERTEX,
                'east_point': cls.EAST_POINT,
                'armc':       cls.ARMC,
            },
            'bodies': {
                'sun':     cls.SUN,
                'moon':    cls.MOON,
                'mercury': cls.MERCURY,
                'venus':   cls.VENUS,
                'mars':    cls.MARS,
                'jupiter': cls.JUPITER,
                'saturn':  cls.SATURN,
                'uranus':  cls.URANUS,
                'neptune': cls.NEPTUNE,
                'pluto':   cls.PLUTO,
                'earth':   cls.EARTH,
                'asteroids': cls.ASTEROIDS,
                'ceres':   cls.CERES,
                'pallas':  cls.PALLAS,
                'juno':    cls.JUNO,
                'vesta':   cls.VESTA,
                'chiron':  cls.CHIRON,
                'mean_node':  cls.MEAN_NODE,
                'true_node':  cls.TRUE_NODE,
                'south_node': cls.SOUTH_NODE,
                'part_of_fortune': cls.PART_OF_FORTUNE,
                'mean_lilith':     cls.MEAN_LILITH,
                'true_lilith':     cls.TRUE_LILITH,
                'mean_lilith':      cls.MEAN_LILITH,
                'true_lilith':      cls.TRUE_LILITH,
            },
            'meta': {
                'api_usage':  cls.INCLUDE_API_USAGE,
                'from_cache': cls.INCLUDE_FROM_CACHE,
            }
        }

    @classmethod
    def merge_onto(cls, base: dict, overrides: dict) -> dict:
        """
        Merge overrides onto an already-merged config dict (not onto class defaults).
        Used for the second merge step: user config + request overrides.

        Args:
            base:      Already-merged config dict (e.g. result of merge())
            overrides: Partial output config to apply on top

        Returns:
            New merged config dict
        """
        import copy
        result = copy.deepcopy(base)

        if not overrides:
            return result

        for key in ['geocentric', 'heliocentric', 'right_ascension', 'declination',
                    'longitude_speed', 'latitude_speed', 'declination_speed', 'retrograde']:
            if key in overrides and overrides[key] is not None:
                result[key] = bool(overrides[key])

        if 'default_house_system' in overrides and overrides['default_house_system'] is not None:
            result['default_house_system'] = overrides['default_house_system']

        for section in ['angles', 'bodies', 'meta']:
            if section in overrides and isinstance(overrides[section], dict):
                for key, val in overrides[section].items():
                    if val is not None and key in result.get(section, {}):
                        result[section][key] = bool(val)

        return result

    @classmethod
    def from_cfg(cls, cfg_dict: dict) -> dict:
        """
        Convert a flat .cfg-style dict (as parsed from configparser) into the
        nested JSON output config format stored in the database.

        The .cfg format uses dotted section names:
            [output]         → top-level keys
            [output.angles]  → nested under 'angles'
            [output.bodies]  → nested under 'bodies'
            [output.meta]    → nested under 'meta'

        Only keys that differ from server defaults are stored (sparse override).

        Args:
            cfg_dict: Dict with keys from [output], [output.angles],
                      [output.bodies], [output.meta] sections.

        Returns:
            Sparse dict of overrides suitable for storage in output_config column.
        """
        def _bool(val):
            if isinstance(val, bool):
                return val
            return str(val).lower() in ('true', '1', 'yes')

        defaults = cls.as_dict()
        result   = {}

        # Top-level fields
        top_keys = [
            'geocentric', 'heliocentric', 'right_ascension', 'declination',
            'longitude_speed', 'latitude_speed', 'declination_speed',
            'retrograde', 'default_house_system',
        ]
        for k in top_keys:
            if k in cfg_dict:
                val = cfg_dict[k]
                if k == 'default_house_system':
                    result[k] = val if val else None
                else:
                    result[k] = _bool(val)

        # Nested sections
        for section in ('angles', 'bodies', 'meta'):
            prefix = f'{section}.'
            section_keys = {
                k[len(prefix):]: v for k, v in cfg_dict.items()
                if k.startswith(prefix)
            }
            if section_keys:
                result.setdefault(section, {})
                for k, v in section_keys.items():
                    if k in defaults.get(section, {}):
                        result[section][k] = _bool(v)

        return result

    @classmethod
    def to_cfg_dict(cls, stored: dict) -> dict:
        """
        Convert a stored output_config dict back to the flat .cfg-style layout
        for display in forms. Returns the effective merged config as a flat
        dict with dotted section keys.
        """
        effective = cls.merge(stored or {})
        flat = {}
        for k in ['geocentric','heliocentric','right_ascension','declination',
                  'longitude_speed','latitude_speed','declination_speed',
                  'retrograde','default_house_system']:
            flat[k] = effective.get(k)
        for section in ('angles', 'bodies', 'meta'):
            for k, v in effective.get(section, {}).items():
                flat[f'{section}.{k}'] = v
        return flat


    @classmethod
    def merge(cls, overrides: dict) -> dict:
        """
        Merge per-request overrides onto the server defaults.
        Only keys present in overrides are changed — everything else
        retains its default value.

        Args:
            overrides: Partial output config from the request body

        Returns:
            Merged config dict
        """
        config = cls.as_dict()

        if not overrides:
            return config

        # Top-level boolean overrides
        for key in ['geocentric', 'heliocentric', 'right_ascension', 'declination',
                    'longitude_speed', 'latitude_speed', 'declination_speed', 'retrograde']:
            if key in overrides:
                config[key] = bool(overrides[key])

        # House system override
        if 'default_house_system' in overrides:
            config['default_house_system'] = overrides['default_house_system']

        # Nested angles overrides
        if 'angles' in overrides and isinstance(overrides['angles'], dict):
            for key in config['angles']:
                if key in overrides['angles']:
                    config['angles'][key] = bool(overrides['angles'][key])

        # Nested bodies overrides
        if 'bodies' in overrides and isinstance(overrides['bodies'], dict):
            for key in config['bodies']:
                if key in overrides['bodies']:
                    config['bodies'][key] = bool(overrides['bodies'][key])

        # Nested meta overrides
        if 'meta' in overrides and isinstance(overrides['meta'], dict):
            for key in config['meta']:
                if key in overrides['meta']:
                    config['meta'][key] = bool(overrides['meta'][key])

        return config