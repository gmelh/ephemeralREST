################################################################################
#                                                                              #
#  ephemeralREST — Swiss Ephemeris REST API                                    #
#  Copyright (C) 2026  ephemeralREST contributors                              #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
################################################################################
"""Importing the GeoNames cities5000 list (at start-up, or by `ephemeral import-cities`).

Besides the cities file, three small GeoNames files give each place its full names, so a place
can be shown as "Perth WA, AU" or "Perth, Scotland, UK" rather than "Perth, AU" and "Perth, GB":

    admin1CodesASCII.txt   states, provinces and regions   (US.IL → Illinois)
    admin2Codes.txt        counties and districts          (GB.ENG.J9 → Isle of Wight)
    countryInfo.txt        country names                   (AU → Australia)

They're kept in CITIES_FOLDER after the import (only the cities file is removed), so a later
import uses them too. Without them the import still works, with country codes only.
"""

import os

REGION_FILES = ('admin1CodesASCII.txt', 'admin2Codes.txt', 'countryInfo.txt')

# GeoNames cities5000.txt columns
COL_GEONAME_ID   = 0
COL_NAME         = 1
COL_ASCII_NAME   = 2
COL_LATITUDE     = 4
COL_LONGITUDE    = 5
COL_COUNTRY_CODE = 8
COL_ADMIN1_CODE  = 10
COL_ADMIN2_CODE  = 11
COL_POPULATION   = 14
COL_TIMEZONE     = 17


def _read_codes(path: str, key_col: int, name_col: int) -> dict:
    """code → name from a GeoNames tab-separated file; comment lines start with #."""
    names = {}
    if not os.path.isfile(path):
        return names
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) > max(key_col, name_col) and parts[key_col] and parts[name_col]:
                names[parts[key_col]] = parts[name_col]
    return names


def read_region_names(folder: str):
    """(admin1, admin2, countries) lookups from the GeoNames files in folder (empty if absent)."""
    admin1 = _read_codes(os.path.join(folder, 'admin1CodesASCII.txt'), 0, 1)
    admin2 = _read_codes(os.path.join(folder, 'admin2Codes.txt'), 0, 1)
    countries = _read_codes(os.path.join(folder, 'countryInfo.txt'), 0, 4)
    return admin1, admin2, countries


def pending_cities_file(folder: str):
    """The cities file waiting to be imported (any .txt but the region files), or None."""
    if not folder or not os.path.isdir(folder):
        return None
    names = sorted(f for f in os.listdir(folder) if f.endswith('.txt') and f not in REGION_FILES)
    return names[0] if names else None


def import_cities_if_pending(db_manager, cities_folder: str, logger) -> None:
    """
    If CITIES_FOLDER holds a cities file (GeoNames cities5000 format):
        1. rebuild the cities table from it, with region and country names from the region
           files beside it, if they're there,
        2. record the import (filename, row count, time),
        3. delete the cities file,
        4. retire the cached place look-ups, so places are found again with the new names
           (charts keep the places they were made with).
    If the import fails part-way, the error is logged loudly and the file is kept.
    """
    if not cities_folder:
        return
    if not os.path.isdir(cities_folder):
        logger.warning(f"CITIES_FOLDER '{cities_folder}' does not exist — skipping cities import check")
        return
    filename = pending_cities_file(cities_folder)
    if not filename:
        return
    filepath = os.path.join(cities_folder, filename)

    logger.warning(
        f"Cities import pending: found '{filename}' in {cities_folder}. "
        "Startup will be slower while the cities table is rebuilt (~200k rows)."
    )
    admin1, admin2, countries = read_region_names(cities_folder)
    if admin1 and countries:
        logger.info(f"Region names: {len(admin1)} regions, {len(admin2)} districts, {len(countries)} countries")
    else:
        logger.warning(
            "No region names (admin1CodesASCII.txt, countryInfo.txt) beside the cities file: "
            "places will show country codes only. `ephemeral import-cities` downloads them.")

    BATCH_SIZE = 1000
    rows = []
    total = 0
    try:
        db_manager.clear_cities()
        logger.info("Cities table cleared — beginning import")

        with open(filepath, encoding='utf-8') as fh:
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 19:
                    continue  # malformed line — skip silently
                try:
                    cc = parts[COL_COUNTRY_CODE]
                    a1 = parts[COL_ADMIN1_CODE] or None
                    a2 = parts[COL_ADMIN2_CODE] or None
                    row = (
                        int(parts[COL_GEONAME_ID]),
                        parts[COL_NAME],
                        parts[COL_ASCII_NAME].lower(),   # stored lowercase for LIKE matching
                        cc,
                        a1,
                        float(parts[COL_LATITUDE]),
                        float(parts[COL_LONGITUDE]),
                        parts[COL_TIMEZONE],
                        int(parts[COL_POPULATION]) if parts[COL_POPULATION] else 0,
                        a2,
                        admin1.get(f"{cc}.{a1}") if a1 else None,
                        admin2.get(f"{cc}.{a1}.{a2}") if a1 and a2 else None,
                        countries.get(cc),
                    )
                except (ValueError, IndexError):
                    continue  # skip lines with unparseable numeric fields

                rows.append(row)
                if len(rows) >= BATCH_SIZE:
                    db_manager.bulk_insert_cities(rows)
                    total += len(rows)
                    rows = []

            if rows:
                db_manager.bulk_insert_cities(rows)
                total += len(rows)

        db_manager.save_cities_import_meta(filename, total)
        logger.info(f"Cities import complete: {total} rows from '{filename}'")

        os.remove(filepath)
        logger.info(f"Cities source file '{filepath}' deleted after successful import")

        retired = db_manager.retire_location_cache()
        if retired:
            logger.info(f"Retired {retired} cached place look-ups; they're found again with region names")

    except Exception as e:
        logger.error(
            f"Cities import FAILED for '{filename}': {e}. The file is kept; "
            "fix the problem and restart, or run `ephemeral import-cities` again.",
            exc_info=True
        )
