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
# app_links.py                                                                #
################################################################################

"""
The rules for the web addresses a domain key may point sign-up email links at (its link
origins). Shared by the API (routes.py) and key_manager.py, so both apply the same ones.
"""

from urllib.parse import urlsplit

LOCAL_HOSTS = ('localhost', '127.0.0.1')


def normalise_origin(url: str):
    """'https://Astro.Example.com:443/x' -> 'https://astro.example.com'. None if not http(s)."""
    try:
        parts = urlsplit(url)
        host  = (parts.hostname or '').lower()
        port  = parts.port
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not host:
        return None
    default = 443 if parts.scheme == 'https' else 80
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else '')


def check_origin_for_key(origin: str, domain: str):
    """
    (normalised origin, None) if a domain key for `domain` may register `origin`, else
    (None, reason). It must be the key's own domain or a subdomain of it, over https, with no
    path. localhost is allowed for development: a token sent there only reaches the person's
    own machine.
    """
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None, 'not a web address'
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        return None, 'an origin is just scheme, host and port, with no path'
    normal = normalise_origin(origin.strip())
    if not normal:
        return None, 'needs to start with https://'
    host = urlsplit(normal).hostname
    if host in LOCAL_HOSTS:
        return normal, None
    if not normal.startswith('https://'):
        return None, 'needs https (http is only for localhost)'
    domain = (domain or '').lower().strip('.')
    if host != domain and not host.endswith('.' + domain):
        return None, f"isn't {domain} or one of its subdomains"
    return normal, None
