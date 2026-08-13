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
# migrate_sqlite_to_mysql.py                                                  #
################################################################################

#!/usr/bin/env python3

"""
One-time data migration: copy an existing SQLite ephemeral.db into a MySQL
database, table by table, preserving primary keys (so foreign keys stay
valid) and converting timestamp columns to native MySQL DATETIME values.

This is a data migration, not a schema migration — the MySQL schema is
created the normal way, via DatabaseManager._init_schema_mysql() (the same
code path the app uses on every startup), and this script only copies rows
into it. Run it once, then flip DB_TYPE=mysql in .env and restart the app.

Usage:
    # Dry run — connect to both databases, print row counts, change nothing
    python migrate_sqlite_to_mysql.py --dry-run

    # Migrate into an empty MySQL database (the common case)
    python migrate_sqlite_to_mysql.py

    # Wipe the MySQL tables first, then migrate (safe to re-run)
    python migrate_sqlite_to_mysql.py --truncate

    # Skip the (potentially large, fully re-importable) cities table
    python migrate_sqlite_to_mysql.py --skip-cities

    # Point at a specific SQLite file / MySQL server instead of .env values
    python migrate_sqlite_to_mysql.py \\
        --sqlite-path /srv/ephemeral/app/ephemeral.db \\
        --mysql-host db.internal --mysql-user ephemeral \\
        --mysql-password secret --mysql-database ephemeral

What is NOT migrated:
    api_usage_count.json, google_api_usage.log, ephemeral.log — these are
    files, not database tables, and carry over unchanged regardless of
    which database backend is in use.
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()


# ==============================================================================
# Table migration order — parents before children, so foreign keys resolve.
# Each entry: (table_name, upsert_key_columns or None).
# upsert_key_columns marks tables with a natural key that the MySQL schema
# pre-populates a default row into (key_class_limits) or that has a fixed
# singleton row (cities_import_meta) — these use INSERT ... ON DUPLICATE KEY
# UPDATE instead of a plain INSERT so a from-scratch MySQL schema doesn't
# collide with the row it already created.
# ==============================================================================
TABLE_ORDER = [
    ('locations',            None),
    ('api_keys',             None),
    ('key_class_limits',     ['key_type']),
    ('canonical_places',     None),
    ('email_templates',      None),
    ('chart_archive',        None),
    ('cities',               None),          # skipped entirely with --skip-cities
    ('cities_import_meta',   ['id']),
    ('charts',               None),
    ('derived_charts',       None),
    ('email_verifications',  None),
    ('login_2fa_codes',      None),
    ('trusted_devices',      None),
    ('smtp_config',          ['key']),
    ('portal_settings',      ['key']),
    ('place_aliases',        None),
    ('place_cache',          None),
    ('place_lookup_log',     None),
    ('chart_recalculations', None),
    ('views',                None),
]

# Column names that are TIMESTAMP/DATETIME in the MySQL schema and need
# their SQLite text representation parsed into a real datetime object
# before insertion. Columns that merely have "date" or "time" in the name
# but are opaque TEXT/VARCHAR in both schemas (datetime_utc, datetime_local,
# reference_date) are deliberately excluded — they're stored and compared
# as plain strings, never as SQL datetime values.
TIMESTAMP_COLUMNS = {
    'created_at', 'updated_at', 'last_accessed', 'last_used',
    'fetched_at', 'expires_at', 'first_calculated_at',
    'recalculated_at', 'imported_at',
}

BATCH_SIZE = 2000


def _parse_dt(value):
    """Parse a SQLite-stored timestamp string into a datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"Could not parse timestamp value: {s!r}")


def _row_count_mysql(mysql_conn, table: str) -> int:
    row = mysql_conn.execute(f"SELECT COUNT(*) AS n FROM `{table}`").fetchone()
    return row['n'] if row else 0


def _migrate_table(sqlite_conn, mysql_conn, table: str, upsert_key, verbose: bool) -> int:
    """Copy every row of `table` from the SQLite connection to the MySQL one."""
    cur = sqlite_conn.execute(f'SELECT * FROM "{table}"')
    columns = [d[0] for d in cur.description]
    quoted_cols = ', '.join(f'`{c}`' for c in columns)
    placeholders = ', '.join('?' * len(columns))

    if upsert_key:
        update_cols = [c for c in columns if c not in upsert_key]
        if update_cols:
            update_clause = ', '.join(f'`{c}` = VALUES(`{c}`)' for c in update_cols)
            insert_sql = (
                f"INSERT INTO `{table}` ({quoted_cols}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {update_clause}"
            )
        else:
            insert_sql = f"INSERT IGNORE INTO `{table}` ({quoted_cols}) VALUES ({placeholders})"
    else:
        insert_sql = f"INSERT INTO `{table}` ({quoted_cols}) VALUES ({placeholders})"

    dt_indices = [i for i, c in enumerate(columns) if c in TIMESTAMP_COLUMNS]

    batch = []
    total = 0
    for row in cur:
        values = list(row)
        for i in dt_indices:
            values[i] = _parse_dt(values[i])
        batch.append(tuple(values))
        if len(batch) >= BATCH_SIZE:
            mysql_conn.executemany(insert_sql, batch)
            total += len(batch)
            if verbose:
                print(f"    ... {total} rows")
            batch = []

    if batch:
        mysql_conn.executemany(insert_sql, batch)
        total += len(batch)

    return total


