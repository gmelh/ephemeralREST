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
# cleanup.py                                                                  #
################################################################################

#!/usr/bin/env python3

"""
CLI script to clean up expired and old cache entries from the Ephemeral.REST database.

Usage:
    # Expire place cache only (default, safe to run frequently)
    python cleanup.py

    # Also clean up old chart/location cache entries older than N days
    python cleanup.py --charts --days 90

    # Full cleanup with summary
    python cleanup.py --all --days 60 --verbose

    # Dry run — show what would be deleted without deleting
    python cleanup.py --all --dry-run

What is deleted:
    --place-cache   expired place_cache rows (leaves canonical_places + aliases intact)
    --charts        chart rows not accessed in --days days
                    location rows not used in --days days and not referenced by any chart
    --all           both of the above

What is NEVER deleted:
    canonical_places   — permanent place identity
    place_aliases      — permanent alias mappings
    place_lookup_log   — audit log (prune manually if needed)
"""

import argparse
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Ensure the script can import from the project directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description='Ephemeral.REST cache cleanup utility'
    )
    parser.add_argument(
        '--place-cache',
        action='store_true',
        help='Delete expired place_cache rows'
    )
    parser.add_argument(
        '--charts',
        action='store_true',
        help='Delete old chart and location cache entries'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Run all cleanup tasks (place-cache + charts)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=90,
        help='Age threshold in days for chart/location cleanup (default: 90)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without deleting anything'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print detailed stats before and after cleanup'
    )

    args = parser.parse_args()

    # Default to --place-cache if nothing specified
    if not any([args.place_cache, args.charts, args.all]):
        args.place_cache = True

    if args.all:
        args.place_cache = True
        args.charts      = True

    # Initialise database
    from database import DatabaseManager
    db_path = os.environ.get('DATABASE_PATH', 'ephemeral.db')
    db      = DatabaseManager(db_path)

    print(f"\n{'=' * 55}")
    print(f"  Ephemeral.REST Cache Cleanup")
    print(f"  Database: {db_path}")
    print(f"  Time:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print(f"  Mode:     DRY RUN (no changes will be made)")
    print(f"{'=' * 55}\n")

    if args.verbose:
        stats = db.get_cache_stats()
        print("Before cleanup:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print()

    total_deleted = 0

    # --- Place cache cleanup ---
    if args.place_cache:
        if args.dry_run:
            # Count what would be deleted
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM place_cache WHERE expires_at <= datetime('now')")
                count = cursor.fetchone()[0]
            print(f"[DRY RUN] Would delete {count} expired place_cache row(s)")
        else:
            deleted = db.cleanup_expired_place_cache()
            print(f"Deleted {deleted} expired place_cache row(s)")
            total_deleted += deleted

    # --- Chart/location cleanup ---
    if args.charts:
        if args.dry_run:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) FROM charts
                    WHERE last_accessed < datetime('now', '-' || ? || ' days')
                ''', (args.days,))
                chart_count = cursor.fetchone()[0]
                cursor.execute('''
                    SELECT COUNT(*) FROM locations
                    WHERE last_used < datetime('now', '-' || ? || ' days')
                    AND id NOT IN (SELECT DISTINCT location_id FROM charts)
                ''', (args.days,))
                loc_count = cursor.fetchone()[0]
            print(f"[DRY RUN] Would delete {chart_count} chart(s) and {loc_count} location(s) "
                  f"not accessed in {args.days} days")
        else:
            deleted = db.cleanup_old_cache(args.days)
            print(f"Deleted {deleted} chart/location cache entries older than {args.days} days")
            total_deleted += deleted

    if args.verbose and not args.dry_run:
        print()
        stats = db.get_cache_stats()
        print("After cleanup:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    print(f"\n{'=' * 55}")
    if args.dry_run:
        print("  Dry run complete — no changes made")
    else:
        print(f"  Total deleted: {total_deleted} row(s)")
    print(f"{'=' * 55}\n")


if __name__ == '__main__':
    main()