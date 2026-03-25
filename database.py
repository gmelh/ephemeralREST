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
# database.py                                                                 #
################################################################################

"""
Database management module for Astro API.
Handles SQLite operations with connection pooling and caching.

Tables:
    Existing (unchanged):
        locations       — simple location cache keyed by query string (FK target for charts)
        charts          — calculated chart storage

    New (canonical place system):
        canonical_places    — one row per real-world place
        place_aliases       — user-entered variants mapped to canonical places
        place_cache         — Google-derived lat/lon/timezone, expires after 30 days
        place_lookup_log    — audit log of every resolution attempt
"""
import sqlite3
import json
import hashlib
import uuid
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

PLACE_CACHE_EXPIRY_DAYS = 30


class DatabaseManager:
    """Manages database operations with connection pooling"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {str(e)}")
            raise
        finally:
            conn.close()

    def init_database(self):
        """Initialize the SQLite database with all required tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # ------------------------------------------------------------------
            # Existing tables (unchanged)
            # ------------------------------------------------------------------

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS locations
                (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_text        TEXT UNIQUE NOT NULL,
                    query_hash        TEXT UNIQUE NOT NULL,
                    latitude          REAL NOT NULL,
                    longitude         REAL NOT NULL,
                    formatted_address TEXT NOT NULL,
                    timezone          TEXT NOT NULL,
                    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS charts
                (
                    id             TEXT PRIMARY KEY,
                    chart_name     TEXT DEFAULT 'Untitled Chart',
                    datetime_utc   TEXT NOT NULL,
                    datetime_local TEXT NOT NULL,
                    location_id    INTEGER NOT NULL,
                    chart_data     TEXT NOT NULL,
                    chart_hash     TEXT UNIQUE NOT NULL,
                    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count   INTEGER DEFAULT 1,
                    FOREIGN KEY (location_id) REFERENCES locations (id)
                )
            ''')

            # Migrations: add columns to charts if missing from older databases
            cursor.execute("PRAGMA table_info(charts)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'chart_name' not in columns:
                cursor.execute("ALTER TABLE charts ADD COLUMN chart_name TEXT DEFAULT 'Untitled Chart'")
                logger.info("Migration: added chart_name column to charts table")
            if 'chart_type' not in columns:
                cursor.execute("ALTER TABLE charts ADD COLUMN chart_type TEXT NOT NULL DEFAULT 'natal'")
                logger.info("Migration: added chart_type column to charts table")

            # Derived charts — all charts calculated from a primary radix
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS derived_charts
                (
                    id                  TEXT PRIMARY KEY,
                    chart_id            TEXT NOT NULL,
                    secondary_chart_id  TEXT,
                    chart_type          TEXT NOT NULL,
                    chart_name          TEXT DEFAULT 'Untitled',
                    reference_date      TEXT NOT NULL,
                    chart_data          TEXT NOT NULL,
                    chart_hash          TEXT UNIQUE NOT NULL,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count        INTEGER DEFAULT 1,
                    FOREIGN KEY (chart_id)           REFERENCES charts(id),
                    FOREIGN KEY (secondary_chart_id) REFERENCES charts(id)
                )
            ''')


            # ------------------------------------------------------------------
            # API key management tables
            # ------------------------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys
                (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_type        TEXT NOT NULL CHECK (key_type IN ('domain', 'user')),
                    name            TEXT NOT NULL,
                    identifier      TEXT NOT NULL UNIQUE,
                    key_enc         TEXT NOT NULL,
                    key_prefix      TEXT NOT NULL,
                    admin           INTEGER NOT NULL DEFAULT 0,
                    active          INTEGER NOT NULL DEFAULT 1,
                    rate_per_minute INTEGER,
                    rate_per_hour   INTEGER,
                    rate_per_day    INTEGER,
                    output_config   TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS key_class_limits
                (
                    key_type        TEXT PRIMARY KEY CHECK (key_type IN ('domain', 'user', 'wildcard')),
                    rate_per_minute INTEGER NOT NULL DEFAULT 10,
                    rate_per_hour   INTEGER NOT NULL DEFAULT 50,
                    rate_per_day    INTEGER NOT NULL DEFAULT 200,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("INSERT OR IGNORE INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day) VALUES ('domain', 20, 200, 1000)")
            cursor.execute("INSERT OR IGNORE INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day) VALUES ('user', 10, 100, 500)")
            cursor.execute("INSERT OR IGNORE INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day) VALUES ('wildcard', 5, 30, 100)")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_type   ON api_keys(key_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(active)")


            # ------------------------------------------------------------------
            # Registration and verification tables
            # ------------------------------------------------------------------

            # Domain key registration requests — await admin approval
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS registration_requests
                (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id     INTEGER,
                    domain         TEXT NOT NULL UNIQUE,
                    name           TEXT NOT NULL,
                    contact_email  TEXT NOT NULL,
                    reason         TEXT,
                    status      TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending','approved','rejected')),
                    admin_note  TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            # Email verification tokens for user key activation
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications
                (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id  INTEGER NOT NULL,
                    token       TEXT NOT NULL UNIQUE,
                    email       TEXT NOT NULL,
                    used        INTEGER NOT NULL DEFAULT 0,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TIMESTAMP NOT NULL,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reg_requests_status ON registration_requests(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verif_token   ON email_verifications(token)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verif_key     ON email_verifications(api_key_id)")


            # ------------------------------------------------------------------
            # SMTP configuration (single row, upserted by key)
            # ------------------------------------------------------------------
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS smtp_config
                (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ------------------------------------------------------------------
            # New canonical place tables
            # ------------------------------------------------------------------

            # One row per real-world place
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS canonical_places
                (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_key  TEXT UNIQUE NOT NULL,
                    google_place_id TEXT,
                    formatted_name  TEXT NOT NULL,
                    locality        TEXT,
                    admin_area_1    TEXT,
                    admin_area_2    TEXT,
                    country         TEXT,
                    country_code    TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Many user-entered strings mapping to one canonical place
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS place_aliases
                (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias_text          TEXT NOT NULL,
                    normalized_alias    TEXT UNIQUE NOT NULL,
                    canonical_place_id  INTEGER NOT NULL,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (canonical_place_id) REFERENCES canonical_places (id)
                )
            ''')

            # Temporary Google-derived geocode/timezone cache, expires after 30 days
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS place_cache
                (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_place_id  INTEGER UNIQUE NOT NULL,
                    latitude            REAL NOT NULL,
                    longitude           REAL NOT NULL,
                    timezone_id         TEXT NOT NULL,
                    utc_offset_seconds  INTEGER NOT NULL DEFAULT 0,
                    dst_offset_seconds  INTEGER NOT NULL DEFAULT 0,
                    geocode_source      TEXT NOT NULL DEFAULT 'google',
                    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at          TIMESTAMP NOT NULL,
                    FOREIGN KEY (canonical_place_id) REFERENCES canonical_places (id)
                )
            ''')

            # Audit log of every resolution attempt
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS place_lookup_log
                (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    input_text          TEXT NOT NULL,
                    normalized_input    TEXT NOT NULL,
                    matched_alias_id    INTEGER,
                    matched_place_id    INTEGER,
                    cache_hit           INTEGER NOT NULL DEFAULT 0,
                    google_called       INTEGER NOT NULL DEFAULT 0,
                    success             INTEGER NOT NULL DEFAULT 0,
                    error_message       TEXT,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ------------------------------------------------------------------
            # Indexes
            # ------------------------------------------------------------------

            # Existing
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_locations_query_hash    ON locations(query_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_charts_hash             ON charts(chart_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_charts_location         ON charts(location_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_charts_last_accessed    ON charts(last_accessed)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_locations_last_used     ON locations(last_used)')

            # New
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_canonical_places_key    ON canonical_places(normalized_key)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_canonical_places_gid    ON canonical_places(google_place_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_place_aliases_norm      ON place_aliases(normalized_alias)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_place_aliases_place     ON place_aliases(canonical_place_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_place_cache_place       ON place_cache(canonical_place_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_place_cache_expires     ON place_cache(expires_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_place_log_created       ON place_lookup_log(created_at)')

            # Derived charts indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_derived_chart_id        ON derived_charts(chart_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_derived_secondary_id    ON derived_charts(secondary_chart_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_derived_type            ON derived_charts(chart_type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_derived_hash            ON derived_charts(chart_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_derived_last_accessed   ON derived_charts(last_accessed)')

            logger.info("Database initialized successfully")

    # ==========================================================================
    # Existing location cache methods (unchanged — used by charts FK)
    # ==========================================================================

    def get_location_from_cache(self, query_text: str) -> Optional[Dict[str, Any]]:
        """Get location from the simple locations cache by query string."""
        query_hash = hashlib.md5(query_text.lower().strip().encode()).hexdigest()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, latitude, longitude, formatted_address, timezone
                FROM locations
                WHERE query_hash = ?
            ''', (query_hash,))

            result = cursor.fetchone()
            if result:
                cursor.execute('''
                    UPDATE locations SET last_used = CURRENT_TIMESTAMP
                    WHERE query_hash = ?
                ''', (query_hash,))
                return {
                    'id':               result['id'],
                    'latitude':         result['latitude'],
                    'longitude':        result['longitude'],
                    'formatted_address': result['formatted_address'],
                    'timezone':         result['timezone'],
                    'from_cache':       True
                }
        return None

    def save_location_to_cache(self, query_text: str, location_data: Dict[str, Any]) -> int:
        """Save location data to the simple locations cache."""
        query_hash = hashlib.md5(query_text.lower().strip().encode()).hexdigest()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO locations
                (query_text, query_hash, latitude, longitude, formatted_address, timezone)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                query_text,
                query_hash,
                location_data['latitude'],
                location_data['longitude'],
                location_data['formatted_address'],
                location_data['timezone']
            ))
            return cursor.lastrowid

    # ==========================================================================
    # Canonical place methods
    # ==========================================================================

    def get_canonical_place(self, place_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a canonical place row by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, normalized_key, google_place_id, formatted_name,
                       locality, admin_area_1, admin_area_2, country, country_code,
                       created_at, updated_at
                FROM canonical_places WHERE id = ?
            ''', (place_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_canonical_place_id_by_google_id(self, google_place_id: str) -> Optional[int]:
        """Find a canonical place by Google place_id."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM canonical_places WHERE google_place_id = ?',
                (google_place_id,)
            )
            row = cursor.fetchone()
            return row['id'] if row else None

    def get_canonical_place_id_by_key(self, normalized_key: str) -> Optional[int]:
        """Find a canonical place by its normalized key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM canonical_places WHERE normalized_key = ?',
                (normalized_key,)
            )
            row = cursor.fetchone()
            return row['id'] if row else None

    def create_canonical_place(self, normalized_key: str, geo: Dict) -> int:
        """Insert a new canonical place and return its ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO canonical_places
                (normalized_key, google_place_id, formatted_name, locality,
                 admin_area_1, admin_area_2, country, country_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                normalized_key,
                geo.get('google_place_id'),
                geo.get('formatted_name', ''),
                geo.get('locality'),
                geo.get('admin_area_1'),
                geo.get('admin_area_2'),
                geo.get('country'),
                geo.get('country_code'),
            ))
            return cursor.lastrowid

    def update_canonical_place(self, place_id: int, geo: Dict) -> None:
        """Update an existing canonical place with fresh Google data."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE canonical_places
                SET google_place_id = ?,
                    formatted_name  = ?,
                    locality        = ?,
                    admin_area_1    = ?,
                    admin_area_2    = ?,
                    country         = ?,
                    country_code    = ?,
                    updated_at      = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                geo.get('google_place_id'),
                geo.get('formatted_name', ''),
                geo.get('locality'),
                geo.get('admin_area_1'),
                geo.get('admin_area_2'),
                geo.get('country'),
                geo.get('country_code'),
                place_id,
            ))

    # ==========================================================================
    # Place alias methods
    # ==========================================================================

    def get_place_alias(self, normalized_alias: str) -> Optional[Dict[str, Any]]:
        """Look up a place alias by its normalized form."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, alias_text, normalized_alias, canonical_place_id
                FROM place_aliases WHERE normalized_alias = ?
            ''', (normalized_alias,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_place_alias(
            self,
            normalized_alias: str,
            alias_text: str,
            canonical_place_id: int
    ) -> int:
        """
        Create a new alias or return the existing one's ID.
        If the alias already points to a different canonical place,
        it is left unchanged (first-write wins).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM place_aliases WHERE normalized_alias = ?',
                (normalized_alias,)
            )
            existing = cursor.fetchone()
            if existing:
                return existing['id']

            cursor.execute('''
                INSERT INTO place_aliases (alias_text, normalized_alias, canonical_place_id)
                VALUES (?, ?, ?)
            ''', (alias_text, normalized_alias, canonical_place_id))
            return cursor.lastrowid

    # ==========================================================================
    # Place cache methods
    # ==========================================================================

    def get_place_cache(self, canonical_place_id: int) -> Optional[Dict[str, Any]]:
        """
        Return the place cache row if it exists and has not expired.
        Returns None if missing or expired.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, canonical_place_id, latitude, longitude,
                       timezone_id, utc_offset_seconds, dst_offset_seconds,
                       geocode_source, fetched_at, expires_at
                FROM place_cache
                WHERE canonical_place_id = ?
                AND expires_at > datetime('now')
            ''', (canonical_place_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_place_cache(
            self,
            canonical_place_id: int,
            geo: Dict,
            tz: Dict
    ) -> None:
        """
        Insert or replace the place cache row with a fresh 30-day expiry.
        """
        expires_at = datetime.utcnow() + timedelta(days=PLACE_CACHE_EXPIRY_DAYS)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO place_cache
                (canonical_place_id, latitude, longitude, timezone_id,
                 utc_offset_seconds, dst_offset_seconds, geocode_source,
                 fetched_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'google', CURRENT_TIMESTAMP, ?)
                ON CONFLICT(canonical_place_id) DO UPDATE SET
                    latitude           = excluded.latitude,
                    longitude          = excluded.longitude,
                    timezone_id        = excluded.timezone_id,
                    utc_offset_seconds = excluded.utc_offset_seconds,
                    dst_offset_seconds = excluded.dst_offset_seconds,
                    geocode_source     = 'google',
                    fetched_at         = CURRENT_TIMESTAMP,
                    expires_at         = excluded.expires_at
            ''', (
                canonical_place_id,
                geo['latitude'],
                geo['longitude'],
                tz.get('timeZoneId', 'UTC'),
                tz.get('rawOffset', 0),
                tz.get('dstOffset', 0),
                expires_at.isoformat(),
            ))

    def cleanup_expired_place_cache(self) -> int:
        """
        Delete expired place_cache rows.
        canonical_places and place_aliases are left intact.
        Returns the number of rows deleted.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM place_cache WHERE expires_at <= datetime('now')
            ''')
            deleted = cursor.rowcount
            logger.info(f"Cleaned up {deleted} expired place cache rows")
            return deleted

    # ==========================================================================
    # Lookup log
    # ==========================================================================

    def log_place_lookup(self, entry: Dict) -> None:
        """Insert a row into the place_lookup_log table."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO place_lookup_log
                    (input_text, normalized_input, matched_alias_id, matched_place_id,
                     cache_hit, google_called, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    entry.get('input_text', ''),
                    entry.get('normalized_input', ''),
                    entry.get('matched_alias_id'),
                    entry.get('matched_place_id'),
                    1 if entry.get('cache_hit') else 0,
                    1 if entry.get('google_called') else 0,
                    1 if entry.get('success') else 0,
                    entry.get('error_message'),
                ))
        except Exception as e:
            # Logging failure should never surface to the user
            logger.warning(f"Failed to write place_lookup_log: {e}")

    def get_place_lookup_stats(self) -> Dict[str, Any]:
        """Summary statistics from the lookup log."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    COUNT(*)                                AS total_lookups,
                    SUM(cache_hit)                          AS cache_hits,
                    SUM(google_called)                      AS google_calls,
                    SUM(success)                            AS successful,
                    COUNT(*) - SUM(success)                 AS failed
                FROM place_lookup_log
            ''')
            row = cursor.fetchone()
            return dict(row) if row else {}

    # ==========================================================================
    # Chart methods (unchanged)
    # ==========================================================================

    def get_chart_from_cache(self, datetime_utc: datetime, location_id: int) -> Optional[Dict[str, Any]]:
        """Get chart from cache if it exists"""
        chart_key  = f"{datetime_utc.isoformat()}_{location_id}"
        chart_hash = hashlib.md5(chart_key.encode()).hexdigest()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, chart_name, chart_data FROM charts WHERE chart_hash = ?
            ''', (chart_hash,))

            result = cursor.fetchone()
            if result:
                cursor.execute('''
                    UPDATE charts
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count  = access_count + 1
                    WHERE id = ?
                ''', (result['id'],))
                return {
                    'id':         result['id'],
                    'chart_name': result['chart_name'],
                    'chart_data': json.loads(result['chart_data']),
                    'from_cache': True
                }
        return None

    def save_chart_to_cache(
            self,
            datetime_utc: datetime,
            datetime_local: datetime,
            location_id: int,
            chart_data: Dict[str, Any],
            chart_name: str = 'Untitled Chart',
            house_system: str = None
    ) -> str:
        """
        Save chart data to cache and return chart ID.
        Hash includes datetime + location + chart_name + house_system.
        Different house systems for the same chart produce separate records.
        """
        house_key  = house_system or 'none'
        chart_key  = f"{datetime_utc.isoformat()}_{location_id}_{chart_name}_{house_key}"
        chart_hash = hashlib.md5(chart_key.encode()).hexdigest()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM charts WHERE chart_hash = ?', (chart_hash,))
            existing = cursor.fetchone()

            if existing:
                chart_id = existing['id']
                cursor.execute('''
                    UPDATE charts
                    SET chart_data     = ?,
                        datetime_utc   = ?,
                        datetime_local = ?,
                        last_accessed  = CURRENT_TIMESTAMP
                    WHERE chart_hash = ?
                ''', (
                    json.dumps(chart_data),
                    datetime_utc.isoformat(),
                    datetime_local.isoformat(),
                    chart_hash
                ))
                logger.info(f"Updated existing chart {chart_id}")
            else:
                chart_id = str(uuid.uuid4())
                cursor.execute('''
                    INSERT INTO charts
                    (id, chart_name, datetime_utc, datetime_local, location_id, chart_data, chart_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    chart_id,
                    chart_name,
                    datetime_utc.isoformat(),
                    datetime_local.isoformat(),
                    location_id,
                    json.dumps(chart_data),
                    chart_hash
                ))
                logger.info(f"Created new chart {chart_id}")

            return chart_id


    def update_chart_data_by_id(
            self,
            chart_id: str,
            chart_data: Dict[str, Any],
            datetime_utc: datetime,
            datetime_local: datetime,
    ) -> bool:
        """
        Force-update chart_data for a known chart UUID.
        Used by the recalc flow — preserves chart_id, chart_name,
        location_id, chart_hash, and access_count.

        Returns True if the chart was found and updated, False if not found.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE charts
                SET chart_data     = ?,
                    datetime_utc   = ?,
                    datetime_local = ?,
                    last_accessed  = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    json.dumps(chart_data),
                    datetime_utc.isoformat(),
                    datetime_local.isoformat(),
                    chart_id,
                )
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Recalculated chart {chart_id} — data updated in place")
            else:
                logger.warning(f"Recalc attempted on unknown chart_id {chart_id}")
            return updated

    def get_chart_by_id(self, chart_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a chart by its ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT c.id,
                       c.chart_name,
                       c.datetime_utc,
                       c.datetime_local,
                       c.chart_data,
                       c.access_count,
                       l.latitude,
                       l.longitude,
                       l.formatted_address,
                       l.timezone
                FROM charts c
                JOIN locations l ON c.location_id = l.id
                WHERE c.id = ?
            ''', (chart_id,))

            result = cursor.fetchone()
            if result:
                cursor.execute('''
                    UPDATE charts
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count  = access_count + 1
                    WHERE id = ?
                ''', (chart_id,))
                return {
                    'id':            result['id'],
                    'chart_name':    result['chart_name'],
                    'datetime_utc':  result['datetime_utc'],
                    'datetime_local': result['datetime_local'],
                    'chart_data':    json.loads(result['chart_data']),
                    'access_count':  result['access_count'],
                    'location': {
                        'latitude':          result['latitude'],
                        'longitude':         result['longitude'],
                        'formatted_address': result['formatted_address'],
                        'timezone':          result['timezone']
                    }
                }
        return None


    # ==========================================================================
    # Derived charts methods
    # ==========================================================================

    def save_derived_chart(
            self,
            chart_id: str,
            chart_type: str,
            reference_date: str,
            chart_data: Dict[str, Any],
            chart_name: str = 'Untitled',
            secondary_chart_id: str = None,
    ) -> str:
        """
        Save a derived chart and return its UUID.
        Hash includes chart_id + chart_type + reference_date + secondary_chart_id
        so the same derivation always resolves to the same record.
        Re-running the same calculation updates data in place, preserving the UUID.
        """
        hash_key   = f"{chart_id}_{chart_type}_{reference_date}_{secondary_chart_id or ''}"
        chart_hash = hashlib.md5(hash_key.encode()).hexdigest()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM derived_charts WHERE chart_hash = ?',
                (chart_hash,)
            )
            existing = cursor.fetchone()

            if existing:
                derived_id = existing['id']
                cursor.execute('''
                    UPDATE derived_charts
                    SET chart_data    = ?,
                        chart_name    = ?,
                        last_accessed = CURRENT_TIMESTAMP
                    WHERE chart_hash = ?
                ''', (json.dumps(chart_data), chart_name, chart_hash))
                logger.info(f"Updated derived chart {derived_id} ({chart_type})")
            else:
                derived_id = str(uuid.uuid4())
                cursor.execute('''
                    INSERT INTO derived_charts
                    (id, chart_id, secondary_chart_id, chart_type, chart_name,
                     reference_date, chart_data, chart_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    derived_id,
                    chart_id,
                    secondary_chart_id,
                    chart_type,
                    chart_name,
                    reference_date,
                    json.dumps(chart_data),
                    chart_hash,
                ))
                logger.info(f"Created derived chart {derived_id} ({chart_type})")

            return derived_id

    def get_derived_chart_by_id(self, derived_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a derived chart by its UUID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT d.id, d.chart_id, d.secondary_chart_id, d.chart_type,
                       d.chart_name, d.reference_date, d.chart_data,
                       d.created_at, d.last_accessed, d.access_count
                FROM derived_charts d
                WHERE d.id = ?
            ''', (derived_id,))

            row = cursor.fetchone()
            if row:
                cursor.execute('''
                    UPDATE derived_charts
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count  = access_count + 1
                    WHERE id = ?
                ''', (derived_id,))
                return {
                    'id':                 row['id'],
                    'chart_id':           row['chart_id'],
                    'secondary_chart_id': row['secondary_chart_id'],
                    'chart_type':         row['chart_type'],
                    'chart_name':         row['chart_name'],
                    'reference_date':     row['reference_date'],
                    'chart_data':         json.loads(row['chart_data']),
                    'created_at':         row['created_at'],
                    'last_accessed':      row['last_accessed'],
                    'access_count':       row['access_count'],
                }
        return None

    def get_derived_charts_for_radix(
            self,
            chart_id: str,
            chart_type: str = None
    ) -> list:
        """
        List all derived charts for a given radix chart.
        Optionally filter by chart_type.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if chart_type:
                cursor.execute('''
                    SELECT id, chart_type, chart_name, reference_date,
                           created_at, last_accessed, access_count
                    FROM derived_charts
                    WHERE chart_id = ? AND chart_type = ?
                    ORDER BY reference_date DESC
                ''', (chart_id, chart_type))
            else:
                cursor.execute('''
                    SELECT id, chart_type, chart_name, reference_date,
                           created_at, last_accessed, access_count
                    FROM derived_charts
                    WHERE chart_id = ?
                    ORDER BY chart_type, reference_date DESC
                ''', (chart_id,))

            return [dict(row) for row in cursor.fetchall()]

    def delete_derived_chart(self, derived_id: str) -> bool:
        """Delete a derived chart by UUID. Returns True if deleted."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM derived_charts WHERE id = ?', (derived_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted derived chart {derived_id}")
            return deleted




    # ==========================================================================
    # SMTP configuration methods
    # ==========================================================================

    SMTP_KEYS = [
        'host', 'port', 'user', 'password', 'from_addr',
        'use_tls', 'use_ssl', 'admin_email', 'base_url',
    ]

    def get_smtp_config(self) -> Dict[str, str]:
        """Return all SMTP config key/value pairs as a dict."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM smtp_config')
            return {row['key']: row['value'] for row in cursor.fetchall()}

    def set_smtp_config(self, config: Dict[str, str]) -> None:
        """Upsert SMTP config key/value pairs."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in config.items():
                if key not in self.SMTP_KEYS:
                    continue
                cursor.execute("""
                    INSERT INTO smtp_config (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value      = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                """, (key, str(value) if value is not None else ''))

    def clear_smtp_config(self) -> None:
        """Delete all SMTP config rows."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM smtp_config')

    # ==========================================================================
    # Registration request methods
    # ==========================================================================

    def create_registration_request(
            self,
            api_key_id: int,
            domain: str,
            name: str,
            contact_email: str = '',
            reason: str = None
    ) -> int:
        """Insert a new domain registration request."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO registration_requests
                (api_key_id, domain, name, contact_email, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (api_key_id, domain, name, contact_email, reason))
            return cursor.lastrowid

    def get_registration_requests(self, status: str = None) -> list:
        """Return registration requests, optionally filtered by status."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("""
                    SELECT r.*, k.key_prefix
                    FROM registration_requests r
                    LEFT JOIN api_keys k ON r.api_key_id = k.id
                    WHERE r.status = ?
                    ORDER BY r.created_at DESC
                """, (status,))
            else:
                cursor.execute("""
                    SELECT r.*, k.key_prefix
                    FROM registration_requests r
                    LEFT JOIN api_keys k ON r.api_key_id = k.id
                    ORDER BY r.created_at DESC
                """)
            return [dict(row) for row in cursor.fetchall()]

    def get_registration_request_by_id(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single registration request by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, k.key_prefix
                FROM registration_requests r
                LEFT JOIN api_keys k ON r.api_key_id = k.id
                WHERE r.id = ?
            """, (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_registration_request(
            self,
            request_id: int,
            status: str,
            admin_note: str = None
    ) -> bool:
        """Update the status of a registration request."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE registration_requests
                SET status     = ?,
                    admin_note = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, admin_note, request_id))
            return cursor.rowcount > 0

    def domain_registration_exists(self, domain: str) -> bool:
        """Check whether a domain already has a registration request or key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM registration_requests WHERE domain = ?",
                (domain,)
            )
            return cursor.fetchone() is not None

    # ==========================================================================
    # Email verification methods
    # ==========================================================================

    def create_email_verification(
            self,
            api_key_id: int,
            email: str,
            token: str,
            expiry_hours: int = 24
    ) -> int:
        """Insert a new email verification token."""
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(hours=expiry_hours)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO email_verifications
                (api_key_id, token, email, expires_at)
                VALUES (?, ?, ?, ?)
            """, (api_key_id, token, email, expires_at.isoformat()))
            return cursor.lastrowid

    def get_email_verification(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a valid (unused, unexpired) email verification record by token.
        Returns None if not found, used, or expired.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, api_key_id, token, email, used, created_at, expires_at
                FROM email_verifications
                WHERE token = ?
                AND used = 0
                AND expires_at > datetime('now')
            """, (token,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_email_verification_used(self, token: str) -> bool:
        """Mark a verification token as used."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE email_verifications SET used = 1 WHERE token = ?",
                (token,)
            )
            return cursor.rowcount > 0

    # ==========================================================================
    # API key management methods
    # ==========================================================================

    def create_api_key(
            self,
            key_type: str,
            name: str,
            identifier: str,
            key_enc: str,
            key_prefix: str,
            admin: bool = False,
            active: bool = True,
            rate_per_minute: int = None,
            rate_per_hour: int = None,
            rate_per_day: int = None,
            output_config: dict = None,
    ) -> int:
        """Insert a new API key record. Returns the new row id."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO api_keys
                (key_type, name, identifier, key_enc, key_prefix, admin, active,
                 rate_per_minute, rate_per_hour, rate_per_day, output_config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key_type, name, identifier, key_enc, key_prefix,
                1 if admin else 0,
                1 if active else 0,
                rate_per_minute, rate_per_hour, rate_per_day,
                json.dumps(output_config) if output_config else None,
            ))
            return cursor.lastrowid

    def get_api_keys_by_prefix(self, prefix: str) -> list:
        """Find active key records matching a key prefix."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, key_type, name, identifier, key_enc, key_prefix,
                       admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                       output_config
                FROM api_keys
                WHERE key_prefix = ? AND active = 1
            """, (prefix,))
            rows = cursor.fetchall()
            return [self._api_key_row_to_dict(r) for r in rows]

    def get_api_key_by_id(self, key_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single API key record by integer ID (includes key_enc)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, key_type, name, identifier, key_enc, key_prefix,
                       admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                       output_config, created_at, updated_at
                FROM api_keys WHERE id = ?
            """, (key_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get('output_config'):
                try:
                    d['output_config'] = json.loads(d['output_config'])
                except Exception:
                    d['output_config'] = {}
            return d

    def get_all_api_keys(self, include_inactive: bool = False) -> list:
        """Return all API key records (without key_enc)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT id, key_type, name, identifier, key_prefix,
                       admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                       output_config, created_at, updated_at
                FROM api_keys
            """
            if not include_inactive:
                query += " WHERE active = 1"
            query += " ORDER BY key_type, name"
            cursor.execute(query)
            rows = cursor.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d['output_config']:
                    d['output_config'] = json.loads(d['output_config'])
                result.append(d)
            return result

    def update_api_key(self, key_id: int, **fields) -> bool:
        """Update one or more fields on an API key record."""
        allowed = {
            'name', 'key_enc', 'key_prefix', 'admin', 'active',
            'rate_per_minute', 'rate_per_hour', 'rate_per_day', 'output_config'
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        if 'output_config' in updates and isinstance(updates['output_config'], dict):
            updates['output_config'] = json.dumps(updates['output_config'])

        set_clause = ', '.join(f"{k} = ?" for k in updates)
        values     = list(updates.values()) + [key_id]

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE api_keys SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_api_key(self, key_id: int) -> bool:
        """Permanently delete an API key record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM api_keys WHERE id = ?', (key_id,))
            return cursor.rowcount > 0

    def get_key_class_limits(self, key_type: str) -> Dict[str, int]:
        """Return the class-level default rate limits for a key type."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT rate_per_minute, rate_per_hour, rate_per_day FROM key_class_limits WHERE key_type = ?',
                (key_type,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {'rate_per_minute': 10, 'rate_per_hour': 50, 'rate_per_day': 200}

    def set_key_class_limits(
            self,
            key_type: str,
            rate_per_minute: int,
            rate_per_hour: int,
            rate_per_day: int
    ) -> None:
        """Update class-level default rate limits."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key_type) DO UPDATE SET
                    rate_per_minute = excluded.rate_per_minute,
                    rate_per_hour   = excluded.rate_per_hour,
                    rate_per_day    = excluded.rate_per_day,
                    updated_at      = CURRENT_TIMESTAMP
            """, (key_type, rate_per_minute, rate_per_hour, rate_per_day))

    def _api_key_row_to_dict(self, row) -> Dict[str, Any]:
        """Convert a raw api_keys row to a dict, parsing output_config JSON."""
        d = dict(row)
        if d.get('output_config'):
            try:
                d['output_config'] = json.loads(d['output_config'])
            except (json.JSONDecodeError, TypeError):
                d['output_config'] = {}
        else:
            d['output_config'] = {}
        return d

    # ==========================================================================
    # Stats and cleanup
    # ==========================================================================

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics across all tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM locations')
            location_count = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*), SUM(access_count) FROM charts')
            chart_stats    = cursor.fetchone()
            chart_count    = chart_stats[0]
            total_accesses = chart_stats[1] or 0

            cursor.execute('SELECT COUNT(*) FROM canonical_places')
            canonical_count = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM place_aliases')
            alias_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM place_cache WHERE expires_at > datetime('now')")
            active_cache_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM place_cache WHERE expires_at <= datetime('now')")
            expired_cache_count = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM derived_charts')
            derived_count = cursor.fetchone()[0]

            return {
                'locations_cached':     location_count,
                'charts_cached':        chart_count,
                'derived_charts':       derived_count,
                'total_chart_accesses': total_accesses,
                'canonical_places':     canonical_count,
                'place_aliases':        alias_count,
                'place_cache_active':   active_cache_count,
                'place_cache_expired':  expired_cache_count,
            }

    def cleanup_old_cache(self, days: int = 90) -> int:
        """Remove old chart and location cache entries"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM charts
                WHERE last_accessed < datetime('now', '-' || ? || ' days')
            ''', (days,))
            deleted_charts = cursor.rowcount

            cursor.execute('''
                DELETE FROM locations
                WHERE last_used < datetime('now', '-' || ? || ' days')
                AND id NOT IN (SELECT DISTINCT location_id FROM charts)
            ''', (days,))
            deleted_locations = cursor.rowcount

            logger.info(f"Cleaned up {deleted_charts} charts and {deleted_locations} locations")
            return deleted_charts + deleted_locations