def main():
    parser = argparse.ArgumentParser(
        description='Migrate an Ephemeral.REST SQLite database into MySQL.'
    )
    parser.add_argument('--sqlite-path', default=os.environ.get('DATABASE_PATH', 'ephemeral.db'),
                         help='Path to the source SQLite database (default: DATABASE_PATH from .env)')
    parser.add_argument('--mysql-host', default=os.environ.get('MYSQL_HOST', 'localhost'))
    parser.add_argument('--mysql-port', type=int, default=int(os.environ.get('MYSQL_PORT', '3306')))
    parser.add_argument('--mysql-user', default=os.environ.get('MYSQL_USER', ''))
    parser.add_argument('--mysql-password', default=os.environ.get('MYSQL_PASSWORD', ''))
    parser.add_argument('--mysql-database', default=os.environ.get('MYSQL_DATABASE', ''))
    parser.add_argument('--truncate', action='store_true',
                         help='Empty each MySQL table before migrating (safe to re-run)')
    parser.add_argument('--skip-cities', action='store_true',
                         help='Skip the cities table (large, and fully re-importable from GeoNames)')
    parser.add_argument('--dry-run', action='store_true',
                         help='Connect to both databases and report row counts; change nothing')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    if not args.mysql_user or not args.mysql_database:
        print("ERROR: --mysql-user and --mysql-database are required "
              "(or set MYSQL_USER / MYSQL_DATABASE in .env).", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.sqlite_path):
        print(f"ERROR: SQLite source database not found: {args.sqlite_path}", file=sys.stderr)
        sys.exit(1)

    from database import DatabaseManager

    print(f"\n{'=' * 60}")
    print("  Ephemeral.REST — SQLite → MySQL migration")
    print(f"  Source: {args.sqlite_path}")
    print(f"  Target: mysql://{args.mysql_host}:{args.mysql_port}/{args.mysql_database}")
    if args.dry_run:
        print("  Mode:   DRY RUN (no changes will be made)")
    print(f"{'=' * 60}\n")

    # Source: a fully-migrated SQLite DatabaseManager, so any older-schema
    # columns (chart_name, chart_type, etc.) have already been added before
    # we start reading.
    source_mgr = DatabaseManager(db_type='sqlite', db_path=args.sqlite_path)

    # Target: creates the MySQL schema (CREATE TABLE IF NOT EXISTS) via the
    # same _init_schema_mysql() the app itself uses on startup.
    target_mgr = DatabaseManager(
        db_type='mysql',
        mysql_config={
            'host': args.mysql_host,
            'port': args.mysql_port,
            'user': args.mysql_user,
            'password': args.mysql_password,
            'database': args.mysql_database,
        },
    )

    tables = [(t, key) for t, key in TABLE_ORDER if not (args.skip_cities and t == 'cities')]

    with target_mgr.get_connection() as mysql_conn:
        # Pre-flight: report existing row counts, and refuse to proceed into
        # a non-empty table unless --truncate was given. Tables migrated via
        # an upsert key (key_class_limits, cities_import_meta, smtp_config,
        # portal_settings) are exempt: _init_schema_mysql() seeds
        # key_class_limits with a default row on every connection, so a
        # brand-new target database always has 1 row there — that's
        # expected and the upsert handles it safely, not a sign of
        # pre-existing data worth blocking on.
        non_empty = []
        for table, upsert_key in tables:
            if upsert_key:
                continue
            n = _row_count_mysql(mysql_conn, table)
            if n > 0:
                non_empty.append((table, n))

        if non_empty and not args.truncate and not args.dry_run:
            print("ERROR: the following MySQL tables already contain data:\n")
            for table, n in non_empty:
                print(f"    {table}: {n} row(s)")
            print(
                "\nRe-run with --truncate to empty these tables first, or point "
                "--mysql-database at a fresh database.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.dry_run:
            print("Source row counts (SQLite):\n")
            with source_mgr.get_connection() as sqlite_conn:
                for table, _ in tables:
                    row = sqlite_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                    print(f"    {table}: {row[0]}")
            if non_empty:
                print("\nMySQL already contains data in:\n")
                for table, n in non_empty:
                    print(f"    {table}: {n} row(s)  (would require --truncate)")
            print("\nDry run complete — no changes made.")
            return

        if args.truncate:
            print("Truncating target tables...")
            mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table, _ in reversed(tables):
                mysql_conn.execute(f"TRUNCATE TABLE `{table}`")
            mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 1")

        print("Migrating...\n")
        results = []
        with source_mgr.get_connection() as sqlite_conn:
            mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table, upsert_key in tables:
                print(f"  {table} ...", end=' ', flush=True)
                n = _migrate_table(sqlite_conn, mysql_conn, table, upsert_key, args.verbose)
                print(f"{n} row(s)")
                results.append((table, n))
            mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 1")

    total = sum(n for _, n in results)
    print(f"\n{'=' * 60}")
    print(f"  Migration complete — {total} row(s) across {len(results)} table(s)")
    print(f"{'=' * 60}")
    print(
        "\nNext step: set DB_TYPE=mysql (and the MYSQL_* settings used above) "
        "in .env, then restart the app."
    )


if __name__ == '__main__':
    main()