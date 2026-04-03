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

Behaviour is controlled by the USE_GOOGLE config flag:

    USE_GOOGLE=false  (fully offline)
        autocomplete  → CitiesService.search()
        geocode       → CitiesService.resolve() → pytz offsets → locations cache

    USE_GOOGLE=true  (hybrid)
        autocomplete  → CitiesService.search()  (clean vocabulary, no Google)
        geocode       → existing PlaceRepository chain (alias → cache → Google)
                        Input arrives clean from cities5000 autocomplete, keeping
                        the canonical_places table tidy.

In both modes the locations table and the dict shape returned to callers are
identical, so the rest of the API is unaffected.
"""
import logging
from typing import Tuple, Optional, Dict, Any

from cities_service import CitiesService

logger = logging.getLogger(__name__)


class GeocodingService:
    """Handles geocoding and timezone lookups."""

    def __init__(self, api_key: str, db_manager, usage_tracker, use_google: bool = True):
        self.use_google    = use_google
        self.db            = db_manager
        self.usage_tracker = usage_tracker
        self.cities_svc    = CitiesService(db_manager)

        if use_google:
            import googlemaps
            from place_repository import PlaceRepository
            self.gmaps       = googlemaps.Client(key=api_key)
            self.place_repo  = PlaceRepository(db_manager, api_key, usage_tracker)
            logger.info("GeocodingService initialised in Google (hybrid) mode")
        else:
            self.gmaps      = None
            self.place_repo = None
            logger.info("GeocodingService initialised in offline (cities5000) mode")

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def geocode_location(self, location: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Resolve a location string to lat/lon/timezone.

        USE_GOOGLE=false:
            locations cache → CitiesService.resolve() → save to locations cache

        USE_GOOGLE=true:
            locations cache → PlaceRepository.resolve() → save to locations cache

        Returns:
            Tuple of (location_info dict, error_message)
        """
        # Fast path: exact query string already in locations cache
        cached = self.db.get_location_from_cache(location)
        if cached:
            logger.info(f"Location '{location}' found in locations cache (id={cached['id']})")
            return cached, None

        if self.use_google:
            return self._geocode_via_google(location)
        else:
            return self._geocode_via_cities(location)

    def resolve_place(self, place_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Full canonical place resolution — returns the rich place dict.
        Used by the /locations/resolve endpoint.
        In offline mode, delegates to CitiesService.resolve().
        """
        if self.use_google:
            return self.place_repo.resolve(place_name)
        return self.cities_svc.resolve(place_name)

    def autocomplete(self, query: str) -> Dict[str, Any]:
        """
        Autocomplete search for locations.

        Always uses CitiesService (both modes) — this replaces Google Places
        autocomplete entirely. In USE_GOOGLE=true mode the benefit is that
        every selected city produces a consistent, canonical string that
        arrives clean at PlaceRepository, reducing alias proliferation.

        Returns {'predictions': [...]} matching the shape callers expect,
        where each prediction has at minimum a 'description' key.
        """
        results = self.cities_svc.search(query, limit=10)

        if not results:
            logger.info(f"Autocomplete '{query}' — no cities results")
            return {'predictions': []}

        logger.info(f"Autocomplete '{query}' → {len(results)} predictions (cities5000)")
        return {'predictions': results}

    # -------------------------------------------------------------------------
    # Internal geocoding paths
    # -------------------------------------------------------------------------

    def _geocode_via_cities(self, location: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Offline resolution via CitiesService."""
        place, error = self.cities_svc.resolve(location)
        if error:
            return None, error
        return self._save_and_return(location, place)

    def _geocode_via_google(self, location: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Resolution via PlaceRepository (alias → canonical → Google cache).
        Unchanged from the original geocode_location() implementation.
        """
        place, error = self.place_repo.resolve(location)
        if error:
            return None, error
        return self._save_and_return(location, place)

    def _save_and_return(
        self,
        location: str,
        place: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], None]:
        """
        Build the location_info dict, save it to the locations cache, and return it.
        Common to both resolution paths.
        """
        location_info = {
            'latitude':           place['latitude'],
            'longitude':          place['longitude'],
            'formatted_address':  place['formatted_name'],
            'timezone':           place['timezone_id'],
            'utc_offset_seconds': place['utc_offset_seconds'],
            'dst_offset_seconds': place['dst_offset_seconds'],
            'daylight_saving':    place.get('daylight_saving', False),
            'from_cache':         place['cache_hit'],
        }

        location_id = self.db.save_location_to_cache(location, location_info)
        location_info['id'] = location_id

        logger.info(
            f"Location '{location}' resolved to '{place['formatted_name']}' "
            f"(source={place.get('source', 'google')}, "
            f"locations_id={location_id}, cache_hit={place['cache_hit']})"
        )
        return location_info, None