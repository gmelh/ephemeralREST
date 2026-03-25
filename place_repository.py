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
# place_repository.py                                                         #
################################################################################

"""
Repository for canonical place resolution.

Implements the full lookup flow:
    1. Normalise input
    2. Check place_aliases  -> canonical_place_id
    3. Check place_cache    -> lat/lon/timezone if fresh
    4. On miss: call Google, build/refresh canonical record
    5. Log every lookup attempt to place_lookup_log

This module talks only to the database — no Flask, no routes.
GeocodingService calls into this repository and converts the result
into the format the rest of the API expects.
"""

import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

from location_normaliser import normalise

logger = logging.getLogger(__name__)

# Cache lifetime in days
CACHE_EXPIRY_DAYS = 30


class PlaceRepository:
    """
    Resolves user-entered place strings to canonical place records.
    All Google API calls are made here.
    """

    def __init__(self, db_manager, google_api_key: str, usage_tracker=None):
        self.db  = db_manager
        self.api_key      = google_api_key
        self.usage_tracker = usage_tracker

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def resolve(self, place_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Resolve a user-entered place string to a canonical place with
        lat/lon/timezone data.

        Returns:
            Tuple of (place_dict, error_message)

        place_dict keys:
            canonical_place_id, formatted_name, google_place_id,
            locality, admin_area_1, admin_area_2, country, country_code,
            latitude, longitude, timezone_id, utc_offset_seconds,
            dst_offset_seconds, cache_hit, source
        """
        normalised = normalise(place_name)
        log_entry  = {
            'input_text':       place_name,
            'normalized_input': normalised,
            'matched_alias_id': None,
            'matched_place_id': None,
            'cache_hit':        False,
            'google_called':    False,
            'success':          False,
            'error_message':    None,
        }

        try:
            # --- Step 1: alias lookup ---
            alias = self.db.get_place_alias(normalised)

            if alias:
                log_entry['matched_alias_id'] = alias['id']
                log_entry['matched_place_id'] = alias['canonical_place_id']

                # --- Step 2: check cache ---
                cache = self.db.get_place_cache(alias['canonical_place_id'])
                if cache:
                    place = self.db.get_canonical_place(alias['canonical_place_id'])
                    log_entry['cache_hit'] = True
                    log_entry['success']   = True
                    self.db.log_place_lookup(log_entry)
                    return self._build_result(place, cache, cache_hit=True), None

                # Alias exists but cache expired — refresh from Google
                logger.info(
                    f"Cache expired for place_id={alias['canonical_place_id']} "
                    f"('{place_name}') — refreshing from Google"
                )

            # --- Step 3: Google lookup ---
            if not self._check_usage():
                err = "Google API usage limit exceeded for this month"
                log_entry['error_message'] = err
                self.db.log_place_lookup(log_entry)
                return None, err

            log_entry['google_called'] = True
            geo, tz, err = self._call_google(place_name)
            if err:
                log_entry['error_message'] = err
                self.db.log_place_lookup(log_entry)
                return None, err

            # --- Step 4: upsert canonical place ---
            canonical_place_id = self._upsert_canonical_place(geo)

            # --- Step 5: upsert alias ---
            alias_id = self.db.upsert_place_alias(normalised, place_name, canonical_place_id)

            # --- Step 6: refresh cache ---
            self.db.upsert_place_cache(canonical_place_id, geo, tz)

            log_entry['matched_alias_id'] = alias_id
            log_entry['matched_place_id'] = canonical_place_id
            log_entry['success']          = True
            self.db.log_place_lookup(log_entry)

            place = self.db.get_canonical_place(canonical_place_id)
            cache = self.db.get_place_cache(canonical_place_id)
            return self._build_result(place, cache, cache_hit=False), None

        except Exception as e:
            logger.error(f"Place resolution error for '{place_name}': {e}", exc_info=True)
            log_entry['error_message'] = str(e)
            self.db.log_place_lookup(log_entry)
            return None, f"Place resolution failed: {str(e)}"

    # -------------------------------------------------------------------------
    # Google API calls
    # -------------------------------------------------------------------------

    def _check_usage(self) -> bool:
        if self.usage_tracker:
            return self.usage_tracker.check_and_increment()
        return True

    def _call_google(
            self,
            place_name: str
    ) -> Tuple[Optional[Dict], Optional[Dict], Optional[str]]:
        """
        Call Google Geocoding + Timezone APIs.
        Returns (geocode_data, timezone_data, error_message).
        """
        # --- Geocoding ---
        try:
            url     = "https://maps.googleapis.com/maps/api/geocode/json"
            params  = {'address': place_name, 'key': self.api_key}
            resp    = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data    = resp.json()

            status = data.get('status')
            if status == 'ZERO_RESULTS':
                return None, None, f"Location not found: '{place_name}'"
            if status == 'OVER_QUERY_LIMIT':
                return None, None, "Google geocoding quota exceeded"
            if status != 'OK':
                return None, None, f"Google geocoding error: {status}"

            result   = data['results'][0]
            lat      = result['geometry']['location']['lat']
            lng      = result['geometry']['location']['lng']
            geo_data = {
                'google_place_id':   result.get('place_id'),
                'formatted_name':    result.get('formatted_address', place_name),
                'latitude':          lat,
                'longitude':         lng,
                'locality':          _extract_component(result, 'locality'),
                'admin_area_1':      _extract_component(result, 'administrative_area_level_1'),
                'admin_area_2':      _extract_component(result, 'administrative_area_level_2'),
                'country':           _extract_component(result, 'country'),
                'country_code':      _extract_component(result, 'country', short=True),
            }

        except requests.RequestException as e:
            logger.error(f"Google geocoding network error: {e}")
            return None, None, f"Geocoding network error: {str(e)}"
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"Google geocoding parse error: {e}")
            return None, None, f"Geocoding response parse error: {str(e)}"

        # --- Timezone (uses geocoded lat/lng) ---
        try:
            ts      = int(datetime.utcnow().timestamp())
            url     = "https://maps.googleapis.com/maps/api/timezone/json"
            params  = {
                'location': f"{lat},{lng}",
                'timestamp': ts,
                'key': self.api_key
            }
            resp    = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            tz_data = resp.json()

            tz_status = tz_data.get('status')
            if tz_status == 'OVER_QUERY_LIMIT':
                return None, None, "Google timezone quota exceeded"
            if tz_status != 'OK':
                logger.warning(f"Google timezone returned status {tz_status} — defaulting to UTC")
                tz_result = {'timeZoneId': 'UTC', 'rawOffset': 0, 'dstOffset': 0}
            else:
                tz_result = {
                    'timeZoneId':        tz_data.get('timeZoneId', 'UTC'),
                    'rawOffset':         tz_data.get('rawOffset', 0),
                    'dstOffset':         tz_data.get('dstOffset', 0),
                }

        except requests.RequestException as e:
            logger.error(f"Google timezone network error: {e}")
            # Non-fatal — fall back to UTC rather than failing the whole request
            logger.warning("Timezone lookup failed — defaulting to UTC")
            tz_result = {'timeZoneId': 'UTC', 'rawOffset': 0, 'dstOffset': 0}

        return geo_data, tz_result, None

    # -------------------------------------------------------------------------
    # Canonical place upsert
    # -------------------------------------------------------------------------

    def _upsert_canonical_place(self, geo: Dict) -> int:
        """
        Find or create a canonical place record.

        Priority:
        1. Match on google_place_id (most reliable)
        2. Match on normalized_key derived from formatted_name
        3. Create new record
        """
        google_place_id = geo.get('google_place_id')
        normalized_key  = normalise(geo['formatted_name'])

        # Try by google_place_id first
        if google_place_id:
            existing_id = self.db.get_canonical_place_id_by_google_id(google_place_id)
            if existing_id:
                logger.info(f"Matched canonical place {existing_id} by google_place_id")
                self.db.update_canonical_place(existing_id, geo)
                return existing_id

        # Try by normalized_key
        existing_id = self.db.get_canonical_place_id_by_key(normalized_key)
        if existing_id:
            logger.info(f"Matched canonical place {existing_id} by normalized_key")
            self.db.update_canonical_place(existing_id, geo)
            return existing_id

        # Create new
        new_id = self.db.create_canonical_place(normalized_key, geo)
        logger.info(f"Created canonical place {new_id}: {geo['formatted_name']}")
        return new_id

    # -------------------------------------------------------------------------
    # Result builder
    # -------------------------------------------------------------------------

    def _build_result(
            self,
            place: Dict,
            cache: Dict,
            cache_hit: bool
    ) -> Dict[str, Any]:
        # source reflects where the data came from in this request:
        #   'google' — fetched live from Google API just now
        #   'cache'  — served from local canonical/place cache
        source = 'cache' if cache_hit else 'google'

        dst_offset = cache['dst_offset_seconds']

        return {
            'canonical_place_id': place['id'],
            'formatted_name':     place['formatted_name'],
            'google_place_id':    place['google_place_id'],
            'locality':           place['locality'],
            'admin_area_1':       place['admin_area_1'],
            'admin_area_2':       place['admin_area_2'],
            'country':            place['country'],
            'country_code':       place['country_code'],
            'latitude':           cache['latitude'],
            'longitude':          cache['longitude'],
            'timezone_id':        cache['timezone_id'],
            'utc_offset_seconds': cache['utc_offset_seconds'],
            'dst_offset_seconds': dst_offset,
            'daylight_saving':    dst_offset != 0,
            'cache_hit':          cache_hit,
            'source':             source,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_component(result: Dict, component_type: str, short: bool = False) -> Optional[str]:
    """Extract a named component from a Google geocode result."""
    for component in result.get('address_components', []):
        if component_type in component.get('types', []):
            return component['short_name'] if short else component['long_name']
    return None