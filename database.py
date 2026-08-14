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
Handles SQLite (default) or MySQL operations, selected via DB_TYPE, with
connection pooling and caching.

Tables:
    Existing (unchanged):
        locations       — simple location cache keyed by query string (FK target for charts)
        charts          — calculated chart storage

    New (canonical place system):
        canonical_places    — one row per real-world place
        place_aliases       — user-entered variants mapped to canonical places
        place_cache         — Google-derived lat/lon/timezone, expires after 30 days
        place_lookup_log    — audit log of every resolution attempt

MySQL support:
    Set DB_TYPE=mysql (plus MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/
    MYSQL_DATABASE) to run against MySQL instead of SQLite. This is a
    MySQL-only mode, not a dual-dialect abstraction: when MySQL is selected,
    a separate schema (_init_schema_mysql) is created and a thin
    connection/cursor wrapper (_MySQLConnectionWrapper/_MySQLCursorWrapper)
    translates the handful of SQLite-specific idioms used throughout this
    module (the '?' placeholder style, INSERT OR IGNORE/REPLACE, the
    ON CONFLICT...DO UPDATE upsert syntax, and datetime('now', ...) helpers)
    into their MySQL equivalents. Everything else — every method below
    get_connection() — is written once and shared by both backends.
"""
import os
import re
import sqlite3
import json
import hashlib
import uuid
import logging
import decimal
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

PLACE_CACHE_EXPIRY_DAYS = 30


# ==============================================================================
# MySQL compatibility layer
#
# Only imported/used when DB_TYPE=mysql. Translates the SQLite idioms this
# module was originally written against into MySQL-compatible SQL, and wraps
# rows/cursors so the calling code (row['col'], row[0], dict(row), lastrowid,
# rowcount, etc.) behaves the same regardless of backend.
# ==============================================================================

# MySQL expression standing in for SQLite's datetime('now'). A native
# UTC_TIMESTAMP() value (not a formatted string) so it compares directly
# against the datetime objects _fmt_dt() passes through for MySQL, and so
# it contains no '%' characters — mysql-connector's own placeholder
# scanner treats a literal '%s' anywhere in the SQL text (including inside
# a format-string literal) as a bind parameter, so DATE_FORMAT(...,'%s')
# is not safe to use here.
_MYSQL_NOW_EXPR = "UTC_TIMESTAMP(6)"


def _translate_sql_mysql(sql: str) -> str:
    """
    Translate a SQLite-flavoured SQL statement into MySQL-compatible SQL.
    This is a small, targeted translation covering exactly the idioms used
    in this file — not a general SQL dialect converter.
    """
    out = sql

    # SQLite relative-date arithmetic used by cleanup_old_cache()
    out = out.replace(
        "datetime('now', '-' || ? || ' days')",
        "DATE_SUB(UTC_TIMESTAMP(), INTERVAL ? DAY)"
    )
    # SQLite "now" comparisons against expires_at / last_accessed columns
    out = out.replace("datetime('now')", _MYSQL_NOW_EXPR)

    # MySQL's default utf8mb4 collations are already case-insensitive, so
    # this SQLite-specific clause can simply be dropped.
    out = out.replace('COLLATE NOCASE', '')

    # Upsert syntax
    out = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT IGNORE INTO', out, flags=re.IGNORECASE)
    out = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'REPLACE INTO', out, flags=re.IGNORECASE)
    out = re.sub(
        r'ON CONFLICT\s*\([^)]*\)\s*DO UPDATE SET\s*(.*)',
        lambda m: 'ON DUPLICATE KEY UPDATE ' + m.group(1),
        out,
        flags=re.IGNORECASE | re.DOTALL
    )
    out = re.sub(r'excluded\.(\w+)', r'VALUES(\1)', out, flags=re.IGNORECASE)

    # Placeholder style — done last since none of the substitutions above
    # introduce a literal '?' that shouldn't be converted.
    out = out.replace('?', '%s')

    return out


def _normalise_mysql_value(value):
    """
    Normalise a single MySQL connector value so downstream code (much of
    which assumes SQLite's plain str/int/float/None row values) doesn't
    need to know it's talking to MySQL.
    """
    if isinstance(value, decimal.Decimal):
        # SUM()/AVG() over integer columns come back as Decimal in MySQL
        # but as plain int/float from SQLite.
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, date):
        return value.isoformat()
    return value


class _MySQLRow(dict):
    """
    dict subclass that also supports positional index access, mirroring
    sqlite3.Row (which supports both row['col'] and row[0]).
    """
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _MySQLCursorWrapper:
    """Wraps a mysql-connector cursor to translate SQL and normalise rows."""

    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, sql, params=()):
        self._cursor.execute(_translate_sql_mysql(sql), params or ())
        return self

    def executemany(self, sql, seq_of_params):
        self._cursor.executemany(_translate_sql_mysql(sql), list(seq_of_params))
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return _MySQLRow((k, _normalise_mysql_value(v)) for k, v in row.items())

    def fetchall(self):
        return [
            _MySQLRow((k, _normalise_mysql_value(v)) for k, v in row.items())
            for row in self._cursor.fetchall()
        ]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _MySQLConnectionWrapper:
    """
    Wraps a mysql-connector connection so it can be used as a drop-in
    replacement for a sqlite3.Connection in this module — including the
    conn.execute(...) convenience method sqlite3 provides directly on the
    connection object (not just on a cursor).
    """

    def __init__(self, mysql_config: Dict[str, Any]):
        import mysql.connector  # imported lazily — only required for DB_TYPE=mysql
        self._conn = mysql.connector.connect(
            host=mysql_config['host'],
            port=int(mysql_config.get('port', 3306)),
            user=mysql_config['user'],
            password=mysql_config.get('password', ''),
            database=mysql_config['database'],
            autocommit=False,
        )

    def cursor(self):
        return _MySQLCursorWrapper(self._conn.cursor(dictionary=True))

    def execute(self, sql, params=()):
        """Mimics sqlite3.Connection.execute() — execute directly on the connection."""
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


class DatabaseManager:
    """Manages database operations with connection pooling. Backs onto
    either SQLite (default) or MySQL, selected via db_type."""

    def __init__(
            self,
            db_path: str = None,
            db_type: str = None,
            mysql_config: Optional[Dict[str, Any]] = None,
    ):
        self.db_type = (db_type or os.environ.get('DB_TYPE', 'sqlite')).strip().lower()
        if self.db_type not in ('sqlite', 'mysql'):
            logger.warning(f"Unknown DB_TYPE '{self.db_type}' — falling back to sqlite")
            self.db_type = 'sqlite'

        if self.db_type == 'mysql':
            self.mysql_config = mysql_config or {
                'host':     os.environ.get('MYSQL_HOST', 'localhost'),
                'port':     int(os.environ.get('MYSQL_PORT', '3306')),
                'user':     os.environ.get('MYSQL_USER', ''),
                'password': os.environ.get('MYSQL_PASSWORD', ''),
                'database': os.environ.get('MYSQL_DATABASE', ''),
            }
            if not self.mysql_config.get('user') or not self.mysql_config.get('database'):
                raise ValueError(
                    "DB_TYPE=mysql requires at least MYSQL_USER and MYSQL_DATABASE "
                    "to be set (see .env)."
                )
            self.db_path = None
        else:
            self.mysql_config = None
            self.db_path = db_path or os.environ.get('DATABASE_PATH', 'ephemeral.db')

        self.init_database()

    def _fmt_dt(self, dt: datetime):
        """
        Format a datetime for storage in an expires_at-style column.
        SQLite gets Python's normal ISO8601 string, matching its opaque
        TEXT/TIMESTAMP columns. MySQL gets the raw datetime object back —
        the connector serialises it natively into its DATETIME/TIMESTAMP
        wire format, which is what UTC_TIMESTAMP(6) comparisons expect.
        """
        if self.db_type == 'mysql':
            return dt
        return dt.isoformat()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        if self.db_type == 'mysql':
            conn = _MySQLConnectionWrapper(self.mysql_config)
        else:
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
        """Initialize the database schema for whichever backend is configured."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if self.db_type == 'mysql':
                self._init_schema_mysql(cursor)
            else:
                self._init_schema_sqlite(cursor)
            logger.info(f"Database initialized successfully ({self.db_type})")

    def _init_schema_sqlite(self, cursor):
        """Initialize the SQLite database with all required tables"""

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
                key_type        TEXT NOT NULL DEFAULT 'user',
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
                password_hash         TEXT,
                must_change_password  INTEGER NOT NULL DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrations for api_keys table
        cursor.execute("PRAGMA table_info(api_keys)")
        _api_key_cols = {column[1]: column for column in cursor.fetchall()}

        # Fix: if key_type column has a NOT NULL constraint with no default,
        # SQLite cannot ALTER COLUMN — we must recreate the table.
        # Detect this by checking whether the column has a dflt_value.
        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        _needs_recreate = False
        if 'key_type' in _api_key_cols:
            _col = _api_key_cols['key_type']
            # dflt_value is index 4; if NOT NULL (index 3 == 1) and no default
            if _col[3] == 1 and _col[4] is None:
                _needs_recreate = True

        if _needs_recreate:
            logger.info("Migration: recreating api_keys table to fix key_type constraint")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys_new
                (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_type        TEXT NOT NULL DEFAULT 'user',
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
                    password_hash         TEXT,
                    must_change_password  INTEGER NOT NULL DEFAULT 1,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                INSERT INTO api_keys_new
                    (id, key_type, name, identifier, key_enc, key_prefix,
                     admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                     output_config, created_at, updated_at)
                SELECT
                    id,
                    COALESCE(key_type, 'user'),
                    name, identifier, key_enc, key_prefix,
                    admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                    output_config, created_at, updated_at
                FROM api_keys
            """)
            cursor.execute("DROP TABLE api_keys")
            cursor.execute("ALTER TABLE api_keys_new RENAME TO api_keys")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_identifier ON api_keys(identifier)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix)")
            logger.info("Migration: api_keys table recreated successfully")
            # Refresh column info after recreation
            cursor.execute("PRAGMA table_info(api_keys)")
            _api_key_cols = {column[1]: column for column in cursor.fetchall()}

        # Add password columns if missing
        if 'password_hash' not in _api_key_cols:
            cursor.execute("ALTER TABLE api_keys ADD COLUMN password_hash TEXT")
            logger.info("Migration: added password_hash column to api_keys")
        if 'must_change_password' not in _api_key_cols:
            cursor.execute("ALTER TABLE api_keys ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")
            logger.info("Migration: added must_change_password column to api_keys")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS key_class_limits
            (
                key_type        TEXT PRIMARY KEY,
                rate_per_minute INTEGER NOT NULL DEFAULT 10,
                rate_per_hour   INTEGER NOT NULL DEFAULT 50,
                rate_per_day    INTEGER NOT NULL DEFAULT 200,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("INSERT OR IGNORE INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day) VALUES ('user', 10, 100, 500)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_type   ON api_keys(key_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(active)")

        # ------------------------------------------------------------------
        # Federated service registry — admin-curated list of known external
        # services a key can be granted access to. `slug` is what
        # api_key_services.service actually stores; this table exists so
        # the portal can present a real list (with names/descriptions)
        # instead of admins typing free-text service names from memory.
        # ------------------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS federated_services
            (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                slug         TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                description  TEXT,
                base_url     TEXT,
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_federated_services_active ON federated_services(active)")

        # ------------------------------------------------------------------
        # Federated service access grants.
        #
        # ephemeral.rest can act as the shared identity provider for a
        # cluster of companion services that read this same database:
        # holding a key is enough to authenticate against ephemeral.rest
        # itself, and a key can additionally be granted access to any
        # number of arbitrarily-named external services, each of which
        # checks its own grants directly against this table rather than
        # calling back to ephemeral.rest per request. Service names are
        # free text chosen by whoever operates the companion service —
        # nothing here is specific to any particular deployment.
        # ------------------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_key_services
            (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id     INTEGER NOT NULL REFERENCES api_keys(id),
                service    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(key_id, service)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_key_services_key     ON api_key_services(key_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_key_services_service ON api_key_services(service)")


        # ------------------------------------------------------------------
        # Registration and verification tables
        # ------------------------------------------------------------------

        # registration_requests table removed — flat self-serve registration,
        # no admin approval workflow.

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

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verif_token   ON email_verifications(token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verif_key     ON email_verifications(api_key_id)")


        # ------------------------------------------------------------------
        # Login — 2FA codes and trusted devices
        # ------------------------------------------------------------------

        # Short-lived numeric codes emailed during the 2FA login step
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_2fa_codes
            (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key_id  INTEGER NOT NULL,
                code        TEXT NOT NULL,
                used        INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_login_2fa_key ON login_2fa_codes(api_key_id)")

        # Trusted-device tokens — allow skipping 2FA on recognised machines.
        # Token is stored as a portal cookie; lifetime is configurable via
        # TRUSTED_DEVICE_DAYS (default 28).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trusted_devices
            (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key_id  INTEGER NOT NULL,
                token       TEXT NOT NULL UNIQUE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trusted_device_token ON trusted_devices(token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trusted_device_key   ON trusted_devices(api_key_id)")


        # ------------------------------------------------------------------
        # SMTP configuration (single row, upserted by key)
        # ------------------------------------------------------------------
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS smtp_config
            (
                `key`      TEXT PRIMARY KEY,
                `value`    TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ------------------------------------------------------------------
        # Portal settings — configurable admin portal behaviour
        # ------------------------------------------------------------------
        # Simple key/value store. Defaults are baked into the application;
        # only values that differ from defaults are stored here.

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portal_settings
            (
                `key`      TEXT PRIMARY KEY,
                `value`    TEXT NOT NULL,
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

        # Email templates
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_templates
            (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            VARCHAR(64) UNIQUE NOT NULL,
                bg_color        VARCHAR(16)  NOT NULL DEFAULT '#f4f4f4',
                panel_color     VARCHAR(16)  NOT NULL DEFAULT '#ffffff',
                text_color      VARCHAR(16)  NOT NULL DEFAULT '#1a1a1a',
                content_width   INTEGER      NOT NULL DEFAULT 600,
                header_align    VARCHAR(8)   NOT NULL DEFAULT 'left',
                subject         TEXT,
                header_text     TEXT,
                body_text       TEXT,
                footer_text     TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ------------------------------------------------------------------
        # Permanent chart archive — never cleaned up, append-only
        # Records every chart ever calculated for potential recalculation.
        # INSERT OR IGNORE on chart_id means the first calculation wins;
        # recalcs update the live charts table but never touch this record.
        # ------------------------------------------------------------------
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chart_archive
            (
                chart_id            TEXT PRIMARY KEY,
                chart_name          TEXT NOT NULL,
                datetime_utc        TEXT NOT NULL,
                datetime_local      TEXT NOT NULL,
                location            TEXT NOT NULL,
                first_calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_archive_name ON chart_archive(chart_name)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_archive_datetime ON chart_archive(datetime_utc)'
        )

        # ------------------------------------------------------------------
        # Chart recalculation history — linked to chart_archive
        # Records every recalculation of a chart, preserving what changed.
        # A note field allows the reason to be recorded (e.g. "Birth time
        # confirmed from birth certificate").
        # ------------------------------------------------------------------
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chart_recalculations
            (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                chart_id        TEXT NOT NULL
                                    REFERENCES chart_archive(chart_id),
                chart_name      TEXT NOT NULL,
                datetime_utc    TEXT NOT NULL,
                datetime_local  TEXT NOT NULL,
                location        TEXT NOT NULL,
                note            TEXT,
                recalculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_recalc_chart_id '
            'ON chart_recalculations(chart_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_recalc_at '
            'ON chart_recalculations(recalculated_at)'
        )

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS views
            (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                view_id       TEXT UNIQUE NOT NULL,
                key_id        INTEGER NOT NULL REFERENCES api_keys(id),
                data          TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Migration: add last_accessed to views if missing from older databases
        cursor.execute("PRAGMA table_info(views)")
        view_columns = [column[1] for column in cursor.fetchall()]
        if 'last_accessed' not in view_columns:
            cursor.execute(
                "ALTER TABLE views ADD COLUMN last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            )
            # Back-fill from updated_at so existing rows get a sensible value
            cursor.execute(
                "UPDATE views SET last_accessed = updated_at WHERE last_accessed IS NULL"
            )
            logger.info("Migration: added last_accessed column to views table")

        # ------------------------------------------------------------------
        # GeoNames cities5000 — offline geocoding / autocomplete source
        # ------------------------------------------------------------------

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cities
            (
                geoname_id   INTEGER PRIMARY KEY,
                name         TEXT NOT NULL,
                ascii_name   TEXT NOT NULL,
                country_code TEXT NOT NULL,
                admin1_code  TEXT,
                latitude     REAL NOT NULL,
                longitude    REAL NOT NULL,
                timezone_id  TEXT NOT NULL,
                population   INTEGER
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cities_import_meta
            (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                filename    TEXT NOT NULL,
                row_count   INTEGER NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_cities_ascii  ON cities(ascii_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_cities_name   ON cities(name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_cities_cc     ON cities(country_code)')

    def _mysql_create_index(self, cursor, sql: str):
        """
        MySQL's CREATE INDEX has no IF NOT EXISTS clause. Swallow the
        resulting "duplicate key name" error on repeat runs so schema
        init stays idempotent, matching the CREATE TABLE IF NOT EXISTS
        semantics used everywhere else in this file.
        """
        try:
            cursor.execute(sql)
        except Exception as e:
            msg = str(e).lower()
            if 'duplicate key name' in msg or 'already exists' in msg:
                return
            raise

    def _init_schema_mysql(self, cursor):
        """
        Initialize the MySQL database with all required tables.

        This is a fresh-schema creation (final column set already applied)
        rather than a port of the SQLite migration history above — MySQL
        support is new, so there is no legacy MySQL data to migrate from.
        TEXT/BLOB columns that need a PRIMARY KEY, UNIQUE, or plain index
        are sized as VARCHAR instead, since MySQL requires an explicit key
        length for any indexed TEXT/BLOB column.
        """
        charset = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS locations
            (
                id                INT AUTO_INCREMENT PRIMARY KEY,
                query_text        VARCHAR(255) UNIQUE NOT NULL,
                query_hash        VARCHAR(32) UNIQUE NOT NULL,
                latitude          DOUBLE NOT NULL,
                longitude         DOUBLE NOT NULL,
                formatted_address TEXT NOT NULL,
                timezone          VARCHAR(64) NOT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS charts
            (
                id             VARCHAR(36) PRIMARY KEY,
                chart_name     VARCHAR(255) DEFAULT 'Untitled Chart',
                chart_type     VARCHAR(32) NOT NULL DEFAULT 'natal',
                datetime_utc   VARCHAR(64) NOT NULL,
                datetime_local VARCHAR(64) NOT NULL,
                location_id    INT NOT NULL,
                chart_data     LONGTEXT NOT NULL,
                chart_hash     VARCHAR(32) UNIQUE NOT NULL,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count   INT DEFAULT 1,
                FOREIGN KEY (location_id) REFERENCES locations (id)
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS derived_charts
            (
                id                  VARCHAR(36) PRIMARY KEY,
                chart_id            VARCHAR(36) NOT NULL,
                secondary_chart_id  VARCHAR(36),
                chart_type          VARCHAR(32) NOT NULL,
                chart_name          VARCHAR(255) DEFAULT 'Untitled',
                reference_date      TEXT NOT NULL,
                chart_data          LONGTEXT NOT NULL,
                chart_hash          VARCHAR(32) UNIQUE NOT NULL,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count        INT DEFAULT 1,
                FOREIGN KEY (chart_id)           REFERENCES charts(id),
                FOREIGN KEY (secondary_chart_id) REFERENCES charts(id)
            ) {charset}
        ''')

        # ------------------------------------------------------------------
        # API key management tables
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS api_keys
            (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                key_type        VARCHAR(32) NOT NULL DEFAULT 'user',
                name            TEXT NOT NULL,
                identifier      VARCHAR(255) NOT NULL UNIQUE,
                key_enc         TEXT NOT NULL,
                key_prefix      VARCHAR(32) NOT NULL,
                admin           INT NOT NULL DEFAULT 0,
                active          INT NOT NULL DEFAULT 1,
                rate_per_minute INT,
                rate_per_hour   INT,
                rate_per_day    INT,
                output_config   LONGTEXT,
                password_hash         TEXT,
                must_change_password  INT NOT NULL DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS key_class_limits
            (
                key_type        VARCHAR(32) PRIMARY KEY,
                rate_per_minute INT NOT NULL DEFAULT 10,
                rate_per_hour   INT NOT NULL DEFAULT 50,
                rate_per_day    INT NOT NULL DEFAULT 200,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        """)

        cursor.execute(
            "INSERT IGNORE INTO key_class_limits (key_type, rate_per_minute, rate_per_hour, rate_per_day) "
            "VALUES ('user', 10, 100, 500)"
        )

        self._mysql_create_index(cursor, "CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix)")
        self._mysql_create_index(cursor, "CREATE INDEX idx_api_keys_type   ON api_keys(key_type)")
        self._mysql_create_index(cursor, "CREATE INDEX idx_api_keys_active ON api_keys(active)")

        # ------------------------------------------------------------------
        # Federated service registry — admin-curated list of known external
        # services a key can be granted access to. `slug` is what
        # api_key_services.service actually stores; VARCHAR(32) to match
        # that column's size.
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS federated_services
            (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                slug         VARCHAR(32) UNIQUE NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                description  TEXT,
                base_url     TEXT,
                active       INT NOT NULL DEFAULT 1,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        """)
        self._mysql_create_index(cursor, "CREATE INDEX idx_federated_services_active ON federated_services(active)")

        # ------------------------------------------------------------------
        # Federated service access grants.
        #
        # ephemeral.rest can act as the shared identity provider for a
        # cluster of companion services that read this same database:
        # holding a key is enough to authenticate against ephemeral.rest
        # itself, and a key can additionally be granted access to any
        # number of arbitrarily-named external services, each of which
        # checks its own grants directly against this table rather than
        # calling back to ephemeral.rest per request. Service names are
        # free text chosen by whoever operates the companion service —
        # nothing here is specific to any particular deployment.
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS api_key_services
            (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                key_id     INT NOT NULL REFERENCES api_keys(id),
                service    VARCHAR(32) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(key_id, service)
            ) {charset}
        """)
        self._mysql_create_index(cursor, "CREATE INDEX idx_key_services_key     ON api_key_services(key_id)")
        self._mysql_create_index(cursor, "CREATE INDEX idx_key_services_service ON api_key_services(service)")

        # ------------------------------------------------------------------
        # Registration and verification tables
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS email_verifications
            (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                api_key_id  INT NOT NULL,
                token       VARCHAR(255) NOT NULL UNIQUE,
                email       TEXT NOT NULL,
                used        INT NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
            ) {charset}
        """)
        self._mysql_create_index(cursor, "CREATE INDEX idx_email_verif_key ON email_verifications(api_key_id)")

        # ------------------------------------------------------------------
        # Login — 2FA codes and trusted devices
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS login_2fa_codes
            (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                api_key_id  INT NOT NULL,
                code        TEXT NOT NULL,
                used        INT NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
            ) {charset}
        """)
        self._mysql_create_index(cursor, "CREATE INDEX idx_login_2fa_key ON login_2fa_codes(api_key_id)")

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS trusted_devices
            (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                api_key_id  INT NOT NULL,
                token       VARCHAR(255) NOT NULL UNIQUE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
            ) {charset}
        """)
        self._mysql_create_index(cursor, "CREATE INDEX idx_trusted_device_key ON trusted_devices(api_key_id)")

        # ------------------------------------------------------------------
        # SMTP configuration and portal settings (single-row-per-key stores)
        # `key` is a reserved word in MySQL — backtick-quoted throughout,
        # which also works fine on SQLite.
        # ------------------------------------------------------------------
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS smtp_config
            (
                `key`      VARCHAR(64) PRIMARY KEY,
                `value`    TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS portal_settings
            (
                `key`      VARCHAR(64) PRIMARY KEY,
                `value`    TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        """)

        # ------------------------------------------------------------------
        # Canonical place tables
        # ------------------------------------------------------------------
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS canonical_places
            (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                normalized_key  VARCHAR(255) UNIQUE NOT NULL,
                google_place_id VARCHAR(255),
                formatted_name  TEXT NOT NULL,
                locality        TEXT,
                admin_area_1    TEXT,
                admin_area_2    TEXT,
                country         TEXT,
                country_code    VARCHAR(8),
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS place_aliases
            (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                alias_text          TEXT NOT NULL,
                normalized_alias    VARCHAR(255) UNIQUE NOT NULL,
                canonical_place_id  INT NOT NULL,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (canonical_place_id) REFERENCES canonical_places (id)
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS place_cache
            (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                canonical_place_id  INT UNIQUE NOT NULL,
                latitude            DOUBLE NOT NULL,
                longitude           DOUBLE NOT NULL,
                timezone_id         VARCHAR(64) NOT NULL,
                utc_offset_seconds  INT NOT NULL DEFAULT 0,
                dst_offset_seconds  INT NOT NULL DEFAULT 0,
                geocode_source      VARCHAR(32) NOT NULL DEFAULT 'google',
                fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at          TIMESTAMP NOT NULL,
                FOREIGN KEY (canonical_place_id) REFERENCES canonical_places (id)
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS place_lookup_log
            (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                input_text          TEXT NOT NULL,
                normalized_input    TEXT NOT NULL,
                matched_alias_id    INT,
                matched_place_id    INT,
                cache_hit           INT NOT NULL DEFAULT 0,
                google_called       INT NOT NULL DEFAULT 0,
                success             INT NOT NULL DEFAULT 0,
                error_message       TEXT,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        # ------------------------------------------------------------------
        # Indexes
        # ------------------------------------------------------------------
        self._mysql_create_index(cursor, 'CREATE INDEX idx_charts_location         ON charts(location_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_charts_last_accessed    ON charts(last_accessed)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_locations_last_used     ON locations(last_used)')

        self._mysql_create_index(cursor, 'CREATE INDEX idx_canonical_places_gid    ON canonical_places(google_place_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_place_aliases_place     ON place_aliases(canonical_place_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_place_cache_place       ON place_cache(canonical_place_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_place_cache_expires     ON place_cache(expires_at)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_place_log_created       ON place_lookup_log(created_at)')

        self._mysql_create_index(cursor, 'CREATE INDEX idx_derived_chart_id        ON derived_charts(chart_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_derived_secondary_id    ON derived_charts(secondary_chart_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_derived_type            ON derived_charts(chart_type)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_derived_last_accessed   ON derived_charts(last_accessed)')

        # Email templates
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS email_templates
            (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                name            VARCHAR(64) UNIQUE NOT NULL,
                bg_color        VARCHAR(16)  NOT NULL DEFAULT '#f4f4f4',
                panel_color     VARCHAR(16)  NOT NULL DEFAULT '#ffffff',
                text_color      VARCHAR(16)  NOT NULL DEFAULT '#1a1a1a',
                content_width   INT          NOT NULL DEFAULT 600,
                header_align    VARCHAR(8)   NOT NULL DEFAULT 'left',
                subject         TEXT,
                header_text     TEXT,
                body_text       TEXT,
                footer_text     TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        # Permanent chart archive
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS chart_archive
            (
                chart_id            VARCHAR(36) PRIMARY KEY,
                chart_name          VARCHAR(255) NOT NULL,
                datetime_utc        VARCHAR(64) NOT NULL,
                datetime_local      TEXT NOT NULL,
                location            TEXT NOT NULL,
                first_calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_archive_name     ON chart_archive(chart_name)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_archive_datetime ON chart_archive(datetime_utc)')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS chart_recalculations
            (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                chart_id        VARCHAR(36) NOT NULL REFERENCES chart_archive(chart_id),
                chart_name      TEXT NOT NULL,
                datetime_utc    TEXT NOT NULL,
                datetime_local  TEXT NOT NULL,
                location        TEXT NOT NULL,
                note            TEXT,
                recalculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_recalc_chart_id ON chart_recalculations(chart_id)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_recalc_at       ON chart_recalculations(recalculated_at)')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS views
            (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                view_id       VARCHAR(36) UNIQUE NOT NULL,
                key_id        INT NOT NULL REFERENCES api_keys(id),
                data          LONGTEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        # ------------------------------------------------------------------
        # GeoNames cities5000 — offline geocoding / autocomplete source
        # ------------------------------------------------------------------
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS cities
            (
                geoname_id   INT PRIMARY KEY,
                name         VARCHAR(255) NOT NULL,
                ascii_name   VARCHAR(255) NOT NULL,
                country_code VARCHAR(8) NOT NULL,
                admin1_code  TEXT,
                latitude     DOUBLE NOT NULL,
                longitude    DOUBLE NOT NULL,
                timezone_id  VARCHAR(64) NOT NULL,
                population   INT
            ) {charset}
        ''')

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS cities_import_meta
            (
                id          INT PRIMARY KEY CHECK (id = 1),
                filename    TEXT NOT NULL,
                row_count   INT NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) {charset}
        ''')

        self._mysql_create_index(cursor, 'CREATE INDEX idx_cities_ascii  ON cities(ascii_name)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_cities_name   ON cities(name)')
        self._mysql_create_index(cursor, 'CREATE INDEX idx_cities_cc     ON cities(country_code)')

    # ==========================================================================
    # View methods
    # ==========================================================================

    def save_view(self, view_id: str, key_id: int, data: str) -> bool:
        """
        Insert or replace a view blob. data is a raw JSON string.
        Returns True on success.
        """
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO views (view_id, key_id, data, created_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(view_id) DO UPDATE SET
                    data       = excluded.data,
                    updated_at = CURRENT_TIMESTAMP
            ''', (view_id, key_id, data))
        return True

    def get_view(self, view_id: str) -> Optional[Dict[str, Any]]:
        """Return a view record by UUID, or None if not found.
        Stamps last_accessed on every successful read for expiry tracking.
        """
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT view_id, key_id, data, created_at, updated_at, last_accessed '
                'FROM views WHERE view_id = ?',
                (view_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                'UPDATE views SET last_accessed = CURRENT_TIMESTAMP WHERE view_id = ?',
                (view_id,)
            )
        return {
            'view_id':       row['view_id'],
            'key_id':        row['key_id'],
            'data':          row['data'],
            'created_at':    row['created_at'],
            'updated_at':    row['updated_at'],
            'last_accessed': row['last_accessed'],
        }

    # ==========================================================================
    # Email template methods
    # ==========================================================================

    def get_email_template(self, name: str) -> Optional[Dict[str, Any]]:
        """Return stored overrides for a named template, or None if not customised."""
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT * FROM email_templates WHERE name = ?', (name,)
            ).fetchone()
            return dict(row) if row else None

    def set_email_template(self, name: str, fields: Dict[str, Any]) -> bool:
        """Insert or update a named email template."""
        allowed = {
            'bg_color', 'panel_color', 'text_color', 'content_width',
            'header_align', 'subject', 'header_text', 'body_text', 'footer_text',
        }
        safe = {k: v for k, v in fields.items() if k in allowed}
        if not safe:
            return False
        with self.get_connection() as conn:
            existing = conn.execute(
                'SELECT id FROM email_templates WHERE name = ?', (name,)
            ).fetchone()
            if existing:
                sets = ', '.join(f'{k} = ?' for k in safe)
                conn.execute(
                    f'UPDATE email_templates SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE name = ?',
                    [*safe.values(), name]
                )
            else:
                cols = 'name, ' + ', '.join(safe)
                vals = ', '.join('?' * (len(safe) + 1))
                conn.execute(
                    f'INSERT INTO email_templates ({cols}) VALUES ({vals})',
                    [name, *safe.values()]
                )
            return True

    def reset_email_template(self, name: str) -> bool:
        """Delete stored overrides for a named template, reverting to code defaults."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM email_templates WHERE name = ?', (name,))
            return True

    # ==========================================================================
    # Key admin methods
    # ==========================================================================

    def set_key_admin(self, key_id: int, admin: bool) -> bool:
        """Grant or revoke admin status on a key."""
        with self.get_connection() as conn:
            conn.execute(
                'UPDATE api_keys SET admin = ? WHERE id = ?',
                (1 if admin else 0, key_id)
            )
            return True

    def count_admin_keys(self) -> int:
        """Return the number of currently active admin keys."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM api_keys WHERE admin = 1 AND active = 1"
            ).fetchone()
            return row[0] if row else 0

    def is_database_empty(self) -> bool:
        """Return True if no API keys exist at all — used to gate the /setup endpoint."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()
            return (row[0] if row else 0) == 0

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
                self._fmt_dt(expires_at),
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

    def delete_chart(self, chart_id: str) -> bool:
        """
        Delete a natal chart and all its derived charts in a single transaction.
        Derived charts are removed first to satisfy the FK constraint.
        Returns True if the natal chart was found and deleted.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM derived_charts WHERE chart_id = ?', (chart_id,))
            derived_count = cursor.rowcount
            cursor.execute('DELETE FROM charts WHERE id = ?', (chart_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(
                    f"Deleted chart {chart_id} and {derived_count} derived chart(s)"
                )
        return deleted




    def archive_chart(
        self,
        chart_id:       str,
        chart_name:     str,
        datetime_utc:   str,
        datetime_local: str,
        location:       str,
    ) -> bool:
        """
        Permanently record a chart in the archive.

        Uses INSERT OR IGNORE so that recalculations (which reuse the same
        chart_id) never overwrite the original entry. The archive reflects
        the first time a chart was calculated, not the most recent.

        Call this from the route layer where the resolved location string
        (formatted_address) is already available.

        Returns True if a new archive record was created, False if one
        already existed for this chart_id (i.e. a recalculation).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO chart_archive
                (chart_id, chart_name, datetime_utc, datetime_local, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chart_id, chart_name, datetime_utc, datetime_local, location)
            )
            created = cursor.rowcount > 0
        if created:
            logger.info(f"Chart archived: {chart_id} ('{chart_name}', {location})")
        return created

    def record_recalculation(
        self,
        chart_id:       str,
        chart_name:     str,
        datetime_utc:   str,
        datetime_local: str,
        location:       str,
        note:           str = None,
    ) -> int:
        """
        Record a recalculation against an existing chart_archive entry.

        Every call appends a new row — the full history of recalculations
        is preserved in insertion order. The note field should describe why
        the recalculation was performed (e.g. "Birth time confirmed from
        birth certificate", "Location corrected to suburb level").

        Returns the new recalculation row id.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chart_recalculations
                (chart_id, chart_name, datetime_utc, datetime_local, location, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chart_id, chart_name, datetime_utc, datetime_local, location, note)
            )
            row_id = cursor.lastrowid
        logger.info(
            f"Recalculation recorded: chart={chart_id} "
            f"(name='{chart_name}', location={location})"
        )
        return row_id

    def get_recalculations(self, chart_id: str) -> list:
        """
        Return all recalculation records for a given chart_id,
        ordered chronologically (oldest first).
        """
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, chart_id, chart_name, datetime_utc, datetime_local,
                       location, note, recalculated_at
                FROM chart_recalculations
                WHERE chart_id = ?
                ORDER BY recalculated_at ASC
                """,
                (chart_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def search_archive(
        self,
        chart_name: str = None,
        location:   str = None,
        limit:      int = 50,
    ) -> list:
        """
        Search the chart archive by name and/or location (both optional,
        case-insensitive LIKE match). Returns records ordered by
        first_calculated_at DESC.
        """
        clauses = []
        params  = []
        if chart_name:
            clauses.append("chart_name LIKE ?")
            params.append(f"%{chart_name}%")
        if location:
            clauses.append("location LIKE ?")
            params.append(f"%{location}%")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT chart_id, chart_name, datetime_utc, datetime_local,
                       location, first_calculated_at
                FROM chart_archive
                {where}
                ORDER BY first_calculated_at DESC
                LIMIT ?
                """,
                params
            ).fetchall()

        return [dict(r) for r in rows]

    # ==========================================================================
    # SMTP configuration methods
    # ==========================================================================

    SMTP_KEYS = [
        'host', 'port', 'user', 'password', 'from_addr',
        'use_tls', 'use_ssl', 'admin_email', 'base_url', 'portal_url',
    ]

    def get_smtp_config(self) -> Dict[str, str]:
        """Return all SMTP config key/value pairs as a dict."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT `key`, `value` FROM smtp_config')
            return {row['key']: row['value'] for row in cursor.fetchall()}

    def set_smtp_config(self, config: Dict[str, str]) -> None:
        """Upsert SMTP config key/value pairs."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in config.items():
                if key not in self.SMTP_KEYS:
                    continue
                cursor.execute("""
                    INSERT INTO smtp_config (`key`, `value`)
                    VALUES (?, ?)
                    ON CONFLICT(`key`) DO UPDATE SET
                        `value`    = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                """, (key, str(value) if value is not None else ''))

    def clear_smtp_config(self) -> None:
        """Delete all SMTP config rows."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM smtp_config')

    # ==========================================================================
    # Portal settings methods
    # ==========================================================================

    # Default values — used when a key is absent from the database.
    PORTAL_SETTINGS_DEFAULTS: Dict[str, Any] = {
        'site_name':             'ephemeralREST',
        'site_version':          '1.0',
        'session_timeout':       1800,
        'logout_redirect_url':   '/login.php',
        'allow_admin_promotion': True,
        'trusted_device_days':   28,
        'portal_url':            '',
    }

    def get_portal_settings(self) -> Dict[str, Any]:
        """
        Return all portal settings, merging database values over defaults.
        Numeric and boolean values are cast to their correct types.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT `key`, `value` FROM portal_settings')
            stored = {row['key']: row['value'] for row in cursor.fetchall()}

        result = dict(self.PORTAL_SETTINGS_DEFAULTS)
        for key, value in stored.items():
            default = self.PORTAL_SETTINGS_DEFAULTS.get(key)
            if isinstance(default, bool):
                result[key] = value.lower() in ('true', '1', 'yes')
            elif isinstance(default, int):
                try:
                    result[key] = int(value)
                except (ValueError, TypeError):
                    pass
            else:
                result[key] = value
        return result

    def set_portal_settings(self, settings: Dict[str, Any]) -> None:
        """Upsert portal settings key/value pairs."""
        allowed = set(self.PORTAL_SETTINGS_DEFAULTS.keys())
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in settings.items():
                if key not in allowed:
                    continue
                str_value = str(value).lower() if isinstance(value, bool) else str(value)
                cursor.execute("""
                    INSERT INTO portal_settings (`key`, `value`)
                    VALUES (?, ?)
                    ON CONFLICT(`key`) DO UPDATE SET `value` = excluded.value,
                                                      updated_at = CURRENT_TIMESTAMP
                """, (key, str_value))

    def reset_portal_setting(self, key: str) -> bool:
        """Delete a portal setting row, reverting it to its built-in default."""
        if key not in self.PORTAL_SETTINGS_DEFAULTS:
            return False
        with self.get_connection() as conn:
            conn.execute('DELETE FROM portal_settings WHERE `key` = ?', (key,))
        return True

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
            """, (api_key_id, token, email, self._fmt_dt(expires_at)))
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
    # Login — 2FA codes
    # ==========================================================================

    def create_2fa_code(self, api_key_id: int, code: str, expiry_minutes: int = 10) -> int:
        """Insert a new 2FA login code. Returns the new row id."""
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO login_2fa_codes (api_key_id, code, expires_at)
                VALUES (?, ?, ?)
            """, (api_key_id, code, self._fmt_dt(expires_at)))
            return cursor.lastrowid

    def get_valid_2fa_code(self, api_key_id: int, code: str) -> Optional[Dict[str, Any]]:
        """Fetch a valid (unused, unexpired) 2FA code for the given key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, api_key_id, code, used, created_at, expires_at
                FROM login_2fa_codes
                WHERE api_key_id = ?
                AND code = ?
                AND used = 0
                AND expires_at > datetime('now')
            """, (api_key_id, code))
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_2fa_code_used(self, code_id: int) -> bool:
        """Mark a 2FA code as used."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE login_2fa_codes SET used = 1 WHERE id = ?",
                (code_id,)
            )
            return cursor.rowcount > 0

    def invalidate_2fa_codes(self, api_key_id: int) -> None:
        """Mark all outstanding 2FA codes for a key as used (e.g. before issuing a new one)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE login_2fa_codes SET used = 1 WHERE api_key_id = ? AND used = 0",
                (api_key_id,)
            )

    # ==========================================================================
    # Login — trusted devices
    # ==========================================================================

    def create_trusted_device(self, api_key_id: int, token: str, expiry_days: int = 28) -> int:
        """Insert a new trusted-device token. Returns the new row id."""
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(days=expiry_days)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trusted_devices (api_key_id, token, expires_at)
                VALUES (?, ?, ?)
            """, (api_key_id, token, self._fmt_dt(expires_at)))
            return cursor.lastrowid

    def get_trusted_device(self, token: str) -> Optional[Dict[str, Any]]:
        """Fetch a valid (unexpired) trusted-device record by token."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, api_key_id, token, created_at, expires_at
                FROM trusted_devices
                WHERE token = ?
                AND expires_at > datetime('now')
            """, (token,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_trusted_device(self, token: str) -> bool:
        """Remove a trusted-device token (e.g. on logout / forget-this-device)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trusted_devices WHERE token = ?", (token,))
            return cursor.rowcount > 0

    def delete_trusted_devices_for_key(self, api_key_id: int) -> None:
        """Remove all trusted-device tokens for a key (e.g. on password reset)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trusted_devices WHERE api_key_id = ?", (api_key_id,))

    # ==========================================================================
    # API key management methods
    # ==========================================================================

    def create_api_key(
            self,
            name: str,
            identifier: str,
            key_enc: str,
            key_prefix: str,
            key_type: str = 'user',
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
                (name, identifier, key_enc, key_prefix, key_type, admin, active,
                 rate_per_minute, rate_per_hour, rate_per_day, output_config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name, identifier, key_enc, key_prefix, key_type,
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
                       output_config, must_change_password
                FROM api_keys
                WHERE key_prefix = ? AND active = 1
            """, (prefix,))
            rows = cursor.fetchall()
            return [self._api_key_row_to_dict(r) for r in rows]

    def get_api_key_by_identifier(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Fetch a single API key record by identifier (email), case-insensitive."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, key_type, name, identifier, key_enc, key_prefix,
                       admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                       output_config, password_hash, must_change_password,
                       created_at, updated_at
                FROM api_keys WHERE identifier = ? COLLATE NOCASE
            """, (identifier,))
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

    def get_api_key_by_id(self, key_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single API key record by integer ID (includes key_enc and password_hash)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, key_type, name, identifier, key_enc, key_prefix,
                       admin, active, rate_per_minute, rate_per_hour, rate_per_day,
                       output_config, password_hash, must_change_password,
                       created_at, updated_at
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
                       output_config, must_change_password, created_at, updated_at
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
            'name', 'key_enc', 'key_prefix', 'admin', 'active', 'key_type',
            'rate_per_minute', 'rate_per_hour', 'rate_per_day', 'output_config',
            'password_hash', 'must_change_password'
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
        """
        Permanently delete an API key record and any service grants it holds.
        Service grants are removed first — MySQL's FK constraint on
        api_key_services.key_id would otherwise reject the delete.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM api_key_services WHERE key_id = ?', (key_id,))
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

    # ==========================================================================
    # Federated service access grants
    #
    # ephemeral.rest can act as the shared identity provider for a cluster
    # of companion services built by anyone self-hosting this software.
    # Holding a key is enough to authenticate against ephemeral.rest
    # itself — this table only governs additional, arbitrarily-named
    # external services, each of which is expected to check its own
    # grants directly against this shared database (see the "Federated
    # services" section of the architecture doc for the intended pattern).
    # ==========================================================================

    def grant_key_service(self, key_id: int, service: str) -> bool:
        """Grant a key access to a named external service. Idempotent — granting twice is a no-op."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO api_key_services (key_id, service) VALUES (?, ?)",
                (key_id, service)
            )
            return True

    def grant_key_services(self, key_id: int, services: List[str]) -> List[str]:
        """
        Grant a key access to multiple named external services in one call.
        Idempotent per service — already-granted services are left as-is.
        Returns the key's full list of granted services after the operation.
        """
        if services:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    "INSERT OR IGNORE INTO api_key_services (key_id, service) VALUES (?, ?)",
                    [(key_id, s) for s in services]
                )
        return self.get_key_services(key_id)

    def revoke_key_service(self, key_id: int, service: str) -> bool:
        """Revoke a key's access to a service. Returns True if a grant was removed."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM api_key_services WHERE key_id = ? AND service = ?",
                (key_id, service)
            )
            return cursor.rowcount > 0

    def revoke_key_services(self, key_id: int, services: List[str]) -> List[str]:
        """
        Revoke a key's access to multiple named services in one call.
        Returns the key's full list of remaining granted services.
        """
        if services:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ', '.join('?' * len(services))
                cursor.execute(
                    f"DELETE FROM api_key_services WHERE key_id = ? AND service IN ({placeholders})",
                    [key_id, *services]
                )
        return self.get_key_services(key_id)

    def set_key_services(self, key_id: int, services: List[str]) -> List[str]:
        """
        Replace a key's entire set of granted services with exactly
        `services` — grants not in the list are revoked, missing ones are
        added. Suited to a checkbox-list admin UI ("save the services this
        key should have access to") where the caller sends the full
        desired state rather than an incremental grant/revoke. Pass an
        empty list to revoke all of a key's service access.
        Returns the key's full list of granted services after the operation.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_key_services WHERE key_id = ?", (key_id,))
            if services:
                cursor.executemany(
                    "INSERT INTO api_key_services (key_id, service) VALUES (?, ?)",
                    [(key_id, s) for s in services]
                )
        return self.get_key_services(key_id)

    def key_has_service(self, key_id: int, service: str) -> bool:
        """Check whether a key is granted access to a specific service."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM api_key_services WHERE key_id = ? AND service = ?",
                (key_id, service)
            ).fetchone()
            return row is not None

    def get_key_services(self, key_id: int) -> list:
        """Return the list of service names a key is granted access to."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT service FROM api_key_services WHERE key_id = ? ORDER BY service",
                (key_id,)
            ).fetchall()
            return [r['service'] for r in rows]

    def get_keys_for_service(self, service: str) -> list:
        """
        Return active API key records (without key_enc) granted access to a
        given service — used by admin views to see who can call what.
        """
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT k.id, k.key_type, k.name, k.identifier, k.key_prefix,
                       k.admin, k.active, k.rate_per_minute, k.rate_per_hour,
                       k.rate_per_day, k.created_at
                FROM api_keys k
                JOIN api_key_services s ON s.key_id = k.id
                WHERE s.service = ? AND k.active = 1
                ORDER BY k.name
            """, (service,)).fetchall()
            return [dict(r) for r in rows]

    # ==========================================================================
    # Federated service registry
    #
    # Admin-curated list of known external services, so the portal can show
    # a real list instead of free text. api_key_services.service continues
    # to store the plain slug string — this registry is a layer on top,
    # not a replacement; grant/revoke/check methods above are unaffected.
    # ==========================================================================

    def create_federated_service(
            self,
            slug: str,
            display_name: str,
            description: str = None,
            base_url: str = None,
    ) -> int:
        """Register a new federated service. Raises on duplicate slug (UNIQUE)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO federated_services (slug, display_name, description, base_url)
                VALUES (?, ?, ?, ?)
            """, (slug, display_name, description, base_url))
            return cursor.lastrowid

    def get_federated_services(self, active_only: bool = False) -> list:
        """List registered federated services, newest first by name."""
        with self.get_connection() as conn:
            sql = "SELECT * FROM federated_services"
            if active_only:
                sql += " WHERE active = 1"
            sql += " ORDER BY display_name"
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_federated_service(self, service_id: int) -> Optional[Dict[str, Any]]:
        """Fetch one registered service by id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM federated_services WHERE id = ?", (service_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_federated_service_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch one registered service by slug."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM federated_services WHERE slug = ?", (slug,)
            ).fetchone()
            return dict(row) if row else None

    def update_federated_service(
            self,
            service_id: int,
            display_name: str = None,
            description: str = None,
            base_url: str = None,
            active: bool = None,
    ) -> bool:
        """Update a registered service's editable fields. Only supplied fields change."""
        fields, params = [], []
        if display_name is not None:
            fields.append("display_name = ?"); params.append(display_name)
        if description is not None:
            fields.append("description = ?"); params.append(description)
        if base_url is not None:
            fields.append("base_url = ?"); params.append(base_url)
        if active is not None:
            fields.append("active = ?"); params.append(1 if active else 0)

        if not fields:
            return False

        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(service_id)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE federated_services SET {', '.join(fields)} WHERE id = ?",
                params
            )
            return cursor.rowcount > 0

    def delete_federated_service(self, service_id: int, remove_grants: bool = False) -> bool:
        """
        Permanently remove a service from the registry. Existing grants in
        api_key_services referencing its slug are left as-is by default
        (they just stop appearing as a checkbox option in the portal) —
        pass remove_grants=True to also revoke every key's access to it.
        Prefer update_federated_service(active=False) over this for most
        cases; that keeps the service visible (read-only) on keys that
        already have it, which is usually less surprising than deletion.
        """
        service = self.get_federated_service(service_id)
        if not service:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if remove_grants:
                cursor.execute(
                    "DELETE FROM api_key_services WHERE service = ?", (service['slug'],)
                )
            cursor.execute("DELETE FROM federated_services WHERE id = ?", (service_id,))
            return cursor.rowcount > 0

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
    # Cities (GeoNames cities5000) methods
    # ==========================================================================

    def clear_cities(self) -> None:
        """Delete all rows from cities and cities_import_meta (pre-import wipe)."""
        with self.get_connection() as conn:
            conn.execute('DELETE FROM cities')
            conn.execute('DELETE FROM cities_import_meta')
        logger.info("Cities table cleared")

    def bulk_insert_cities(self, rows: list) -> int:
        """
        Bulk-insert a list of city tuples into the cities table.
        Each tuple: (geoname_id, name, ascii_name, country_code,
                     admin1_code, latitude, longitude, timezone_id, population)
        Returns the number of rows inserted.
        """
        with self.get_connection() as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO cities
                (geoname_id, name, ascii_name, country_code,
                 admin1_code, latitude, longitude, timezone_id, population)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', rows)
        return len(rows)

    def save_cities_import_meta(self, filename: str, row_count: int) -> None:
        """Record metadata about the most recent cities import (single row, id=1)."""
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO cities_import_meta (id, filename, row_count, imported_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    filename    = excluded.filename,
                    row_count   = excluded.row_count,
                    imported_at = CURRENT_TIMESTAMP
            ''', (filename, row_count))

    def get_cities_import_meta(self) -> Optional[Dict[str, Any]]:
        """Return the most recent cities import metadata, or None if not yet imported."""
        with self.get_connection() as conn:
            row = conn.execute(
                'SELECT filename, row_count, imported_at FROM cities_import_meta WHERE id = 1'
            ).fetchone()
        if not row:
            return None
        return {
            'filename':    row['filename'],
            'row_count':   row['row_count'],
            'imported_at': row['imported_at'],
        }

    def search_cities(self, query: str, limit: int = 10) -> list:
        """
        Prefix-match on ascii_name for autocomplete suggestions.
        Orders by population DESC so major cities surface first.
        Returns a list of dicts.
        """
        normalised = query.strip().lower()
        pattern    = normalised + '%'
        with self.get_connection() as conn:
            rows = conn.execute('''
                SELECT geoname_id, name, ascii_name, country_code,
                       admin1_code, latitude, longitude, timezone_id, population
                FROM cities
                WHERE ascii_name LIKE ?
                ORDER BY population DESC
                LIMIT ?
            ''', (pattern, limit)).fetchall()
        return [dict(r) for r in rows]

    def resolve_city(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Best-match city for a query string — used for offline geocoding.
        Tries exact ascii_name match first, then prefix match.
        Returns the highest-population match, or None if no results.
        """
        normalised = query.strip().lower()
        with self.get_connection() as conn:
            # Exact match first
            row = conn.execute('''
                SELECT geoname_id, name, ascii_name, country_code,
                       admin1_code, latitude, longitude, timezone_id, population
                FROM cities
                WHERE ascii_name = ?
                ORDER BY population DESC
                LIMIT 1
            ''', (normalised,)).fetchone()

            if not row:
                # Prefix match fallback
                row = conn.execute('''
                    SELECT geoname_id, name, ascii_name, country_code,
                           admin1_code, latitude, longitude, timezone_id, population
                    FROM cities
                    WHERE ascii_name LIKE ?
                    ORDER BY population DESC
                    LIMIT 1
                ''', (normalised + '%',)).fetchone()

        return dict(row) if row else None

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

            cursor.execute('SELECT COUNT(*) FROM cities')
            cities_count = cursor.fetchone()[0]

            cities_meta = self.get_cities_import_meta()

            return {
                'locations_cached':     location_count,
                'charts_cached':        chart_count,
                'derived_charts':       derived_count,
                'total_chart_accesses': total_accesses,
                'canonical_places':     canonical_count,
                'place_aliases':        alias_count,
                'place_cache_active':   active_cache_count,
                'place_cache_expired':  expired_cache_count,
                'cities_loaded':        cities_count,
                'cities_import':        cities_meta,
            }

    def cleanup_old_cache(self, days: int = 90) -> int:
        """
        Remove stale entries across charts, derived charts, views, and locations.
        All deletions run in a single transaction — if any step fails the whole
        cleanup rolls back, leaving the database in its previous state.

        Order of operations (FK constraints require this sequence):
            1. Derived charts whose parent chart is expiring (cascade)
            2. Derived charts that are stale independently
            3. Natal charts that are stale
            4. Views not accessed within the expiry window
            5. Locations no longer referenced by any remaining chart
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Cascade-delete derived charts belonging to expiring natal charts
            cursor.execute('''
                DELETE FROM derived_charts
                WHERE chart_id IN (
                    SELECT id FROM charts
                    WHERE last_accessed < datetime('now', '-' || ? || ' days')
                )
            ''', (days,))
            deleted_derived_cascade = cursor.rowcount

            # 2. Derived charts that are themselves stale (parent chart still active)
            cursor.execute('''
                DELETE FROM derived_charts
                WHERE last_accessed < datetime('now', '-' || ? || ' days')
            ''', (days,))
            deleted_derived_stale = cursor.rowcount

            # 3. Natal charts
            cursor.execute('''
                DELETE FROM charts
                WHERE last_accessed < datetime('now', '-' || ? || ' days')
            ''', (days,))
            deleted_charts = cursor.rowcount

            # 4. Views not accessed within the expiry window
            cursor.execute('''
                DELETE FROM views
                WHERE last_accessed < datetime('now', '-' || ? || ' days')
            ''', (days,))
            deleted_views = cursor.rowcount

            # 5. Orphaned locations (no remaining chart references them)
            cursor.execute('''
                DELETE FROM locations
                WHERE last_used < datetime('now', '-' || ? || ' days')
                AND id NOT IN (SELECT DISTINCT location_id FROM charts)
            ''', (days,))
            deleted_locations = cursor.rowcount

            total = (
                deleted_derived_cascade + deleted_derived_stale +
                deleted_charts + deleted_views + deleted_locations
            )
            logger.info(
                f"Cache cleanup complete: {deleted_charts} charts, "
                f"{deleted_derived_cascade + deleted_derived_stale} derived charts "
                f"({deleted_derived_cascade} cascade, {deleted_derived_stale} stale), "
                f"{deleted_views} views, {deleted_locations} locations — {total} total"
            )
            return total


# ==============================================================================
# Factory
# ==============================================================================

def create_database_manager(config=None) -> 'DatabaseManager':
    """
    Build a DatabaseManager from configuration.

    Pass the app's Config class (config_class) when available so DB_TYPE
    and the MySQL/SQLite settings it already resolved from the environment
    are reused consistently. Standalone scripts (cleanup.py, key_manager.py,
    email_service.py) that don't import config.py can call this with no
    arguments — DatabaseManager falls back to reading DB_TYPE and friends
    directly from the environment in that case.

    This is the preferred way to construct a DatabaseManager; use it
    instead of calling DatabaseManager(...) directly so every entry point
    picks up MySQL settings the same way.
    """
    if config is not None:
        db_type = getattr(config, 'DB_TYPE', 'sqlite')
        if db_type == 'mysql':
            return DatabaseManager(
                db_type='mysql',
                mysql_config={
                    'host':     config.MYSQL_HOST,
                    'port':     config.MYSQL_PORT,
                    'user':     config.MYSQL_USER,
                    'password': config.MYSQL_PASSWORD,
                    'database': config.MYSQL_DATABASE,
                },
            )
        return DatabaseManager(db_type='sqlite', db_path=config.DATABASE_PATH)

    return DatabaseManager()