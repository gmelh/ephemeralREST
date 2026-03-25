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
# gunicorn_config.py                                                          #
################################################################################

"""
Gunicorn configuration for production deployment
"""
import multiprocessing
import os

# Server socket
bind = f"{os.environ.get('FLASK_HOST', '0.0.0.0')}:{os.environ.get('FLASK_PORT', '5000')}"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 120
keepalive = 5

# Maximum requests a worker will process before restarting
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = 'access.log'
errorlog = 'error.log'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'ephemeral_api'

# Server mechanics
daemon = False
pidfile = 'gunicorn.pid'
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = 'path/to/keyfile'
# certfile = 'path/to/certfile'

def on_starting(server):
    """Called just before the master process is initialized"""
    print("Gunicorn server starting...")

def on_reload(server):
    """Called to recycle workers during a reload"""
    print("Gunicorn server reloading...")

def when_ready(server):
    """Called just after the server is started"""
    print(f"Gunicorn server ready. Listening on {bind}")

def worker_int(worker):
    """Called just after a worker exited on SIGINT or SIGQUIT"""
    print(f"Worker {worker.pid} received INT or QUIT signal")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal"""
    print(f"Worker {worker.pid} received SIGABRT signal")