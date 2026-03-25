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
# middleware.py                                                               #
################################################################################

"""
Middleware for Astro API
Handles request ID tracking, logging, and error handling
"""
import uuid
import logging
import traceback
from flask import request, jsonify, g, has_request_context
from werkzeug.exceptions import HTTPException
from datetime import datetime

logger = logging.getLogger(__name__)


def setup_middleware(app):
    """Setup all middleware for the Flask app"""

    @app.before_request
    def before_request():
        """Add request ID and start time to each request"""
        g.request_id = str(uuid.uuid4())
        g.start_time = datetime.now()

        logger.info(f"[{g.request_id}] {request.method} {request.path} - Started")

    @app.after_request
    def after_request(response):
        """Log request completion and add custom headers"""
        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id

            # Calculate request duration
            if hasattr(g, 'start_time'):
                duration = (datetime.now() - g.start_time).total_seconds()
                logger.info(
                    f"[{g.request_id}] {request.method} {request.path} - "
                    f"Status: {response.status_code} - Duration: {duration:.3f}s"
                )

        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        """Handle HTTP exceptions"""
        request_id = getattr(g, 'request_id', 'unknown')

        logger.warning(
            f"[{request_id}] HTTP {e.code} - {e.name}: {e.description}"
        )

        return jsonify({
            'error': e.name,
            'status': e.code,
            'message': e.description,
            'request_id': request_id
        }), e.code

    @app.errorhandler(Exception)
    def handle_exception(e):
        """Handle uncaught exceptions"""
        request_id = getattr(g, 'request_id', 'unknown')

        # Log full traceback
        logger.error(
            f"[{request_id}] Unhandled exception: {str(e)}\n{traceback.format_exc()}"
        )

        # Don't expose internal errors in production
        if app.config.get('DEBUG'):
            error_detail = str(e)
        else:
            error_detail = 'An internal error occurred'

        return jsonify({
            'error': 'Internal Server Error',
            'status': 500,
            'message': error_detail,
            'request_id': request_id
        }), 500

    @app.errorhandler(404)
    def not_found(e):
        """Handle 404 errors"""
        request_id = getattr(g, 'request_id', 'unknown')

        return jsonify({
            'error': 'Not Found',
            'status': 404,
            'message': f'Endpoint {request.path} not found',
            'request_id': request_id
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        """Handle 405 errors"""
        request_id = getattr(g, 'request_id', 'unknown')

        return jsonify({
            'error': 'Method Not Allowed',
            'status': 405,
            'message': f'Method {request.method} not allowed for {request.path}',
            'request_id': request_id
        }), 405


def setup_request_logging():
    """Configure detailed request logging"""

    class RequestFormatter(logging.Formatter):
        """Custom formatter that includes request ID"""

        def format(self, record):
            # FIXED: Check if we're in a request context before accessing g
            if has_request_context() and hasattr(g, 'request_id'):
                record.request_id = g.request_id
            else:
                record.request_id = 'N/A'

            return super().format(record)

    return RequestFormatter(
        '[%(asctime)s] [%(request_id)s] %(levelname)s in %(module)s: %(message)s'
    )