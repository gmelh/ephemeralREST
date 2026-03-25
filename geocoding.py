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
# geocoding.py                                                                #
################################################################################

"""
Geocoding and location services module.

geocode_location() now routes through the canonical place system:
    1. PlaceRepository.resolve() does alias lookup, cache check, Google call
    2. The resolved lat/lon/timezone is saved to the locations table
       (which is the FK target for chart records — unchanged)
    3. Returns the same dict shape the rest of the API expects

The autocomplete method is unchanged.
"""
import logging
import googlemaps
from datetime import datetime
from typing import Tuple, Optional, Dict, Any

from place_repository import PlaceRepository

logger = logging.getLogger(__name__)


class GeocodingService:
    """Handles geocoding and timezone lookups"""

    def __init__(self, api_key: str, db_manager, usage_tracker):
        self.api_key       = api_key
        self.gmaps         = googlemaps.Client(key=api_key)
        self.db            = db_manager
        self.usage_tracker = usage_tracker
        self.place_repo    = PlaceRepository(db_manager, api_key, usage_tracker)

    def geocode_location(self, location: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Resolve a location string to lat/lon/timezone.

        Flow:
            1. Check the simple locations cache (keyed by query string)
               — fast path for repeated identical strings
            2. Run canonical resolution via PlaceRepository
               — handles aliases, canonical deduplication, 30-day Google cache
            3. Save result to the locations table for chart FK references
            4. Return {id, latitude, longitude, formatted_address, timezone, from_cache}

        Returns:
            Tuple of (location_info, error_message)
        """
        # --- Fast path: exact query string already in locations cache ---
        cached = self.db.get_location_from_cache(location)
        if cached:
            logger.info(f"Location '{location}' found in locations cache (id={cached['id']})")
            return cached, None

        # --- Canonical resolution ---
        place, error = self.place_repo.resolve(location)
        if error:
            return None, error

        # Build the location_info dict the rest of the API expects
        location_info = {
            'latitude':          place['latitude'],
            'longitude':         place['longitude'],
            'formatted_address': place['formatted_name'],
            'timezone':          place['timezone_id'],
            'utc_offset_seconds': place['utc_offset_seconds'],
            'dst_offset_seconds': place['dst_offset_seconds'],
            'daylight_saving':   place['daylight_saving'],
            'from_cache':        place['cache_hit'],
        }

        # Save to the locations table so chart FK references work
        location_id = self.db.save_location_to_cache(location, location_info)
        location_info['id'] = location_id

        logger.info(
            f"Location '{location}' resolved to '{place['formatted_name']}' "
            f"(canonical_place_id={place['canonical_place_id']}, "
            f"locations_id={location_id}, cache_hit={place['cache_hit']})"
        )
        return location_info, None

    def resolve_place(self, place_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Full canonical place resolution — returns the rich place dict.
        Used by the /locations/resolve endpoint.
        """
        return self.place_repo.resolve(place_name)

    def autocomplete(self, query: str) -> Dict[str, Any]:
        """Perform autocomplete search for locations via Google Places."""
        try:
            result = self.gmaps.places_autocomplete(
                input_text=query,
                types=['(cities)']
            )
            predictions = [
                {
                    'description': place['description'],
                    'place_id':    place['place_id']
                } for place in result
            ]
            logger.info(f"Autocomplete for '{query}' returned {len(predictions)} results")
            return {'predictions': predictions}

        except googlemaps.exceptions.ApiError as e:
            logger.error(f"Autocomplete API error: {str(e)}")
            return {'error': f'Autocomplete API error: {str(e)}'}
        except Exception as e:
            logger.error(f"Autocomplete error: {str(e)}")
            return {'error': f'Autocomplete failed: {str(e)}'}