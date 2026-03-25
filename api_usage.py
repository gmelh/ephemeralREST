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
# api_usage.py                                                                #
################################################################################

"""
API usage tracking module for Astro API
Tracks and limits Google Maps API usage
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from functools import wraps
from flask import jsonify

logger = logging.getLogger(__name__)


class APIUsageTracker:
    """Tracks API usage and enforces monthly limits"""

    def __init__(self, usage_file: str, max_monthly_requests: int):
        self.usage_file = usage_file
        self.max_monthly_requests = max_monthly_requests
        self.load_usage_data()

    def load_usage_data(self):
        """Load usage data from file"""
        try:
            with open(self.usage_file, 'r') as f:
                self.usage_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.usage_data = {
                'current_month': datetime.now().strftime('%Y-%m'),
                'count': 0,
                'disabled_until': None
            }
            self.save_usage_data()

    def save_usage_data(self):
        """Save usage data to file"""
        try:
            with open(self.usage_file, 'w') as f:
                json.dump(self.usage_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save usage data: {str(e)}")

    def check_and_increment(self) -> bool:
        """Check if API call is allowed and increment counter"""
        current_month = datetime.now().strftime('%Y-%m')

        # Reset count if new month
        if self.usage_data['current_month'] != current_month:
            self.usage_data = {
                'current_month': current_month,
                'count': 0,
                'disabled_until': None
            }
            logger.info(f"Reset API usage counter for new month: {current_month}")

        # Check if API is disabled
        if self.usage_data.get('disabled_until'):
            disabled_until = datetime.fromisoformat(self.usage_data['disabled_until'])
            if datetime.now() < disabled_until:
                return False
            else:
                self.usage_data['disabled_until'] = None
                logger.info("API re-enabled after disabled period")

        # Check if we've exceeded the limit
        if self.usage_data['count'] >= self.max_monthly_requests:
            # Disable until next month
            next_month = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1)
            self.usage_data['disabled_until'] = next_month.isoformat()
            self.save_usage_data()
            logger.warning(f"API usage limit exceeded. Disabled until {next_month}")
            return False

        # Increment count
        self.usage_data['count'] += 1
        self.save_usage_data()

        # Log the API usage
        logger.info(f"Google API call #{self.usage_data['count']} for month {current_month}")

        return True

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get current usage statistics"""
        return {
            'current_month': self.usage_data['current_month'],
            'requests_used': self.usage_data['count'],
            'requests_remaining': max(0, self.max_monthly_requests - self.usage_data['count']),
            'api_disabled': self.usage_data.get('disabled_until') is not None,
            'disabled_until': self.usage_data.get('disabled_until')
        }

    def reset_usage(self):
        """Manually reset usage counter (admin function)"""
        current_month = datetime.now().strftime('%Y-%m')
        self.usage_data = {
            'current_month': current_month,
            'count': 0,
            'disabled_until': None
        }
        self.save_usage_data()
        logger.info("API usage counter manually reset")


def rate_limit_google_api(usage_tracker: APIUsageTracker):
    """Decorator to check API usage limits before making Google API calls"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not usage_tracker.check_and_increment():
                stats = usage_tracker.get_usage_stats()
                return jsonify({
                    'error': 'Google API usage limit exceeded for this month',
                    'message': f'API disabled until next month.',
                    'usage_stats': stats
                }), 429
            return f(*args, **kwargs)

        return decorated_function

    return decorator