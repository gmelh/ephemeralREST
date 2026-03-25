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
# location_normaliser.py                                                      #
################################################################################

"""
Location normalisation for Astro API.

Goal: reduce duplicate aliases and repeated Google calls by producing a
consistent lookup key from user-entered place strings.

Deliberately practical — not a full address parser. Handles the most common
sources of variation: case, whitespace, punctuation, and a small set of
well-known abbreviations.
"""

import re

# ---------------------------------------------------------------------------
# Abbreviation expansion table
# ---------------------------------------------------------------------------
# Maps abbreviations (lower case) to their canonical expanded form.
# Applied AFTER splitting on commas so "WA" in "Perth, WA" is treated as
# a standalone token rather than matching mid-word.
#
# Extend this dict as needed. Keep expansions lower case.

_ABBREVIATIONS: dict[str, str] = {
    # Australian states / territories
    'wa':  'western australia',
    'nsw': 'new south wales',
    'vic': 'victoria',
    'qld': 'queensland',
    'sa':  'south australia',
    'tas': 'tasmania',
    'act': 'australian capital territory',
    'nt':  'northern territory',

    # US states (most common)
    'al': 'alabama',       'ak': 'alaska',       'az': 'arizona',
    'ar': 'arkansas',      'ca': 'california',   'co': 'colorado',
    'ct': 'connecticut',   'de': 'delaware',     'fl': 'florida',
    'ga': 'georgia',       'hi': 'hawaii',       'id': 'idaho',
    'il': 'illinois',      'in': 'indiana',      'ia': 'iowa',
    'ks': 'kansas',        'ky': 'kentucky',     'la': 'louisiana',
    'me': 'maine',         'md': 'maryland',     'ma': 'massachusetts',
    'mi': 'michigan',      'mn': 'minnesota',    'ms': 'mississippi',
    'mo': 'missouri',      'mt': 'montana',      'ne': 'nebraska',
    'nv': 'nevada',        'nh': 'new hampshire','nj': 'new jersey',
    'nm': 'new mexico',    'ny': 'new york',     'nc': 'north carolina',
    'nd': 'north dakota',  'oh': 'ohio',         'ok': 'oklahoma',
    'or': 'oregon',        'pa': 'pennsylvania', 'ri': 'rhode island',
    'sc': 'south carolina','sd': 'south dakota', 'tn': 'tennessee',
    'tx': 'texas',         'ut': 'utah',         'vt': 'vermont',
    'va': 'virginia',      'wa': 'washington',   'wv': 'west virginia',
    'wi': 'wisconsin',     'wy': 'wyoming',

    # Canadian provinces
    'ab': 'alberta',       'bc': 'british columbia', 'mb': 'manitoba',
    'nb': 'new brunswick', 'nl': 'newfoundland',     'ns': 'nova scotia',
    'on': 'ontario',       'pe': 'prince edward island', 'qc': 'quebec',
    'sk': 'saskatchewan',

    # UK
    'uk':  'united kingdom',
    'gb':  'united kingdom',
    'eng': 'england',
    'sco': 'scotland',
    'wal': 'wales',

    # Common country abbreviations
    'usa': 'united states',
    'us':  'united states',
    'aus': 'australia',
    'nz':  'new zealand',
    'rsa': 'south africa',
    'uae': 'united arab emirates',
}

# Note: 'wa' maps to both 'western australia' and 'washington'.
# We can't resolve this ambiguity without context — the abbreviation
# expansion is best-effort. Google will resolve it correctly from the
# full place string anyway; expansion mainly helps alias matching.
# For 'wa' we keep 'western australia' since the API is AU-oriented.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalise(place_name: str) -> str:
    """
    Produce a normalised lookup key from a user-entered place string.

    Steps:
    1. Strip and lower-case
    2. Normalise whitespace and punctuation
    3. Split on commas, expand abbreviations per token
    4. Rejoin and collapse whitespace

    Returns a single lower-case string with no leading/trailing whitespace.

    Examples:
        "Perth, WA"             -> "perth, western australia"
        "  New York,  NY  "     -> "new york, new york"
        "London"                -> "london"
        "Sydney, NSW, Australia"-> "sydney, new south wales, australia"
    """
    if not place_name:
        return ''

    # Lower case and strip
    text = place_name.lower().strip()

    # Normalise whitespace within the string
    text = re.sub(r'\s+', ' ', text)

    # Normalise punctuation: ensure comma-space separation
    # "Perth,WA" -> "Perth, WA"
    text = re.sub(r'\s*,\s*', ', ', text)

    # Remove any characters that aren't alphanumeric, space, comma, hyphen, apostrophe
    text = re.sub(r"[^\w\s,'\-]", '', text)

    # Split on commas, expand abbreviations in each segment
    parts = [_expand_abbreviations(part.strip()) for part in text.split(',')]

    # Rejoin
    result = ', '.join(p for p in parts if p)

    # Final whitespace collapse
    result = re.sub(r'\s+', ' ', result).strip()

    return result


def _expand_abbreviations(token: str) -> str:
    """
    Expand abbreviations within a single comma-separated segment.
    Only expands whole words — won't match abbreviations mid-word.

    e.g. "wa"       -> "western australia"
         "perth wa" -> "perth western australia"
         "newark"   -> "newark"  (no match)
    """
    words = token.split()
    expanded = []
    for word in words:
        # Strip any trailing punctuation before lookup
        clean = word.strip("'.,-")
        expansion = _ABBREVIATIONS.get(clean)
        if expansion:
            expanded.append(expansion)
        else:
            expanded.append(word)
    return ' '.join(expanded)