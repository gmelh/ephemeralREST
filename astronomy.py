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
# astronomy.py                                                                #
################################################################################

"""
Astronomical calculations module
Handles Swiss Ephemeris calculations for planetary positions
"""
import logging
import swisseph as swe
from datetime import datetime
from typing import Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)


class AstronomyService:
    """Handles astronomical calculations using Swiss Ephemeris"""

    # Standard planets
    PLANETS = {
        'sun':     swe.SUN,
        'moon':    swe.MOON,
        'mercury': swe.MERCURY,
        'venus':   swe.VENUS,
        'mars':    swe.MARS,
        'jupiter': swe.JUPITER,
        'saturn':  swe.SATURN,
        'uranus':  swe.URANUS,
        'neptune': swe.NEPTUNE,
        'pluto':   swe.PLUTO,
        'earth':   swe.EARTH,
    }

    # Asteroids (toggled as a group or individually via output config)
    ASTEROIDS = {
        'ceres':  swe.CERES,
        'pallas': swe.PALLAS,
        'juno':   swe.JUNO,
        'vesta':  swe.VESTA,
        'chiron': swe.CHIRON,
    }

    # Nodes
    NODES = {
        'mean_node': swe.MEAN_NODE,
        'true_node': swe.TRUE_NODE,
    }

    # Lunar apsides (Black Moon Lilith)
    LILITH = {
        'mean_lilith': swe.MEAN_APOG,   # Mean Black Moon Lilith
        'true_lilith': swe.OSCU_APOG,   # True / Oscillating Black Moon Lilith
    }

    # Planets that support perihelion/aphelion via swe.nod_aps_ut()
    # Earth is excluded — heliocentric only and rarely used for apsides
    APSIDE_PLANETS = [
        'mercury', 'venus', 'mars', 'jupiter',
        'saturn', 'uranus', 'neptune', 'pluto',
        'ceres', 'pallas', 'juno', 'vesta', 'chiron',
    ]

    # Supported house systems: display name -> Swiss Ephemeris code
    HOUSE_SYSTEMS = {
        'placidus':      b'P',
        'koch':          b'K',
        'porphyrius':    b'O',
        'regiomontanus': b'R',
        'campanus':      b'C',
        'equal':         b'A',
        'vehlow_equal':  b'V',
        'whole_sign':    b'W',
        'meridian':      b'X',
        'azimuthal':     b'H',
        'topocentric':   b'T',
        'alcabitus':     b'B',
        'morinus':       b'M',
        'krusinski':     b'U',
        'gauquelin':     b'G',
    }

    def __init__(self, ephemeris_path: str):
        self.ephemeris_path = ephemeris_path
        swe.set_ephe_path(ephemeris_path)
        logger.info(f"Swiss Ephemeris path set to: {ephemeris_path}")

    def calculate_planetary_positions(
            self,
            dt_utc: datetime,
            observer_lat: float = None,
            observer_lon: float = None,
            house_system: str = None,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate planetary positions using Swiss Ephemeris.

        Args:
            dt_utc:        Datetime in UTC
            observer_lat:  Observer latitude (optional)
            observer_lon:  Observer longitude (optional)
            house_system:  House system name (optional)
            output_config: Merged output config dict from OutputConfig.merge()

        Returns:
            Tuple of (result_dict, error_message)
            result_dict keys:
                planetary_positions:
                    geocentric:   dict of body_name -> position (if geocentric enabled)
                    heliocentric: dict of body_name -> position (if heliocentric enabled)
                house_cusps: list of house cusp longitudes, or None
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        cfg         = output_config
        bodies_cfg  = cfg.get('bodies', {})
        angles_cfg  = cfg.get('angles', {})

        try:
            # Julian day
            jd = swe.julday(
                dt_utc.year,
                dt_utc.month,
                dt_utc.day,
                dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
            )

            if observer_lat is not None and observer_lon is not None:
                swe.set_topo(observer_lon, observer_lat, 0)

            geocentric_positions  = {}
            heliocentric_positions = {}

            # Build the active body list from config
            all_bodies = self._get_active_bodies(bodies_cfg)

            for planet_name, planet_id in all_bodies.items():
                # Geocentric (skip Earth — heliocentric only)
                if cfg.get('geocentric', True):
                    if planet_name != 'earth':
                        geo_pos, _ = self._calculate_position(
                            jd, planet_id, planet_name,
                            heliocentric=False,
                            output_config=cfg
                        )
                        geocentric_positions[planet_name] = geo_pos
                    else:
                        geocentric_positions[planet_name] = None

                # Heliocentric (skip Sun, Moon, nodes)
                if cfg.get('heliocentric', True):
                    if planet_name not in ['sun', 'moon', 'mean_node', 'true_node']:
                        helio_pos, _ = self._calculate_position(
                            jd, planet_id, planet_name,
                            heliocentric=True,
                            output_config=cfg
                        )
                        heliocentric_positions[planet_name] = helio_pos
                    else:
                        heliocentric_positions[planet_name] = None

            # South Node (geocentric only — Mean Node + 180)
            if bodies_cfg.get('south_node', False) and cfg.get('geocentric', True):
                if 'mean_node' in geocentric_positions and geocentric_positions['mean_node']:
                    mn_lon = geocentric_positions['mean_node']['longitude']
                    geocentric_positions['south_node'] = self._derived_point(
                        (mn_lon + 180.0) % 360.0
                    )
                elif 'true_node' in geocentric_positions and geocentric_positions['true_node']:
                    tn_lon = geocentric_positions['true_node']['longitude']
                    geocentric_positions['south_node'] = self._derived_point(
                        (tn_lon + 180.0) % 360.0
                    )

            # Houses, ASC, MC
            house_cusps = None
            ascmc       = None

            if observer_lat is not None and observer_lon is not None:
                if house_system is not None:
                    house_cusps, ascmc = self._calculate_houses(
                        jd, observer_lat, observer_lon, house_system, angles_cfg
                    )
                else:
                    # No house system — ASC/MC only via Placidus
                    _, ascmc = swe.houses_ex(jd, observer_lat, observer_lon, b'P')

                if ascmc is not None:
                    if angles_cfg.get('asc', True) and cfg.get('geocentric', True):
                        geocentric_positions['asc'] = self._angle_position(ascmc[0])
                    if angles_cfg.get('mc', True) and cfg.get('geocentric', True):
                        geocentric_positions['mc'] = self._angle_position(ascmc[1])

                    # Part of Fortune: ASC + Moon - Sun (mod 360)
                    if bodies_cfg.get('part_of_fortune', False) and cfg.get('geocentric', True):
                        asc_lon  = ascmc[0]
                        moon_lon = geocentric_positions.get('moon', {})
                        sun_lon  = geocentric_positions.get('sun', {})
                        if moon_lon and sun_lon:
                            pof = (asc_lon + moon_lon['longitude'] - sun_lon['longitude']) % 360.0
                            geocentric_positions['part_of_fortune'] = self._derived_point(pof)

            else:
                if angles_cfg.get('asc', True) and cfg.get('geocentric', True):
                    geocentric_positions['asc'] = None
                if angles_cfg.get('mc', True) and cfg.get('geocentric', True):
                    geocentric_positions['mc'] = None

            # ASC/MC not applicable heliocentric
            if cfg.get('heliocentric', True):
                heliocentric_positions['asc'] = None
                heliocentric_positions['mc']  = None

            planetary_positions = {}
            if cfg.get('geocentric', True):
                planetary_positions['geocentric']  = geocentric_positions
            if cfg.get('heliocentric', True):
                planetary_positions['heliocentric'] = heliocentric_positions

            result = {
                'planetary_positions': planetary_positions,
                'house_cusps':         house_cusps,
            }

            return result, None

        except Exception as e:
            logger.error(f"Swiss Ephemeris calculation error: {str(e)}")
            return None, f"Planetary calculation failed: {str(e)}"

    def _get_active_bodies(self, bodies_cfg: dict) -> dict:
        """
        Build an ordered dict of planet_name -> swe_id based on output config.
        """
        active = {}
        asteroids_on = bodies_cfg.get('asteroids', True)

        for name, swe_id in self.PLANETS.items():
            if bodies_cfg.get(name, True):
                active[name] = swe_id

        for name, swe_id in self.ASTEROIDS.items():
            if asteroids_on and bodies_cfg.get(name, True):
                active[name] = swe_id

        for name, swe_id in self.NODES.items():
            if bodies_cfg.get(name, name == 'mean_node'):  # mean_node on, true_node off by default
                active[name] = swe_id

        for name, swe_id in self.LILITH.items():
            if bodies_cfg.get(name, False):  # both off by default
                active[name] = swe_id

        return active

    def _calculate_position(
            self,
            jd: float,
            planet_id: int,
            planet_name: str,
            heliocentric: bool = False,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate ecliptic and equatorial position for a single planet.

        Args:
            jd:            Julian day number
            planet_id:     Swiss Ephemeris planet ID
            planet_name:   Name of the planet
            heliocentric:  If True, calculate heliocentric position
            output_config: Output config dict

        Returns:
            Tuple of (position_dict, error_message)
        """
        if output_config is None:
            output_config = {}

        try:
            base_flags = swe.FLG_SWIEPH
            if heliocentric:
                base_flags |= swe.FLG_HELCTR

            # Ecliptic coordinates
            ecl, _ = swe.calc_ut(jd, planet_id, base_flags)

            # Equatorial coordinates (only if requested)
            need_equatorial = (
                output_config.get('right_ascension', True) or
                output_config.get('declination', True) or
                output_config.get('declination_speed', True)
            )
            equ = None
            if need_equatorial:
                equ, _ = swe.calc_ut(jd, planet_id, base_flags | swe.FLG_EQUATORIAL)

            longitude_speed = ecl[3] if len(ecl) > 3 else None

            pos = {}

            # Ecliptic
            pos['longitude']   = ecl[0]
            pos['latitude']    = ecl[1]
            pos['distance_au'] = ecl[2]

            if output_config.get('longitude_speed', True):
                pos['longitude_speed'] = longitude_speed
            if output_config.get('latitude_speed', True):
                pos['latitude_speed'] = ecl[4] if len(ecl) > 4 else None

            # Retrograde (geocentric only)
            if not heliocentric and output_config.get('retrograde', True):
                pos['retrograde'] = (longitude_speed < 0) if longitude_speed is not None else None

            # Equatorial
            if equ is not None:
                if output_config.get('right_ascension', True):
                    pos['right_ascension'] = equ[0]
                if output_config.get('declination', True):
                    pos['declination'] = equ[1]
                if output_config.get('declination_speed', True):
                    pos['declination_speed'] = equ[4] if len(equ) > 4 else None

            return pos, None

        except Exception as e:
            position_type = "heliocentric" if heliocentric else "geocentric"
            logger.warning(f"{position_type.capitalize()} calculation failed for {planet_name}: {str(e)}")
            return None, str(e)

    def _calculate_houses(
            self,
            jd: float,
            observer_lat: float,
            observer_lon: float,
            house_system: str,
            angles_cfg: dict = None
    ) -> Tuple[Dict[str, Any], tuple]:
        """
        Calculate house cusps for the given house system.

        Args:
            jd:           Julian day number
            observer_lat: Observer latitude
            observer_lon: Observer longitude
            house_system: House system name
            angles_cfg:   Angles section of output config

        Returns:
            Tuple of (house_cusps_dict, ascmc_tuple)
        """
        if angles_cfg is None:
            angles_cfg = {}

        system_code = self.HOUSE_SYSTEMS.get(house_system, b'P')
        houses, ascmc = swe.houses_ex(jd, observer_lat, observer_lon, system_code)

        cusp_count = 36 if house_system == 'gauquelin' else 12

        house_cusps = {
            'system': house_system,
            'cusps':  {str(i + 1): houses[i] for i in range(cusp_count)},
            'asc':    ascmc[0],
            'mc':     ascmc[1],
        }

        if angles_cfg.get('armc', False):
            house_cusps['armc']       = ascmc[2]
        if angles_cfg.get('vertex', True):
            house_cusps['vertex']     = ascmc[3]
        if angles_cfg.get('east_point', True):
            house_cusps['east_point'] = ascmc[4]

        logger.info(f"House cusps calculated using {house_system} system")
        return house_cusps, ascmc

    def _angle_position(self, longitude: float) -> Dict[str, Any]:
        """Position dict for an angle (ASC, MC) — no speed or equatorial data."""
        return {
            'longitude':         longitude,
            'latitude':          0.0,
            'distance_au':       None,
            'longitude_speed':   None,
            'latitude_speed':    None,
            'retrograde':        None,
            'right_ascension':   None,
            'declination':       None,
            'declination_speed': None,
        }

    def _derived_point(self, longitude: float) -> Dict[str, Any]:
        """Position dict for a derived point (South Node, Part of Fortune)."""
        return {
            'longitude':         longitude,
            'latitude':          0.0,
            'distance_au':       None,
            'longitude_speed':   None,
            'latitude_speed':    None,
            'retrograde':        None,
            'right_ascension':   None,
            'declination':       None,
            'declination_speed': None,
        }

    # ==========================================================================
    # Secondary Progressions
    # ==========================================================================

    def calculate_secondary_progressions(
            self,
            natal_dt_utc: datetime,
            progression_date: datetime,
            observer_lat: float = None,
            observer_lon: float = None,
            house_system: str = None,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate secondary progressions using the day-for-a-year method.

        Each day after birth corresponds to one year of life.
        Progressed JD = natal JD + days elapsed since birth.
        Calculation is performed at noon (12:00 UT) on the progressed day.

        Args:
            natal_dt_utc:     Natal datetime in UTC
            progression_date: The date to progress to (time is set to noon UT)
            observer_lat:     Observer latitude for progressed ASC/MC
            observer_lon:     Observer longitude for progressed ASC/MC
            house_system:     House system for progressed cusps
            output_config:    Merged output config dict

        Returns:
            Tuple of (result_dict, error_message)
            result_dict keys: planetary_positions (geocentric, heliocentric), house_cusps,
                              progressed_jd, natal_jd, days_elapsed
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        try:
            # Natal Julian day
            natal_jd = swe.julday(
                natal_dt_utc.year,
                natal_dt_utc.month,
                natal_dt_utc.day,
                natal_dt_utc.hour + natal_dt_utc.minute / 60.0 + natal_dt_utc.second / 3600.0
            )

            # Progressed Julian day — day-for-a-year at noon UT
            # Days elapsed = number of days between natal date and progression date
            # Strip timezone info from both before subtracting to avoid
            # offset-naive vs offset-aware comparison errors
            natal_date_only  = natal_dt_utc.replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            prog_date_only   = progression_date.replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            days_elapsed     = (prog_date_only - natal_date_only).days
            progressed_jd    = natal_jd + days_elapsed + 0.5  # noon UT

            logger.info(
                f"Secondary progressions: natal={natal_dt_utc.date()}, "
                f"target={progression_date.date()}, "
                f"days_elapsed={days_elapsed}, progressed_jd={progressed_jd:.4f}"
            )

            # Calculate positions at progressed JD — same flow as natal
            # Convert progressed JD back to a naive UTC datetime for calculation
            prog_year, prog_month, prog_day, prog_hour = swe.revjul(progressed_jd)
            prog_hour_int = int(prog_hour)
            prog_min_int  = int((prog_hour - prog_hour_int) * 60)
            prog_sec_int  = int(((prog_hour - prog_hour_int) * 60 - prog_min_int) * 60)
            progressed_dt = datetime(prog_year, prog_month, prog_day,
                                     prog_hour_int, prog_min_int, prog_sec_int)

            result, error = self.calculate_planetary_positions(
                progressed_dt,
                observer_lat,
                observer_lon,
                house_system=house_system,
                output_config=output_config
            )

            if error:
                return None, error

            result['progressed_jd']  = progressed_jd
            result['natal_jd']       = natal_jd
            result['days_elapsed']   = days_elapsed
            result['method']         = 'secondary_progressions'

            return result, None

        except Exception as e:
            logger.error(f"Secondary progressions error: {str(e)}")
            return None, f"Secondary progressions failed: {str(e)}"

    # ==========================================================================
    # Solar Arc Directions
    # ==========================================================================

    def calculate_solar_arc_directions(
            self,
            natal_positions: Dict[str, Any],
            natal_dt_utc: datetime,
            progression_date: datetime,
            observer_lat: float = None,
            observer_lon: float = None,
            house_system: str = None,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate solar arc directions.

        Solar arc = progressed Sun longitude - natal Sun longitude.
        Every natal planet is advanced by this same arc.

        For heliocentric: arc = progressed Earth longitude - natal Earth longitude.

        Progressed Sun/Earth is calculated at noon UT on the progressed JD
        (natal JD + days elapsed), matching the secondary progression method.

        Args:
            natal_positions:  The natal chart_data dict (geocentric/heliocentric)
            natal_dt_utc:     Natal datetime in UTC
            progression_date: The date to direct to (time set to noon UT)
            observer_lat:     Observer latitude for directed ASC/MC
            observer_lon:     Observer longitude for directed ASC/MC
            house_system:     House system for directed cusps
            output_config:    Merged output config dict

        Returns:
            Tuple of (result_dict, error_message)
            result_dict keys: planetary_positions (geocentric, heliocentric), house_cusps,
                              solar_arc_geo, solar_arc_helio,
                              progressed_jd, natal_jd, days_elapsed, method
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        cfg        = output_config
        angles_cfg = cfg.get('angles', {})

        try:
            # Julian days
            natal_jd = swe.julday(
                natal_dt_utc.year,
                natal_dt_utc.month,
                natal_dt_utc.day,
                natal_dt_utc.hour + natal_dt_utc.minute / 60.0 + natal_dt_utc.second / 3600.0
            )

            # Strip timezone info before subtracting
            natal_date_only = natal_dt_utc.replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            prog_date_only  = progression_date.replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            days_elapsed    = (prog_date_only - natal_date_only).days
            progressed_jd   = natal_jd + days_elapsed + 0.5  # noon UT

            # --- Geocentric solar arc ---
            geo_arc = None
            directed_geo = {}

            if cfg.get('geocentric', True):
                natal_geo = natal_positions.get('geocentric', {})

                # Progressed Sun position
                prog_sun, _ = swe.calc_ut(progressed_jd, swe.SUN, swe.FLG_SWIEPH)
                prog_sun_lon = prog_sun[0]

                natal_sun = natal_geo.get('sun', {})
                if natal_sun:
                    natal_sun_lon = natal_sun['longitude']
                    geo_arc = (prog_sun_lon - natal_sun_lon) % 360.0

                    logger.info(
                        f"Solar arc (geo): natal Sun={natal_sun_lon:.4f}, "
                        f"progressed Sun={prog_sun_lon:.4f}, arc={geo_arc:.4f}"
                    )

                    # Apply arc to every natal geocentric position
                    for body_name, natal_pos in natal_geo.items():
                        if natal_pos is None:
                            directed_geo[body_name] = None
                            continue
                        if body_name in ('asc', 'mc'):
                            # Apply arc to angles
                            directed_lon = (natal_pos['longitude'] + geo_arc) % 360.0
                            directed_geo[body_name] = self._angle_position(directed_lon)
                        else:
                            directed_lon = (natal_pos['longitude'] + geo_arc) % 360.0
                            directed_geo[body_name] = self._directed_position(
                                directed_lon, natal_pos, geo_arc, output_config=cfg
                            )

                    # Directed house cusps
                    if house_system and observer_lat is not None and observer_lon is not None:
                        # Use progressed ARMC-based cusps where possible,
                        # or simply apply arc to natal cusps
                        directed_cusps, ascmc = self._calculate_houses(
                            progressed_jd, observer_lat, observer_lon,
                            house_system, angles_cfg
                        )
                        directed_cusps['system'] = house_system
                    elif observer_lat is not None and observer_lon is not None:
                        _, ascmc = swe.houses_ex(progressed_jd, observer_lat, observer_lon, b'P')
                        directed_cusps = None
                        directed_geo['asc'] = self._angle_position(ascmc[0])
                        directed_geo['mc']  = self._angle_position(ascmc[1])
                    else:
                        directed_cusps = None

            # --- Heliocentric solar arc ---
            helio_arc = None
            directed_helio = {}

            if cfg.get('heliocentric', True):
                natal_helio = natal_positions.get('heliocentric', {})

                # Progressed Earth position (helio equivalent of Sun)
                prog_earth, _ = swe.calc_ut(
                    progressed_jd, swe.EARTH, swe.FLG_SWIEPH | swe.FLG_HELCTR
                )
                prog_earth_lon = prog_earth[0]

                natal_earth = natal_helio.get('earth', {})
                if natal_earth:
                    natal_earth_lon = natal_earth['longitude']
                    helio_arc = (prog_earth_lon - natal_earth_lon) % 360.0

                    logger.info(
                        f"Solar arc (helio): natal Earth={natal_earth_lon:.4f}, "
                        f"progressed Earth={prog_earth_lon:.4f}, arc={helio_arc:.4f}"
                    )

                    for body_name, natal_pos in natal_helio.items():
                        if natal_pos is None:
                            directed_helio[body_name] = None
                            continue
                        directed_lon = (natal_pos['longitude'] + helio_arc) % 360.0
                        directed_helio[body_name] = self._directed_position(
                            directed_lon, natal_pos, helio_arc, output_config=cfg
                        )

            result = {
                'method':          'solar_arc_directions',
                'solar_arc_geo':   round(geo_arc, 6)   if geo_arc   is not None else None,
                'solar_arc_helio': round(helio_arc, 6) if helio_arc is not None else None,
                'progressed_jd':   progressed_jd,
                'natal_jd':        natal_jd,
                'days_elapsed':    days_elapsed,
                'house_cusps':     directed_cusps if cfg.get('geocentric', True) else None,
            }

            if cfg.get('geocentric', True):
                result['geocentric']   = directed_geo
            if cfg.get('heliocentric', True):
                result['heliocentric'] = directed_helio

            return result, None

        except Exception as e:
            logger.error(f"Solar arc directions error: {str(e)}")
            return None, f"Solar arc directions failed: {str(e)}"

    def _directed_position(
            self,
            directed_longitude: float,
            natal_pos: Dict[str, Any],
            arc: float,
            output_config: dict = None
    ) -> Dict[str, Any]:
        """
        Build a position dict for a solar arc directed planet.
        Longitude is advanced by the arc; all other values carry forward
        from the natal position since arc direction only moves longitude.
        Field inclusion is controlled by output_config — matches /calculate output.
        Speed and retrograde are always None for directed positions.
        """
        if output_config is None:
            output_config = {}

        pos = {
            'longitude':   directed_longitude,
            'latitude':    natal_pos.get('latitude'),
            'distance_au': natal_pos.get('distance_au'),
        }

        # Speeds and retrograde are not meaningful for directed positions
        if output_config.get('longitude_speed', True):
            pos['longitude_speed'] = None
        if output_config.get('latitude_speed', True):
            pos['latitude_speed'] = None
        if output_config.get('retrograde', True):
            pos['retrograde'] = None

        # Equatorial — carry natal values forward only if enabled in config
        if output_config.get('right_ascension', True):
            pos['right_ascension'] = natal_pos.get('right_ascension')
        if output_config.get('declination', True):
            pos['declination'] = natal_pos.get('declination')
        if output_config.get('declination_speed', True):
            pos['declination_speed'] = None  # not meaningful for directed positions

        return pos

    # ==========================================================================
    # Solar and Lunar Returns
    # ==========================================================================

    def calculate_solar_return(
            self,
            natal_dt_utc: datetime,
            return_year: int,
            observer_lat: float = None,
            observer_lon: float = None,
            house_system: str = None,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate the solar return chart for a given year.

        Finds the exact moment the Sun returns to its natal longitude
        in the given year using Newton's method iteration on the ephemeris.
        The return chart is cast for that exact moment at the observer location.

        Args:
            natal_dt_utc:  Natal datetime in UTC
            return_year:   The year of the solar return to calculate
            observer_lat:  Observer latitude (current residence)
            observer_lon:  Observer longitude (current residence)
            house_system:  House system for return chart cusps
            output_config: Merged output config dict

        Returns:
            Tuple of (result_dict, error_message)
            result_dict includes: all standard position fields plus
                                  return_jd, natal_sun_longitude, return_datetime_utc
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        try:
            # Get natal Sun longitude
            natal_jd = swe.julday(
                natal_dt_utc.year,
                natal_dt_utc.month,
                natal_dt_utc.day,
                natal_dt_utc.hour + natal_dt_utc.minute / 60.0 + natal_dt_utc.second / 3600.0
            )
            natal_sun, _ = swe.calc_ut(natal_jd, swe.SUN, swe.FLG_SWIEPH)
            natal_sun_lon = natal_sun[0]

            # Find return JD using Newton's method
            return_jd, error = self._find_return_jd(
                natal_sun_lon, swe.SUN, return_year, heliocentric=False
            )
            if error:
                return None, error

            # Convert return JD to datetime
            return_dt = self._jd_to_datetime(return_jd)

            logger.info(
                f"Solar return: natal Sun={natal_sun_lon:.4f}°, "
                f"year={return_year}, return_jd={return_jd:.6f}, "
                f"return_dt={return_dt}"
            )

            # Calculate full chart at return moment
            result, error = self.calculate_planetary_positions(
                return_dt,
                observer_lat,
                observer_lon,
                house_system=house_system,
                output_config=output_config
            )
            if error:
                return None, error

            result['return_jd']             = return_jd
            result['natal_sun_longitude']   = natal_sun_lon
            result['return_datetime_utc']   = return_dt.isoformat()
            result['method']                = 'solar_return'
            result['return_year']           = return_year

            return result, None

        except Exception as e:
            logger.error(f"Solar return error: {str(e)}")
            return None, f"Solar return failed: {str(e)}"

    def calculate_lunar_return(
            self,
            natal_dt_utc: datetime,
            return_year: int,
            return_month: int,
            observer_lat: float = None,
            observer_lon: float = None,
            house_system: str = None,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate the lunar return chart for a given month.

        Finds the exact moment the Moon returns to its natal longitude
        in the given month using Newton's method iteration.

        Args:
            natal_dt_utc:  Natal datetime in UTC
            return_year:   Year of the lunar return
            return_month:  Month of the lunar return (1-12)
            observer_lat:  Observer latitude
            observer_lon:  Observer longitude
            house_system:  House system for return chart cusps
            output_config: Merged output config dict

        Returns:
            Tuple of (result_dict, error_message)
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        try:
            # Get natal Moon longitude
            natal_jd = swe.julday(
                natal_dt_utc.year,
                natal_dt_utc.month,
                natal_dt_utc.day,
                natal_dt_utc.hour + natal_dt_utc.minute / 60.0 + natal_dt_utc.second / 3600.0
            )
            natal_moon, _ = swe.calc_ut(natal_jd, swe.MOON, swe.FLG_SWIEPH)
            natal_moon_lon = natal_moon[0]

            # Find return JD
            return_jd, error = self._find_return_jd(
                natal_moon_lon, swe.MOON, return_year, return_month, heliocentric=False
            )
            if error:
                return None, error

            return_dt = self._jd_to_datetime(return_jd)

            logger.info(
                f"Lunar return: natal Moon={natal_moon_lon:.4f}°, "
                f"{return_year}-{return_month:02d}, return_jd={return_jd:.6f}, "
                f"return_dt={return_dt}"
            )

            # Calculate full chart at return moment
            result, error = self.calculate_planetary_positions(
                return_dt,
                observer_lat,
                observer_lon,
                house_system=house_system,
                output_config=output_config
            )
            if error:
                return None, error

            result['return_jd']              = return_jd
            result['natal_moon_longitude']   = natal_moon_lon
            result['return_datetime_utc']    = return_dt.isoformat()
            result['method']                 = 'lunar_return'
            result['return_year']            = return_year
            result['return_month']           = return_month

            return result, None

        except Exception as e:
            logger.error(f"Lunar return error: {str(e)}")
            return None, f"Lunar return failed: {str(e)}"

    # ==========================================================================
    # Return calculation helpers
    # ==========================================================================

    def _find_return_jd(
            self,
            target_longitude: float,
            planet_id: int,
            year: int,
            month: int = None,
            heliocentric: bool = False,
            max_iterations: int = 50,
            tolerance: float = 0.000001
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Find the exact Julian day when a planet reaches target_longitude
        in the given year (and optionally month) using Newton's method.

        For solar returns: searches the full year starting Jan 1.
        For lunar returns: searches a 30-day window starting from the
                           1st of the given month.

        Args:
            target_longitude: The longitude to find the return for (degrees)
            planet_id:        Swiss Ephemeris planet ID
            year:             Year to search in
            month:            Month to search in (lunar returns)
            heliocentric:     Use heliocentric positions
            max_iterations:   Newton's method iteration limit
            tolerance:        Convergence tolerance in degrees

        Returns:
            Tuple of (julian_day, error_message)
        """
        flags = swe.FLG_SWIEPH
        if heliocentric:
            flags |= swe.FLG_HELCTR

        # Starting JD — beginning of the year or month
        if month:
            start_jd = swe.julday(year, month, 1, 0.0)
            search_days = 32  # slightly over a month
        else:
            start_jd = swe.julday(year, 1, 1, 0.0)
            search_days = 370  # slightly over a year

        try:
            # Scan forward in coarse steps to find a bracket
            # where the planet crosses the target longitude
            step = 1.0 if planet_id == swe.MOON else 5.0
            jd   = start_jd
            prev_lon = None
            bracket_jd = None

            for _ in range(int(search_days / step) + 2):
                pos, _ = swe.calc_ut(jd, planet_id, flags)
                lon = pos[0]

                if prev_lon is not None:
                    # Detect crossing — account for 360° wrap
                    diff_prev   = (target_longitude - prev_lon) % 360.0
                    diff_curr   = (target_longitude - lon) % 360.0

                    if diff_prev <= 180.0 and diff_curr > 180.0:
                        # Planet just passed the target
                        bracket_jd = jd - step
                        break

                prev_lon = lon
                jd += step

            if bracket_jd is None:
                return None, (
                    f"Could not find return for longitude {target_longitude:.4f}° "
                    f"in {'month ' + str(month) + ' of ' if month else ''}year {year}"
                )

            # Newton's method refinement
            jd = bracket_jd
            for _ in range(max_iterations):
                pos, _  = swe.calc_ut(jd, planet_id, flags)
                lon     = pos[0]
                speed   = pos[3]  # degrees per day

                if abs(speed) < 0.0001:
                    return None, f"Planet speed near zero — cannot converge"

                # Angular difference accounting for wrap
                diff = target_longitude - lon
                if diff > 180.0:
                    diff -= 360.0
                elif diff < -180.0:
                    diff += 360.0

                if abs(diff) < tolerance:
                    return jd, None

                jd += diff / speed

            # Final check after max iterations
            pos, _ = swe.calc_ut(jd, planet_id, flags)
            diff   = abs(target_longitude - pos[0])
            if diff > 180.0:
                diff = 360.0 - diff
            if diff < 0.001:
                return jd, None

            return None, f"Newton's method did not converge (final diff={diff:.6f}°)"

        except Exception as e:
            return None, f"Return search failed: {str(e)}"

    def _jd_to_datetime(self, jd: float) -> datetime:
        """Convert a Julian day number to a naive UTC datetime."""
        year, month, day, hour = swe.revjul(jd)
        hour_int = int(hour)
        min_int  = int((hour - hour_int) * 60)
        sec_int  = int(((hour - hour_int) * 60 - min_int) * 60)
        return datetime(year, month, day, hour_int, min_int, sec_int)

    # ==========================================================================
    # Apsides
    # ==========================================================================

    def calculate_apsides(
            self,
            dt_utc: datetime,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate lunar and planetary apsides for a given datetime.

        Lunar apsides:
            perigee       — Moon's closest point to Earth (geocentric)
            apogee        — Moon's furthest point from Earth (geocentric)
            mean_lilith   — Mean Black Moon Lilith (swe.MEAN_APOG)
            true_lilith   — True/Oscillating Black Moon Lilith (swe.OSCU_APOG)

        Planetary apsides (heliocentric):
            perihelion    — planet's closest point to the Sun
            aphelion      — planet's furthest point from the Sun

        Args:
            dt_utc:        Datetime in UTC
            output_config: Merged output config (controls which bodies to include)

        Returns:
            Tuple of (result_dict, error_message)
        """
        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        bodies_cfg = output_config.get('bodies', {})

        try:
            jd = swe.julday(
                dt_utc.year, dt_utc.month, dt_utc.day,
                dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
            )

            # ------------------------------------------------------------------
            # Lunar apsides — perigee and apogee via swe.nod_aps_ut()
            # Method 0 = mean, Method 1 = osculating
            # ------------------------------------------------------------------
            lunar_apsides = {}

            # Perigee and apogee (osculating)
            _, aps_osc, _, _ = swe.nod_aps_ut(jd, swe.MOON, swe.FLG_SWIEPH, 1)
            _, aps_mean, _, _ = swe.nod_aps_ut(jd, swe.MOON, swe.FLG_SWIEPH, 0)

            lunar_apsides['perigee'] = {
                'longitude': aps_osc[0],   # periapsis
                'latitude':  0.0,
                'distance_au': aps_osc[2] if len(aps_osc) > 2 else None,
            }
            lunar_apsides['apogee'] = {
                'longitude': aps_osc[1],   # apoapsis
                'latitude':  0.0,
                'distance_au': aps_osc[3] if len(aps_osc) > 3 else None,
            }

            # Mean Lilith
            if bodies_cfg.get('mean_lilith', False):
                pos, _ = swe.calc_ut(jd, swe.MEAN_APOG, swe.FLG_SWIEPH)
                lunar_apsides['mean_lilith'] = {
                    'longitude':       pos[0],
                    'latitude':        pos[1],
                    'distance_au':     pos[2],
                    'longitude_speed': pos[3] if len(pos) > 3 else None,
                }

            # True Lilith
            if bodies_cfg.get('true_lilith', False):
                pos, _ = swe.calc_ut(jd, swe.OSCU_APOG, swe.FLG_SWIEPH)
                lunar_apsides['true_lilith'] = {
                    'longitude':       pos[0],
                    'latitude':        pos[1],
                    'distance_au':     pos[2],
                    'longitude_speed': pos[3] if len(pos) > 3 else None,
                }

            # ------------------------------------------------------------------
            # Planetary apsides — perihelion and aphelion via nod_aps_ut()
            # Uses osculating method (method=1) for all planets
            # ------------------------------------------------------------------
            planetary_apsides = {}

            planet_ids = {
                'mercury': swe.MERCURY, 'venus':   swe.VENUS,
                'mars':    swe.MARS,    'jupiter': swe.JUPITER,
                'saturn':  swe.SATURN,  'uranus':  swe.URANUS,
                'neptune': swe.NEPTUNE, 'pluto':   swe.PLUTO,
                'ceres':   swe.CERES,   'pallas':  swe.PALLAS,
                'juno':    swe.JUNO,    'vesta':   swe.VESTA,
                'chiron':  swe.CHIRON,
            }

            for planet_name, planet_id in planet_ids.items():
                # Respect bodies config — skip disabled bodies
                if not bodies_cfg.get(planet_name, True):
                    continue
                # Skip asteroids if master switch is off
                if planet_name in ('ceres', 'pallas', 'juno', 'vesta', 'chiron'):
                    if not bodies_cfg.get('asteroids', True):
                        continue

                try:
                    _, aps, _, _ = swe.nod_aps_ut(
                        jd, planet_id, swe.FLG_SWIEPH | swe.FLG_HELCTR, 1
                    )
                    planetary_apsides[planet_name] = {
                        'perihelion': {
                            'longitude':   aps[0],
                            'distance_au': aps[2] if len(aps) > 2 else None,
                        },
                        'aphelion': {
                            'longitude':   aps[1],
                            'distance_au': aps[3] if len(aps) > 3 else None,
                        },
                    }
                except Exception as e:
                    logger.warning(f"Apsides failed for {planet_name}: {e}")
                    planetary_apsides[planet_name] = None

            return {
                'lunar_apsides':     lunar_apsides,
                'planetary_apsides': planetary_apsides,
                'datetime_utc':      dt_utc.isoformat(),
                'julian_day':        jd,
            }, None

        except Exception as e:
            logger.error(f"Apsides calculation error: {str(e)}")
            return None, f"Apsides calculation failed: {str(e)}"

    # ==========================================================================
    # Lunations
    # ==========================================================================

    # Sun-Moon angles for each lunation phase
    LUNATION_PHASES = {
        'new_moon':      0.0,
        'first_quarter': 90.0,
        'full_moon':     180.0,
        'last_quarter':  270.0,
    }

    def find_lunations(
            self,
            reference_date: datetime,
            direction: str = 'next',
            start_date: datetime = None,
            end_date: datetime = None,
            phases: list = None,
    ) -> Tuple[Optional[list], Optional[str]]:
        """
        Find lunation events (New Moon, Full Moon, quarters).

        Two modes:
            next/previous: find the next or previous occurrence of each
                           requested phase from reference_date. Can return
                           both directions by passing direction='both'.
            range:         find all occurrences within [start_date, end_date].

        Args:
            reference_date: The date to search from
            direction:      'next', 'previous', or 'both' (ignored if range given)
            start_date:     Range start (if provided with end_date, uses range mode)
            end_date:       Range end
            phases:         List of phase names to include, default all four

        Returns:
            Tuple of (lunation_list, error_message)
            Each lunation: {phase, datetime_utc, julian_day, sun_longitude,
                            moon_longitude, sun_moon_angle}
        """
        if phases is None:
            phases = list(self.LUNATION_PHASES.keys())

        # Validate requested phases
        invalid = [p for p in phases if p not in self.LUNATION_PHASES]
        if invalid:
            return None, f"Invalid phases: {invalid}. Valid: {list(self.LUNATION_PHASES.keys())}"

        try:
            # --- Range mode ---
            if start_date and end_date:
                return self._find_lunations_in_range(start_date, end_date, phases)

            # --- Next / previous / both ---
            results = []
            ref_jd = swe.julday(
                reference_date.year, reference_date.month, reference_date.day,
                reference_date.hour + reference_date.minute / 60.0
            )

            directions = []
            if direction == 'both':
                directions = ['next', 'previous']
            else:
                directions = [direction]

            for d in directions:
                for phase in phases:
                    target_angle = self.LUNATION_PHASES[phase]
                    lunation_jd, error = self._find_lunation_jd(
                        ref_jd, target_angle, forward=(d == 'next')
                    )
                    if error:
                        logger.warning(f"Could not find {phase} ({d}): {error}")
                        continue

                    lunation = self._build_lunation(lunation_jd, phase, d)
                    results.append(lunation)

            # Sort by datetime
            results.sort(key=lambda x: x['julian_day'])
            return results, None

        except Exception as e:
            logger.error(f"Lunation search error: {str(e)}")
            return None, f"Lunation search failed: {str(e)}"

    def _find_lunations_in_range(
            self,
            start_date: datetime,
            end_date: datetime,
            phases: list
    ) -> Tuple[Optional[list], Optional[str]]:
        """Find all lunations within a date range."""
        try:
            start_jd = swe.julday(
                start_date.year, start_date.month, start_date.day, 0.0
            )
            end_jd = swe.julday(
                end_date.year, end_date.month, end_date.day, 23.9999
            )

            results = []
            # Step through in ~7 day intervals (lunation spacing ~7.4 days per quarter)
            step     = 6.0
            scan_jd  = start_jd

            while scan_jd <= end_jd:
                sun_pos,  _ = swe.calc_ut(scan_jd, swe.SUN,  swe.FLG_SWIEPH)
                moon_pos, _ = swe.calc_ut(scan_jd, swe.MOON, swe.FLG_SWIEPH)
                angle = (moon_pos[0] - sun_pos[0]) % 360.0

                next_jd = scan_jd + step
                if next_jd > end_jd:
                    next_jd = end_jd

                sun_next,  _ = swe.calc_ut(next_jd, swe.SUN,  swe.FLG_SWIEPH)
                moon_next, _ = swe.calc_ut(next_jd, swe.MOON, swe.FLG_SWIEPH)
                angle_next = (moon_next[0] - sun_next[0]) % 360.0

                for phase in phases:
                    target = self.LUNATION_PHASES[phase]
                    # Detect crossing of target angle in this interval
                    diff_curr = (target - angle) % 360.0
                    diff_next = (target - angle_next) % 360.0
                    if diff_curr <= 180.0 and diff_next > 180.0:
                        # Refine with Newton's method
                        lunation_jd, error = self._find_lunation_jd(
                            scan_jd, target, forward=True
                        )
                        if not error and start_jd <= lunation_jd <= end_jd:
                            lunation = self._build_lunation(lunation_jd, phase, 'range')
                            # Avoid duplicates
                            if not any(abs(r['julian_day'] - lunation_jd) < 0.01 for r in results):
                                results.append(lunation)

                scan_jd += step

            results.sort(key=lambda x: x['julian_day'])
            return results, None

        except Exception as e:
            return None, f"Range lunation search failed: {str(e)}"

    def _find_lunation_jd(
            self,
            ref_jd: float,
            target_angle: float,
            forward: bool = True,
            max_iterations: int = 50,
            tolerance: float = 0.000001
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Find the exact JD when Moon - Sun angle equals target_angle.
        Uses coarse scan then Newton's method refinement.
        Moon moves ~12°/day relative to Sun so a 6-day step covers ~72°.
        """
        step = 6.0 if forward else -6.0
        jd   = ref_jd + (step * 0.1)  # start just past reference

        # Coarse scan to bracket
        bracket_jd = None
        for _ in range(60):  # max 60 steps = ~360 days forward or back
            sun,  _ = swe.calc_ut(jd,          swe.SUN,  swe.FLG_SWIEPH)
            sun2, _ = swe.calc_ut(jd + step,   swe.SUN,  swe.FLG_SWIEPH)
            moon, _ = swe.calc_ut(jd,          swe.MOON, swe.FLG_SWIEPH)
            moon2,_ = swe.calc_ut(jd + step,   swe.MOON, swe.FLG_SWIEPH)

            angle      = (moon[0]  - sun[0])  % 360.0
            angle_next = (moon2[0] - sun2[0]) % 360.0

            diff_curr = (target_angle - angle)      % 360.0
            diff_next = (target_angle - angle_next) % 360.0

            if forward:
                if diff_curr <= 180.0 and diff_next > 180.0:
                    bracket_jd = jd
                    break
            else:
                if diff_curr > 180.0 and diff_next <= 180.0:
                    bracket_jd = jd
                    break

            jd += step

        if bracket_jd is None:
            return None, f"Could not bracket lunation angle {target_angle}°"

        # Newton's method refinement
        jd = bracket_jd
        for _ in range(max_iterations):
            sun,  _ = swe.calc_ut(jd, swe.SUN,  swe.FLG_SWIEPH)
            moon, _ = swe.calc_ut(jd, swe.MOON, swe.FLG_SWIEPH)

            angle = (moon[0] - sun[0]) % 360.0
            diff  = target_angle - angle
            if diff > 180.0:
                diff -= 360.0
            elif diff < -180.0:
                diff += 360.0

            if abs(diff) < tolerance:
                return jd, None

            # Relative speed: Moon ~13.2°/day, Sun ~1°/day
            rel_speed = moon[3] - sun[3]
            if abs(rel_speed) < 0.001:
                break
            jd += diff / rel_speed

        return jd, None

    def _build_lunation(
            self,
            jd: float,
            phase: str,
            direction: str
    ) -> Dict[str, Any]:
        """Build a lunation result dict from a Julian day."""
        dt = self._jd_to_datetime(jd)
        sun,  _ = swe.calc_ut(jd, swe.SUN,  swe.FLG_SWIEPH)
        moon, _ = swe.calc_ut(jd, swe.MOON, swe.FLG_SWIEPH)
        angle   = (moon[0] - sun[0]) % 360.0

        return {
            'phase':            phase,
            'direction':        direction,
            'datetime_utc':     dt.isoformat(),
            'julian_day':       jd,
            'sun_longitude':    round(sun[0],  6),
            'moon_longitude':   round(moon[0], 6),
            'sun_moon_angle':   round(angle,   6),
        }

    # ==========================================================================
    # Next Apsides (event finder)
    # ==========================================================================

    # Bodies supported for next apside search
    # Moon uses nod_aps_ut(); planets iterate on distance speed sign change
    APSIDE_EVENT_BODIES = {
        'moon':    swe.MOON,
        'mercury': swe.MERCURY,
        'venus':   swe.VENUS,
        'mars':    swe.MARS,
        'jupiter': swe.JUPITER,
        'saturn':  swe.SATURN,
        'uranus':  swe.URANUS,
        'neptune': swe.NEPTUNE,
        'pluto':   swe.PLUTO,
        'ceres':   swe.CERES,
        'pallas':  swe.PALLAS,
        'juno':    swe.JUNO,
        'vesta':   swe.VESTA,
        'chiron':  swe.CHIRON,
    }

    # Approximate orbital periods in days — used to set search window
    # Set to max search cap for very slow bodies
    ORBITAL_PERIODS = {
        'moon':    27.5,
        'mercury': 88,
        'venus':   225,
        'mars':    687,
        'jupiter': 4333,
        'saturn':  10759,
        'uranus':  30687,
        'neptune': 60190,
        'pluto':   90560,
        'ceres':   1682,
        'pallas':  1686,
        'juno':    1593,
        'vesta':   1325,
        'chiron':  18500,
    }

    # Maximum search window in days — prevents runaway searches for slow bodies
    MAX_APSIDE_SEARCH_DAYS = 7300  # 20 years

    def calculate_next_apsides(
            self,
            reference_date: datetime,
            bodies: list = None,
            events: list = None,
            max_search_years: int = 20,
    ) -> Tuple[Optional[list], Optional[str]]:
        """
        Find the next perigee/aphelion and apogee/aphelion events for
        each requested body after the reference date.

        Moon uses swe.nod_aps_ut() for direct lookup.
        Planets iterate forward watching for distance_speed sign change
        (speed < 0 = approaching perihelion, speed > 0 = moving toward aphelion),
        then refine with Newton's method.

        Args:
            reference_date:    Search from this date forward
            bodies:            List of body names — default is all supported bodies
            events:            List of 'perigee'/'perihelion' and/or 'apogee'/'aphelion'
                               Default is both.
            max_search_years:  Cap on forward search window (default 20 years)

        Returns:
            Tuple of (events_list, error_message)
            Each event: {body, event, datetime_utc, julian_day, longitude, distance_au}
        """
        if bodies is None:
            bodies = list(self.APSIDE_EVENT_BODIES.keys())

        if events is None:
            events = ['perigee', 'apogee']

        # Normalise event names — perihelion = perigee, aphelion = apogee
        want_perigee = any(e in events for e in ('perigee', 'perihelion'))
        want_apogee  = any(e in events for e in ('apogee',  'aphelion'))

        max_days = min(max_search_years * 365.25, self.MAX_APSIDE_SEARCH_DAYS)

        ref_jd = swe.julday(
            reference_date.year, reference_date.month, reference_date.day,
            reference_date.hour + reference_date.minute / 60.0
        )

        results = []

        for body_name in bodies:
            planet_id = self.APSIDE_EVENT_BODIES.get(body_name)
            if planet_id is None:
                continue

            try:
                if body_name == 'moon':
                    events_found = self._find_lunar_next_apsides(
                        ref_jd, want_perigee, want_apogee, max_days=max_days
                    )
                else:
                    events_found = self._find_planetary_next_apsides(
                        ref_jd, planet_id, body_name,
                        want_perigee, want_apogee, max_days
                    )

                results.extend(events_found)

            except Exception as e:
                logger.warning(f"Next apside search failed for {body_name}: {e}")

        # Sort by datetime
        results.sort(key=lambda x: x['julian_day'])
        return results, None

    def _find_lunar_next_apsides(
            self,
            ref_jd: float,
            want_perigee: bool,
            want_apogee: bool,
            max_days: float = 365.25
    ) -> list:
        """
        Find ALL Moon perigee and/or apogee events within max_days of ref_jd.

        Moon distance speed < 0 = approaching Earth (heading to perigee)
        Moon distance speed > 0 = moving away from Earth (heading to apogee)
        Sign change negative→positive = perigee
        Sign change positive→negative = apogee

        Moon has ~27.5 day anomalistic cycle so a 1-day step finds all events.
        """
        results = []
        flags = swe.FLG_SWIEPH | swe.FLG_SPEED
        step  = 1.0  # 1-day steps

        prev_pos, _ = swe.calc_ut(ref_jd, swe.MOON, flags)
        prev_speed  = prev_pos[5]
        jd          = ref_jd + step

        while jd <= ref_jd + max_days:
            pos, _ = swe.calc_ut(jd, swe.MOON, flags)
            speed  = pos[5]

            # Sign change negative → positive = perigee (closest point)
            if want_perigee and prev_speed < 0 < speed:
                refined_jd = self._refine_apside_jd(
                    jd - step, jd, swe.MOON, flags, find_minimum=True
                )
                if refined_jd:
                    rpos, _ = swe.calc_ut(refined_jd, swe.MOON, flags)
                    results.append(self._build_apside_event(
                        'moon', 'perigee', refined_jd, rpos[0], rpos[2]
                    ))

            # Sign change positive → negative = apogee (furthest point)
            elif want_apogee and prev_speed > 0 > speed:
                refined_jd = self._refine_apside_jd(
                    jd - step, jd, swe.MOON, flags, find_minimum=False
                )
                if refined_jd:
                    rpos, _ = swe.calc_ut(refined_jd, swe.MOON, flags)
                    results.append(self._build_apside_event(
                        'moon', 'apogee', refined_jd, rpos[0], rpos[2]
                    ))

            prev_speed = speed
            jd        += step

        return results

    def _find_planetary_next_apsides(
            self,
            ref_jd: float,
            planet_id: int,
            planet_name: str,
            want_perigee: bool,
            want_apogee: bool,
            max_days: float
    ) -> list:
        """
        Find the next perihelion and/or aphelion for a planet by scanning
        forward for a sign change in distance speed, then refining.

        distance_speed < 0 → planet is approaching Sun (heading to perihelion)
        distance_speed > 0 → planet is moving away from Sun (heading to aphelion)
        Sign change negative→positive = perihelion just passed
        Sign change positive→negative = aphelion just passed

        We scan until we catch the planet AFTER the minimum/maximum,
        then step back half a period and refine from there.
        """
        results = []
        flags = swe.FLG_SWIEPH | swe.FLG_HELCTR | swe.FLG_SPEED

        # Step size — 1/20th of orbital period for reasonable resolution
        period = self.ORBITAL_PERIODS.get(planet_name, 365)
        step   = max(1.0, period / 20.0)

        prev_jd     = ref_jd
        prev_pos, _ = swe.calc_ut(prev_jd, planet_id, flags)
        prev_speed  = prev_pos[5]  # distance speed

        jd = ref_jd + step

        # Scan full window — collect ALL perihelion/aphelion events
        while jd <= ref_jd + max_days:
            pos, _ = swe.calc_ut(jd, planet_id, flags)
            speed  = pos[5]  # distance speed

            # Sign change negative → positive = perihelion (minimum distance)
            if want_perigee and prev_speed < 0 < speed:
                refined_jd = self._refine_apside_jd(
                    prev_jd, jd, planet_id, flags, find_minimum=True
                )
                if refined_jd and refined_jd > ref_jd:
                    rpos, _ = swe.calc_ut(refined_jd, planet_id, flags)
                    results.append(self._build_apside_event(
                        planet_name, 'perihelion', refined_jd, rpos[0], rpos[2]
                    ))

            # Sign change positive → negative = aphelion (maximum distance)
            elif want_apogee and prev_speed > 0 > speed:
                refined_jd = self._refine_apside_jd(
                    prev_jd, jd, planet_id, flags, find_minimum=False
                )
                if refined_jd and refined_jd > ref_jd:
                    rpos, _ = swe.calc_ut(refined_jd, planet_id, flags)
                    results.append(self._build_apside_event(
                        planet_name, 'aphelion', refined_jd, rpos[0], rpos[2]
                    ))

            prev_jd    = jd
            prev_speed = speed
            jd        += step

        return results

    def _refine_apside_jd(
            self,
            jd_before: float,
            jd_after: float,
            planet_id: int,
            flags: int,
            find_minimum: bool,
            max_iterations: int = 50,
            tolerance: float = 0.0001
    ) -> Optional[float]:
        """
        Refine an apside JD using bisection on the distance speed sign.
        find_minimum=True  → perihelion (speed goes negative → positive)
        find_minimum=False → aphelion  (speed goes positive → negative)
        """
        lo, hi = jd_before, jd_after

        for _ in range(max_iterations):
            mid = (lo + hi) / 2.0
            pos, _ = swe.calc_ut(mid, planet_id, flags)
            speed  = pos[5]  # distance speed

            if abs(hi - lo) < tolerance:
                return mid

            if find_minimum:
                # Looking for negative → positive crossing
                if speed < 0:
                    lo = mid
                else:
                    hi = mid
            else:
                # Looking for positive → negative crossing
                if speed > 0:
                    lo = mid
                else:
                    hi = mid

        return (lo + hi) / 2.0

    def _build_apside_event(
            self,
            body: str,
            event: str,
            jd: float,
            longitude: float,
            distance_au: float
    ) -> Dict[str, Any]:
        """Build an apside event result dict."""
        dt = self._jd_to_datetime(jd)
        return {
            'body':         body,
            'event':        event,
            'datetime_utc': dt.isoformat(),
            'julian_day':   round(jd, 6),
            'longitude':    round(longitude,   6),
            'distance_au':  round(distance_au, 8),
        }

    # ==========================================================================
    # Monthly Ephemeris
    # ==========================================================================

    def calculate_monthly_ephemeris(
            self,
            year: int,
            month: int,
            output_config: dict = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Calculate planetary positions at noon UT for every day of a given month.

        No location is required — no house cusps or ASC/MC are calculated.
        Geocentric and heliocentric positions are returned for all active bodies
        as per the output config.

        Args:
            year:          The year
            month:         The month (1–12)
            output_config: Merged output config dict

        Returns:
            Tuple of (result_dict, error_message)

            result_dict structure:
            {
                "year":  2026,
                "month": 3,
                "days": {
                    "2026-03-01": {
                        "geocentric":  { "sun": {...}, "moon": {...}, ... },
                        "heliocentric": { "mercury": {...}, ... }
                    },
                    "2026-03-02": { ... },
                    ...
                }
            }
        """
        import calendar

        if output_config is None:
            from output_config import OutputConfig
            output_config = OutputConfig.as_dict()

        cfg        = output_config
        bodies_cfg = cfg.get('bodies', {})

        try:
            days_in_month = calendar.monthrange(year, month)[1]
            active_bodies = self._get_active_bodies(bodies_cfg)

            # Remove ASC/MC — not applicable without location
            active_bodies.pop('asc', None)
            active_bodies.pop('mc',  None)

            days = {}

            for day in range(1, days_in_month + 1):
                date_str = f"{year:04d}-{month:02d}-{day:02d}"

                # Noon UT
                jd = swe.julday(year, month, day, 12.0)

                geocentric   = {}
                heliocentric = {}

                for planet_name, planet_id in active_bodies.items():

                    # Geocentric (skip Earth)
                    if cfg.get('geocentric', True) and planet_name != 'earth':
                        geo_pos, _ = self._calculate_position(
                            jd, planet_id, planet_name,
                            heliocentric=False,
                            output_config=cfg
                        )
                        geocentric[planet_name] = geo_pos
                    elif cfg.get('geocentric', True):
                        geocentric[planet_name] = None

                    # Heliocentric (skip Sun, Moon, nodes)
                    if cfg.get('heliocentric', True) and planet_name not in (
                        'sun', 'moon', 'mean_node', 'true_node',
                        'south_node', 'mean_lilith', 'true_lilith', 'part_of_fortune'
                    ):
                        helio_pos, _ = self._calculate_position(
                            jd, planet_id, planet_name,
                            heliocentric=True,
                            output_config=cfg
                        )
                        heliocentric[planet_name] = helio_pos
                    elif cfg.get('heliocentric', True) and planet_name not in heliocentric:
                        heliocentric[planet_name] = None

                    # South Node — derived from mean/true node
                    if planet_name in ('mean_node', 'true_node') and bodies_cfg.get('south_node', False):
                        if geocentric.get(planet_name):
                            sn_lon = (geocentric[planet_name]['longitude'] + 180.0) % 360.0
                            geocentric['south_node'] = self._derived_point(sn_lon)

                    # Part of Fortune requires ASC — skip in ephemeris context
                    # (no location available)

                day_entry = {}
                if cfg.get('geocentric', True):
                    day_entry['geocentric'] = geocentric
                if cfg.get('heliocentric', True):
                    day_entry['heliocentric'] = heliocentric

                days[date_str] = day_entry

            logger.info(f"Monthly ephemeris calculated: {year}-{month:02d} ({days_in_month} days)")

            return {
                'year':  year,
                'month': month,
                'days':  days,
            }, None

        except Exception as e:
            logger.error(f"Monthly ephemeris error: {str(e)}")
            return None, f"Monthly ephemeris failed: {str(e)}"

    # ==========================================================================
    # Eclipse calculations
    # ==========================================================================

    # Swiss Ephemeris eclipse type bit flags
    _ECL_TOTAL         = 4
    _ECL_ANNULAR       = 8
    _ECL_PARTIAL       = 16
    _ECL_ANNULAR_TOTAL = 32   # hybrid solar eclipse
    _ECL_PENUMBRAL     = 64   # lunar penumbral

    def _solar_eclipse_type(self, retval: int) -> str:
        if retval & self._ECL_ANNULAR_TOTAL: return 'hybrid'
        if retval & self._ECL_TOTAL:         return 'total'
        if retval & self._ECL_ANNULAR:       return 'annular'
        if retval & self._ECL_PARTIAL:       return 'partial'
        return 'unknown'

    def _lunar_eclipse_type(self, retval: int) -> str:
        if retval & self._ECL_TOTAL:       return 'total'
        if retval & self._ECL_PARTIAL:     return 'partial'
        if retval & self._ECL_PENUMBRAL:   return 'penumbral'
        return 'unknown'

    def _eclipse_attr_from_positions(self, jd_approx: float, is_solar: bool,
                                      eclipse_type: str = None) -> dict:
        """
        Compute eclipse magnitude, obscuration, and Saros data from geometry.

        Solar eclipses:
            tret[0] from pyswisseph is the new-moon conjunction time, not the
            moment of minimum Sun-Moon separation. Scan ±6 hours in fine steps
            to find the actual minimum, then compute attributes there.

        Lunar eclipses:
            Obscuration is measured against Earth's shadow centre (antisolar
            point), not the Sun itself. sep(Sun, Moon) ≈ 180° at full moon —
            what matters is how close the Moon is to (sun_lon+180°, -sun_lat).
        """
        import math

        saros_series = saros_member = None

        # ── Get Saros data via sol_eclipse_where + sol_eclipse_how ──────────
        # sol_eclipse_when_glob doesn't return attr; sol_eclipse_where gives
        # the geographic coordinates of greatest eclipse, and sol_eclipse_how
        # at those coordinates returns the full attr array with Saros data.
        if is_solar:
            try:
                where_raw = swe.sol_eclipse_where(jd_approx, swe.FLG_SWIEPH)
                # Returns (retval, pathpos, attr) or (retval, pathpos) — extract pathpos
                pathpos = None
                if isinstance(where_raw, (list, tuple)):
                    for item in where_raw:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            try:
                                float(item[0]); float(item[1])
                                pathpos = item
                                break
                            except (TypeError, ValueError):
                                pass
                geopos = [float(pathpos[0]), float(pathpos[1]), 0.0] if pathpos else [0.0, 0.0, 0.0]
                how_raw = swe.sol_eclipse_how(jd_approx, swe.FLG_SWIEPH, geopos)
                if isinstance(how_raw, (list, tuple)):
                    for item in how_raw:
                        if isinstance(item, (list, tuple)) and len(item) > 7:
                            saros_series = int(item[6]) if item[6] else None
                            saros_member = int(item[7]) if item[7] else None
                            break
            except Exception as e:
                logger.debug(f"sol_eclipse_where/how Saros lookup failed: {e}")

        # ── Angular separation helper ─────────────────────────────────────────
        def _sep(lon_a, lat_a, lon_b, lat_b):
            la = math.radians(lat_a); lb = math.radians(lat_b)
            dl = math.radians(lon_a - lon_b)
            c  = math.sin(la)*math.sin(lb) + math.cos(la)*math.cos(lb)*math.cos(dl)
            return math.degrees(math.acos(max(-1.0, min(1.0, c))))

        # ── Circle overlap fraction (fraction of circle R covered by circle r) ─
        def _overlap(R, r, d):
            if R <= 0 or r <= 0: return 0.0
            if d >= R + r:       return 0.0
            if d <= abs(R - r):  return min(r, R) ** 2 / R ** 2
            ca = max(-1.0, min(1.0, (d*d + R*R - r*r) / (2*d*R)))
            cb = max(-1.0, min(1.0, (d*d + r*r - R*R) / (2*d*r)))
            a  = math.acos(ca); b = math.acos(cb)
            area = (R*R*a + r*r*b
                    - R*R*math.sin(a)*math.cos(a)
                    - r*r*math.sin(b)*math.cos(b))
            return min(1.0, area / (math.pi * R * R))

        # ── Apparent radius in degrees from dist_au and body_radius_km ────────
        km_per_au = 149_597_870.7
        def _R_deg(radius_km, dist_au):
            return math.degrees(math.atan2(radius_km, dist_au * km_per_au))

        try:
            if is_solar:
                # ── Solar eclipse ────────────────────────────────────────────
                # Scan ±6 hours around the reported eclipse time in 10-min steps
                # to find the moment of minimum Sun–Moon angular separation.
                scan_step = 10.0 / 1440.0   # 10 minutes in days
                scan_half = 6.0 / 24.0      # 6 hours in days

                best_jd   = jd_approx
                best_sep  = 999.0
                scan_jd   = jd_approx - scan_half
                end_scan  = jd_approx + scan_half

                while scan_jd <= end_scan:
                    sp, _ = swe.calc_ut(scan_jd, swe.SUN,  swe.FLG_SWIEPH)
                    mp, _ = swe.calc_ut(scan_jd, swe.MOON, swe.FLG_SWIEPH)
                    s = _sep(sp[0], sp[1], mp[0], mp[1])
                    if s < best_sep:
                        best_sep = s
                        best_jd  = scan_jd
                    scan_jd += scan_step

                # Refine with 1-minute steps around best
                scan_jd  = best_jd - 15.0/1440.0
                end_scan = best_jd + 15.0/1440.0
                fine_step = 1.0 / 1440.0
                while scan_jd <= end_scan:
                    sp, _ = swe.calc_ut(scan_jd, swe.SUN,  swe.FLG_SWIEPH)
                    mp, _ = swe.calc_ut(scan_jd, swe.MOON, swe.FLG_SWIEPH)
                    s = _sep(sp[0], sp[1], mp[0], mp[1])
                    if s < best_sep:
                        best_sep = s
                        best_jd  = scan_jd
                    scan_jd += fine_step

                # Compute attributes at minimum separation
                sp, _ = swe.calc_ut(best_jd, swe.SUN,  swe.FLG_SWIEPH)
                mp, _ = swe.calc_ut(best_jd, swe.MOON, swe.FLG_SWIEPH)
                R_sun  = _R_deg(696_000.0, float(sp[2]))
                R_moon = _R_deg(1_737.4,   float(mp[2]))
                sep    = best_sep

                logger.info(
                    f"Solar eclipse attr (type={eclipse_type}): "
                    f"min_sep={sep:.4f}° R_sun={R_sun:.4f}° R_moon={R_moon:.4f}° "
                    f"at {self._jd_to_datetime(best_jd).isoformat()}"
                )

                magnitude   = (R_sun + R_moon - sep) / (2 * R_sun)
                obscuration = round(_overlap(R_sun, R_moon, sep), 4)

                # Sanity: if type says total/hybrid but geometry disagrees, correct it
                if eclipse_type in ('total', 'hybrid') and obscuration < 1.0:
                    obscuration = 1.0
                    magnitude   = max(magnitude, 1.0)

                return {
                    'magnitude':    round(magnitude, 4),
                    'obscuration':  obscuration,
                    'saros_series': saros_series,
                    'saros_member': saros_member,
                }

            else:
                # ── Lunar eclipse ────────────────────────────────────────────
                # Scan ±6 hours to find moment of minimum Moon-to-shadow separation.
                # Earth's shadow centre = antisolar point (sun_lon+180°, -sun_lat).
                scan_step = 10.0 / 1440.0
                scan_half = 6.0  / 24.0

                best_jd   = jd_approx
                best_sep  = 999.0
                scan_jd   = jd_approx - scan_half
                end_scan  = jd_approx + scan_half

                while scan_jd <= end_scan:
                    sp, _ = swe.calc_ut(scan_jd, swe.SUN,  swe.FLG_SWIEPH)
                    mp, _ = swe.calc_ut(scan_jd, swe.MOON, swe.FLG_SWIEPH)
                    shadow_lon = (float(sp[0]) + 180.0) % 360.0
                    shadow_lat = -float(sp[1])
                    s = _sep(shadow_lon, shadow_lat, float(mp[0]), float(mp[1]))
                    if s < best_sep:
                        best_sep = s
                        best_jd  = scan_jd
                    scan_jd += scan_step

                # Refine
                scan_jd  = best_jd - 15.0/1440.0
                end_scan = best_jd + 15.0/1440.0
                fine_step = 1.0 / 1440.0
                while scan_jd <= end_scan:
                    sp, _ = swe.calc_ut(scan_jd, swe.SUN,  swe.FLG_SWIEPH)
                    mp, _ = swe.calc_ut(scan_jd, swe.MOON, swe.FLG_SWIEPH)
                    shadow_lon = (float(sp[0]) + 180.0) % 360.0
                    shadow_lat = -float(sp[1])
                    s = _sep(shadow_lon, shadow_lat, float(mp[0]), float(mp[1]))
                    if s < best_sep:
                        best_sep = s
                        best_jd  = scan_jd
                    scan_jd += fine_step

                # Compute at minimum
                sp, _ = swe.calc_ut(best_jd, swe.SUN,  swe.FLG_SWIEPH)
                mp, _ = swe.calc_ut(best_jd, swe.MOON, swe.FLG_SWIEPH)
                R_sun_km   = 696_000.0
                earth_r_km = 6_371.0
                sun_dist   = float(sp[2])
                moon_dist  = float(mp[2])
                R_moon     = _R_deg(1_737.4, moon_dist)

                # Earth's umbra and penumbra radii at Moon's distance
                umbra_km   = earth_r_km - R_sun_km * (moon_dist / sun_dist)
                penumb_km  = earth_r_km + R_sun_km * (moon_dist / sun_dist)
                R_umbra    = _R_deg(max(0.0, umbra_km), moon_dist) if umbra_km > 0 else 0.0
                R_penumbra = _R_deg(penumb_km, moon_dist)

                sep = best_sep

                umbral_mag    = (R_umbra   + R_moon - sep) / (2 * R_moon) if R_umbra > 0 else -1
                penumbral_mag = (R_penumbra + R_moon - sep) / (2 * R_moon)

                logger.info(
                    f"Lunar eclipse attr (type={eclipse_type}): "
                    f"shadow_sep={sep:.4f}° R_umbra={R_umbra:.4f}° "
                    f"R_penumbra={R_penumbra:.4f}° R_moon={R_moon:.4f}° "
                    f"umbral_mag={umbral_mag:.4f} at {self._jd_to_datetime(best_jd).isoformat()}"
                )

                if umbral_mag > 0:
                    obscuration = round(_overlap(R_moon, R_umbra, sep), 4)
                else:
                    obscuration = round(_overlap(R_moon, R_penumbra, sep), 4)

                # Sanity check for total lunar
                if eclipse_type == 'total' and obscuration < 1.0:
                    obscuration = 1.0

                return {
                    'magnitude':        round(max(0.0, penumbral_mag), 4),
                    'umbral_magnitude': round(umbral_mag, 4) if umbral_mag > 0 else None,
                    'obscuration':      obscuration,
                    'saros_series':     saros_series,
                    'saros_member':     saros_member,
                }

        except Exception as e:
            logger.warning(f"Eclipse attr computation failed: {e}")
            # Last resort fallback
            if eclipse_type in ('total', 'hybrid'):
                return {'magnitude': 1.01, 'obscuration': 1.0,
                        'saros_series': saros_series, 'saros_member': saros_member}
            return {'magnitude': None, 'obscuration': 0.0,
                    'saros_series': saros_series, 'saros_member': saros_member}

    def calculate_eclipses(
            self,
            reference_date: datetime,
            years_ahead: int = 5,
    ) -> Tuple[Optional[list], Optional[str]]:
        """
        Find all solar and lunar eclipses within a given time window.

        Searches from reference_date forward for years_ahead years.
        Both solar and lunar eclipses are returned, sorted chronologically.

        For each eclipse the disc obscuration is the fraction of the
        Sun/Moon diameter covered at maximum eclipse (0.0–1.0).
        Totality is represented as 1.0 regardless of the raw magnitude.

        Args:
            reference_date: Start of the search window
            years_ahead:    Number of years to search forward (max 50)

        Returns:
            Tuple of (eclipses_list, error_message)
            Each entry: {
                type, eclipse_type, datetime_utc, julian_day,
                magnitude, obscuration, saros_series, saros_member
            }
        """
        years_ahead = max(1, min(50, years_ahead))

        start_jd = swe.julday(
            reference_date.year, reference_date.month, reference_date.day,
            reference_date.hour + reference_date.minute / 60.0 + reference_date.second / 3600.0
        )
        end_jd = start_jd + years_ahead * 365.25

        eclipses = []

        # ── Solar eclipses ────────────────────────────────────────────────────
        jd = start_jd
        max_iterations = years_ahead * 6   # ~4–5 solar eclipses per year max
        _solar_logged = False
        for _ in range(max_iterations):
            try:
                raw = swe.sol_eclipse_when_glob(jd + 0.001, swe.FLG_SWIEPH, False)
                if not _solar_logged:
                    logger.debug(f"sol_eclipse_when_glob returned {len(raw)} values: types={[type(v).__name__ for v in raw]}")
                    _solar_logged = True
                if len(raw) == 3:
                    retval, tret, attr = raw
                elif len(raw) == 2:
                    retval, tret = raw
                    attr = []
                else:
                    break
            except Exception as e:
                logger.warning(f"Solar eclipse search error at jd={jd:.1f}: {e}")
                break

            if retval == 0 or not tret or tret[0] == 0:
                break
            if tret[0] > end_jd:
                break

            eclipse_type = self._solar_eclipse_type(retval)

            if attr:
                raw_obscuration = float(attr[0]) if attr else 0.0
                magnitude       = float(attr[1]) if len(attr) > 1 else None
                obscuration     = round(min(1.0, max(0.0, raw_obscuration)), 4)
                saros_series    = int(attr[6]) if len(attr) > 6 and attr[6] else None
                saros_member    = int(attr[7]) if len(attr) > 7 and attr[7] else None
            else:
                computed     = self._eclipse_attr_from_positions(tret[0], is_solar=True,
                                                                  eclipse_type=eclipse_type)
                magnitude    = computed['magnitude']
                obscuration  = computed['obscuration']
                saros_series = computed['saros_series']
                saros_member = computed['saros_member']

            dt = self._jd_to_datetime(tret[0])
            eclipses.append({
                'type':         'solar',
                'eclipse_type': eclipse_type,
                'datetime_utc': dt.isoformat(),
                'julian_day':   round(tret[0], 6),
                'magnitude':    round(magnitude, 4) if magnitude is not None else None,
                'obscuration':  obscuration,
                'saros_series': saros_series,
                'saros_member': saros_member,
            })

            jd = tret[0] + 25   # advance past this eclipse (shortest cycle ~29 days)

        # ── Lunar eclipses ────────────────────────────────────────────────────
        jd = start_jd
        max_iterations = years_ahead * 6
        for _ in range(max_iterations):
            try:
                raw = swe.lun_eclipse_when(jd + 0.001, swe.FLG_SWIEPH, False)
                if len(raw) == 3:
                    retval, tret, attr = raw
                elif len(raw) == 2:
                    retval, tret = raw
                    attr = []
                else:
                    break
            except Exception as e:
                logger.warning(f"Lunar eclipse search error at jd={jd:.1f}: {e}")
                break

            if retval == 0 or not tret or tret[0] == 0:
                break
            if tret[0] > end_jd:
                break

            eclipse_type = self._lunar_eclipse_type(retval)

            if attr:
                penumbral_mag = float(attr[0]) if attr else 0.0
                umbral_mag    = float(attr[1]) if len(attr) > 1 else 0.0
                if eclipse_type in ('total', 'partial'):
                    obscuration = round(min(1.0, max(0.0, umbral_mag)), 4)
                else:
                    obscuration = round(min(1.0, max(0.0, penumbral_mag)), 4)
                saros_series = int(attr[4]) if len(attr) > 4 and attr[4] else None
                saros_member = int(attr[5]) if len(attr) > 5 and attr[5] else None
                umbral_out   = round(umbral_mag, 4) if umbral_mag > 0 else None
            else:
                computed      = self._eclipse_attr_from_positions(tret[0], is_solar=False,
                                                                   eclipse_type=eclipse_type)
                penumbral_mag = computed['magnitude']
                umbral_mag    = computed.get('umbral_magnitude')
                obscuration   = computed['obscuration']
                saros_series  = computed['saros_series']
                saros_member  = computed['saros_member']
                umbral_out    = umbral_mag

            dt = self._jd_to_datetime(tret[0])
            eclipses.append({
                'type':             'lunar',
                'eclipse_type':     eclipse_type,
                'datetime_utc':     dt.isoformat(),
                'julian_day':       round(tret[0], 6),
                'magnitude':        round(penumbral_mag, 4) if penumbral_mag is not None else None,
                'umbral_magnitude': umbral_out,
                'obscuration':      obscuration,
                'saros_series':     saros_series,
                'saros_member':     saros_member,
            })

            jd = tret[0] + 25

        eclipses.sort(key=lambda e: e['julian_day'])
        logger.info(
            f"Eclipse search: {reference_date.date()} + {years_ahead}yr → "
            f"{len(eclipses)} eclipses found"
        )
        return eclipses, None