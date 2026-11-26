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
"""Importing the GeoNames cities5000 list (at start-up, or by `ephemeral import-cities`)."""


def import_cities_if_pending(db_manager, cities_folder: str, logger) -> None:
    """
    Check CITIES_FOLDER for a .txt file. If one is found:
        1. Log that import is starting (startup will be slower).
        2. Wipe the existing cities table.
        3. Parse and bulk-insert the GeoNames cities5000 TSV format.
        4. Save import metadata (filename, row count, timestamp) to the DB.
        5. Delete the source file.
    If the import fails partway through, the transaction is rolled back so
    the existing cities data is preserved, and a loud error is logged.
    """
    import os, unicodedata

    if not cities_folder:
        return

    if not os.path.isdir(cities_folder):
        logger.warning(f"CITIES_FOLDER '{cities_folder}' does not exist — skipping cities import check")
        return

    txt_files = [f for f in os.listdir(cities_folder) if f.endswith('.txt')]
    if not txt_files:
        return

    filepath = os.path.join(cities_folder, txt_files[0])
    filename = txt_files[0]

    logger.warning(
        f"Cities import pending: found '{filename}' in {cities_folder}. "
        "Startup will be slower while the cities table is rebuilt (~200k rows)."
    )

    # GeoNames cities5000.txt column indices
    COL_GEONAME_ID   = 0
    COL_NAME         = 1
    COL_ASCII_NAME   = 2
    COL_LATITUDE     = 4
    COL_LONGITUDE    = 5
    COL_COUNTRY_CODE = 8
    COL_ADMIN1_CODE  = 10
    COL_POPULATION   = 14
    COL_TIMEZONE     = 17

    BATCH_SIZE = 1000
    rows       = []
    total      = 0

    try:
        db_manager.clear_cities()
        logger.info("Cities table cleared — beginning import")

        with open(filepath, encoding='utf-8') as fh:
            for line in fh:
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 19:
                    continue  # malformed line — skip silently

                try:
                    row = (
                        int(parts[COL_GEONAME_ID]),
                        parts[COL_NAME],
                        parts[COL_ASCII_NAME].lower(),   # stored lowercase for LIKE matching
                        parts[COL_COUNTRY_CODE],
                        parts[COL_ADMIN1_CODE] or None,
                        float(parts[COL_LATITUDE]),
                        float(parts[COL_LONGITUDE]),
                        parts[COL_TIMEZONE],
                        int(parts[COL_POPULATION]) if parts[COL_POPULATION] else 0,
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

    except Exception as e:
        logger.error(
            f"Cities import FAILED for '{filename}': {e}. "
            "Existing cities data has been preserved (transaction rolled back).",
            exc_info=True
        )
