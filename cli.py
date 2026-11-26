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
"""
The `ephemeral` command, run inside the container (the installer's host
`ephemeral` command passes these through):

  ephemeral version
  ephemeral reset-password EMAIL      print a one-time link to set a new password
  ephemeral fetch-ephemeris           download the Swiss Ephemeris data files
  ephemeral import-cities             download and import GeoNames cities5000
  ephemeral backup-db FILE            a consistent copy of the SQLite database
  ephemeral keys ...                  key_manager.py (create, list, rotate, …)
  ephemeral cleanup ...               cleanup.py (old cache entries, …)
"""

import argparse
import io
import os
import secrets
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

EPHEMERIS_BASE  = 'https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/'
EPHEMERIS_FILES = ['sepl_18.se1', 'semo_18.se1', 'seas_18.se1']
CITIES_URL      = 'https://download.geonames.org/export/dump/cities5000.zip'


def _config():
    os.environ.setdefault('EPHEMERAL_NO_ENV_FILE', '1')
    from config import Config
    return Config


def _db():
    from database import create_database_manager
    return create_database_manager(_config())


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': 'ephemeralREST'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


# ---------------------------------------------------------------------------

def cmd_version(_args) -> int:
    from version import __version__
    print(__version__)
    return 0


def cmd_reset_password(args) -> int:
    """For someone locked out: a set-password link that works without email."""
    Config = _config()
    db = _db()
    record = db.get_api_key_by_identifier(args.email.strip().lower())
    if not record:
        print(f"No account for {args.email}.", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    db.create_email_verification(api_key_id=record['id'], email=record['identifier'], token=token)
    if not record.get('active'):
        db.update_api_key(record['id'], active=1)
        print('(The account was disabled or unverified; it is now active.)')
    base = Config.PUBLIC_URL or 'https://YOUR-ADDRESS'
    print(f"Open this link to set a new password for {record['identifier']} (it lasts 24 hours):")
    print(f"  {base}/set-password?t={token}")
    return 0


def cmd_fetch_ephemeris(args) -> int:
    Config = _config()
    folder = Config.SWISS_EPHEMERIS_PATH
    os.makedirs(folder, exist_ok=True)
    ok = True
    for name in EPHEMERIS_FILES:
        dest = os.path.join(folder, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 10_000 and not args.force:
            print(f"✓ {name} (already there)")
            continue
        try:
            data = _download(EPHEMERIS_BASE + name)
        except Exception as e:
            print(f"✗ {name}: {e}", file=sys.stderr)
            ok = False
            continue
        if len(data) < 10_000:  # an error page, not an ephemeris file
            print(f"✗ {name}: the download looks wrong ({len(data)} bytes)", file=sys.stderr)
            ok = False
            continue
        with open(dest, 'wb') as fh:
            fh.write(data)
        print(f"✓ {name} ({len(data) // 1024:,} KB)")
    print(f"Ephemeris files are in {folder}. They cover 1800–2400 AD; add others from {EPHEMERIS_BASE} for more.")
    return 0 if ok else 1


def cmd_import_cities(args) -> int:
    """Download cities5000 and import it now (the server also imports a file it finds at start-up)."""
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    Config = _config()
    folder = Config.CITIES_FOLDER
    os.makedirs(folder, exist_ok=True)
    if args.file:
        source = args.file
    else:
        print(f"Downloading {CITIES_URL} …")
        try:
            data = _download(CITIES_URL)
        except Exception as e:
            print(f"Couldn't download the cities list: {e}", file=sys.stderr)
            print("Download cities5000.zip yourself, unzip it, and run: ephemeral import-cities --file cities5000.txt", file=sys.stderr)
            return 1
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extract('cities5000.txt', folder)
        source = os.path.join(folder, 'cities5000.txt')
    if os.path.dirname(os.path.abspath(source)) != os.path.abspath(folder):
        import shutil
        shutil.copy(source, folder)
    from cities_import import import_cities_if_pending
    import_cities_if_pending(_db(), folder, logging.getLogger('import-cities'))
    print(f"Cities loaded: {_db().get_cache_stats().get('cities_loaded'):,}")
    return 0


def cmd_backup_db(args) -> int:
    Config = _config()
    if Config.DB_TYPE != 'sqlite':
        print('The database is MySQL — back it up with mysqldump.', file=sys.stderr)
        return 1
    import sqlite3
    source = sqlite3.connect(Config.DATABASE_PATH)
    target = sqlite3.connect(args.file)
    with target:
        source.backup(target)
    source.close()
    target.close()
    print(args.file)
    return 0


def _pass_through(script: str, argv) -> int:
    import runpy
    sys.argv = [script] + list(argv)
    _config()
    try:
        runpy.run_path(os.path.join(HERE, script), run_name='__main__')
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else 1
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'keys':
        return _pass_through('key_manager.py', argv[1:])
    if argv and argv[0] == 'cleanup':
        return _pass_through('cleanup.py', argv[1:])

    parser = argparse.ArgumentParser(prog='ephemeral', description='ephemeralREST administration.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('version', help='Show the version')
    p = sub.add_parser('reset-password', help='Print a one-time link to set a new password')
    p.add_argument('email')
    p = sub.add_parser('fetch-ephemeris', help='Download the Swiss Ephemeris data files')
    p.add_argument('--force', action='store_true', help='Download again even if present')
    p = sub.add_parser('import-cities', help='Download and import the GeoNames cities5000 list')
    p.add_argument('--file', help='Import this cities5000.txt instead of downloading')
    p = sub.add_parser('backup-db', help='Write a consistent copy of the SQLite database')
    p.add_argument('file')
    sub.add_parser('keys', help='Manage keys (key_manager.py; `ephemeral keys --help`)')
    sub.add_parser('cleanup', help='Remove old cache entries (cleanup.py; `ephemeral cleanup --help`)')

    args = parser.parse_args(argv)
    return {
        'version':         cmd_version,
        'reset-password':  cmd_reset_password,
        'fetch-ephemeris': cmd_fetch_ephemeris,
        'import-cities':   cmd_import_cities,
        'backup-db':       cmd_backup_db,
    }[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
