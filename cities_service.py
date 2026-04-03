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
# cities_service.py                                                           #
################################################################################

"""
GeoNames cities5000 service.

Provides two public methods:
    search(query, limit)  — prefix-match autocomplete suggestions
    resolve(query)        — best-match city for offline geocoding

Timezone offsets (utc_offset_seconds, dst_offset_seconds) are derived locally
from the IANA timezone_id stored in the cities table using pytz, so no
external API call is required.

Coordinate precision: GeoNames stores city centroids, accurate to roughly
1-5 km for large cities. This is adequate for astronomical chart calculations
where the timezone ID is the critical value, not rooftop precision.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

import pytz

logger = logging.getLogger(__name__)


def _tz_offsets(timezone_id: str) -> Dict[str, int]:
    """
    Derive current UTC and DST offsets in seconds from an IANA timezone ID.
    Returns {'utc_offset_seconds': int, 'dst_offset_seconds': int}.
    Falls back to zeros if the timezone_id is unrecognised.
    """
    try:
        tz  = pytz.timezone(timezone_id)
        now = datetime.now(tz)
        utc_offset = int(now.utcoffset().total_seconds())
        dst_offset = int(now.dst().total_seconds()) if now.dst() else 0
        return {
            'utc_offset_seconds': utc_offset,
            'dst_offset_seconds': dst_offset,
        }
    except Exception:
        logger.warning(f"Could not derive offsets for timezone '{timezone_id}', defaulting to 0")
        return {'utc_offset_seconds': 0, 'dst_offset_seconds': 0}


def _format_name(row: Dict[str, Any]) -> str:
    """
    Build a human-readable location string from a cities row.
    Format: "City, Country Code" — clean and consistent for every resolution.
    e.g. "Perth, AU" or "London, GB"
    """
    parts = [row['name'], row['country_code']]
    return ', '.join(p for p in parts if p)


class CitiesService:
    """
    Query interface over the cities table populated from GeoNames cities5000.txt.
    All queries go directly to the DB — no in-memory index is maintained.
    At ~200k rows with indexed ascii_name, SQLite handles this comfortably.
    """

    def __init__(self, db_manager):
        self.db = db_manager

    def search(self, query: str, limit: int = 10) -> list:
        """
        Autocomplete search — returns up to `limit` city suggestions.

        Results are ordered by population DESC so major cities surface first
        when multiple cities share a name prefix (e.g. "London" returns
        London GB before smaller Londons).

        Each result dict:
            description  — display string for the autocomplete UI
            geoname_id   — GeoNames ID (stable, use as reference key)
            country_code — ISO 3166-1 alpha-2
            latitude     — city centroid
            longitude    — city centroid
            timezone_id  — IANA timezone string

        Returns an empty list if no matches or cities table is empty.
        """
        if not query or len(query.strip()) < 2:
            return []

        rows = self.db.search_cities(query, limit=limit)
        results = []
        for row in rows:
            results.append({
                'description': _format_name(row),
                'geoname_id':  row['geoname_id'],
                'country_code': row['country_code'],
                'latitude':    row['latitude'],
                'longitude':   row['longitude'],
                'timezone_id': row['timezone_id'],
            })

        logger.debug(f"Cities autocomplete '{query}' → {len(results)} results")
        return results

    def resolve(self, query: str) -> tuple:
        """
        Resolve a location string to a full place dict for chart calculation.

        Tries exact ascii_name match first, then prefix match. Returns the
        highest-population match in either case.

        Returns:
            (place_dict, None)  on success
            (None, error_str)   if no match found or cities table is empty

        The returned place_dict matches the shape expected by GeocodingService
        after PlaceRepository.resolve(), so geocode_location() can handle
        both paths uniformly:
            {
                'formatted_name':     str,
                'latitude':           float,
                'longitude':          float,
                'timezone_id':        str,
                'utc_offset_seconds': int,
                'dst_offset_seconds': int,
                'daylight_saving':    bool,
                'cache_hit':          bool,
                'canonical_place_id': None,   # no canonical place for offline mode
                'source':             'cities5000',
            }
        """
        if not query or not query.strip():
            return None, "Location query is empty"

        row = self.db.resolve_city(query)

        if not row:
            return None, (
                f"Location '{query}' not found in cities database. "
                "Try a larger nearby city, or check spelling."
            )

        offsets = _tz_offsets(row['timezone_id'])

        place = {
            'formatted_name':     _format_name(row),
            'latitude':           row['latitude'],
            'longitude':          row['longitude'],
            'timezone_id':        row['timezone_id'],
            'utc_offset_seconds': offsets['utc_offset_seconds'],
            'dst_offset_seconds': offsets['dst_offset_seconds'],
            'daylight_saving':    offsets['dst_offset_seconds'] != 0,
            'cache_hit':          False,
            'canonical_place_id': None,
            'source':             'cities5000',
        }

        logger.info(
            f"Cities resolved '{query}' → '{place['formatted_name']}' "
            f"(tz={place['timezone_id']}, geoname_id={row['geoname_id']})"
        )
        return place, None