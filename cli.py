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
  ephemeral fetch-ephemeris           download the Swiss Ephemeris data files (all, ~120 MB;
                                      --minimal for 1800–2399 AD only)
  ephemeral import-cities             download and import GeoNames cities5000, with the names
                                      of regions and countries
  ephemeral backup-db FILE            a consistent copy of the SQLite database
  ephemeral check-db                  connect to the database and check it can be used
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


def _epochs(first: int, last: int):
    # Each file covers 600 years, named by its first century: seplm06 (600–1 BC), sepl_18 (1800–2399).
    return [f"m{-c:02d}" if c < 0 else f"_{c:02d}" for c in range(first, last + 1, 6)]


# The 1800–2399 files: enough for most charts, and what fetch-ephemeris --minimal gets.
EPHEMERIS_MINIMAL = ['sepl_18.se1', 'semo_18.se1', 'seas_18.se1']
# Every file Astrodienst publishes for the planets and Moon (13201 BC – 17191 AD) and the
# main asteroids (5401 BC – 5399 AD), with the fixed stars and fictitious bodies: about 120 MB.
EPHEMERIS_FULL = ([f"sepl{e}.se1" for e in _epochs(-132, 168)] +
                  [f"semo{e}.se1" for e in _epochs(-132, 168)] +
                  [f"seas{e}.se1" for e in _epochs(-84, 84)] +
                  ['sefstars.txt', 'seorbel.txt'])

GEONAMES_BASE   = 'https://download.geonames.org/export/dump/'
CITIES_URL      = GEONAMES_BASE + 'cities5000.zip'


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


def _fetch_one(folder: str, name: str, force: bool):
    """Download one ephemeris file (unless it's there). Returns (name, bytes or 0 if kept, error)."""
    dest = os.path.join(folder, name)
    smallest = 10_000 if name.endswith('.se1') else 1_000
    if os.path.isfile(dest) and os.path.getsize(dest) >= smallest and not force:
        return name, 0, None
    try:
        data = _download(EPHEMERIS_BASE + name)
    except Exception as e:
        return name, 0, str(e)
    if len(data) < smallest:  # an error page, not an ephemeris file
        return name, 0, f"the download looks wrong ({len(data)} bytes)"
    partial = dest + '.part'
    with open(partial, 'wb') as fh:
        fh.write(data)
    os.replace(partial, dest)  # never a half-written file where the library would read it
    return name, len(data), None


def cmd_fetch_ephemeris(args) -> int:
    from concurrent.futures import ThreadPoolExecutor
    Config = _config()
    folder = Config.SWISS_EPHEMERIS_PATH
    os.makedirs(folder, exist_ok=True)
    names = EPHEMERIS_MINIMAL if args.minimal else EPHEMERIS_FULL
    missing = [n for n in names if args.force or not os.path.isfile(os.path.join(folder, n))]
    if missing:
        print(f"Downloading {len(missing)} of {len(names)} Swiss Ephemeris files"
              + (' (about 120 MB, a minute or two)' if len(missing) > 20 else '') + ' …', flush=True)
    failed = []
    fetched = total = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, size, error in pool.map(lambda n: _fetch_one(folder, n, args.force), names):
            if error:
                failed.append(name)
                print(f"✗ {name}: {error}", file=sys.stderr)
            elif size:
                fetched += 1
                total += size
    have = len(names) - len(failed)
    print(f"✓ {have} of {len(names)} files in {folder}"
          + (f" ({fetched} downloaded, {total / 1_048_576:,.0f} MB)" if fetched else ' (all already there)'))
    if args.minimal:
        print("They cover 1800–2399 AD. Run `ephemeral fetch-ephemeris` without --minimal for every date "
              "from 13201 BC to 17191 AD.")
    else:
        print("Planets and the Moon: 13201 BC – 17191 AD. Main asteroids: 5401 BC – 5399 AD.")
    if failed:
        print(f"{len(failed)} couldn't be downloaded. Run `ephemeral fetch-ephemeris` again to fetch the rest.",
              file=sys.stderr)
    return 0 if not failed else 1


def cmd_import_cities(args) -> int:
    """Download cities5000 and import it now (the server also imports a file it finds at start-up)."""
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    Config = _config()
    folder = Config.CITIES_FOLDER
    os.makedirs(folder, exist_ok=True)
    from cities_import import REGION_FILES
    # The names of states, regions, counties and countries, so places read in full
    # ("Perth WA, AU", "Perth, Scotland, UK" rather than "Perth, GB"). Kept in the folder for later imports.
    for name in REGION_FILES:
        try:
            data = _download(GEONAMES_BASE + name)
            if len(data) < 1_000:
                raise ValueError(f"the download looks wrong ({len(data)} bytes)")
            with open(os.path.join(folder, name + '.part'), 'wb') as fh:
                fh.write(data)
            os.replace(os.path.join(folder, name + '.part'), os.path.join(folder, name))
            print(f"✓ {name}")
        except Exception as e:
            if os.path.isfile(os.path.join(folder, name)):
                print(f"! {name}: couldn't download it again ({e}); using the copy already there", file=sys.stderr)
            else:
                print(f"! {name}: {e}. Places will show without that part of their names.", file=sys.stderr)
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
    stats = _db().get_cache_stats()
    print(f"Cities loaded: {stats.get('cities_loaded'):,}"
          + (' (with region and country names)' if stats.get('cities_regions') else ''))
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


def cmd_check_db(_args) -> int:
    """Connect with the configured settings and check this account can make tables.
    The installer runs this before starting, so a wrong password, a missing
    trusted-source entry or a bad certificate shows up as one clear message."""
    Config = _config()
    if Config.DB_TYPE != 'mysql':
        print(f"SQLite: {Config.DATABASE_PATH}")
        return 0
    from database import _MySQLConnectionWrapper, MySQLConnectError, mysql_ssl_mode
    cfg = {'host': Config.MYSQL_HOST, 'port': Config.MYSQL_PORT, 'user': Config.MYSQL_USER,
           'password': Config.MYSQL_PASSWORD, 'database': Config.MYSQL_DATABASE,
           'ssl_ca': Config.MYSQL_SSL_CA, 'ssl_mode': Config.MYSQL_SSL_MODE}
    try:
        mode = mysql_ssl_mode(cfg)
        conn = _MySQLConnectionWrapper(cfg)
    except MySQLConnectError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    where = f"{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"
    try:
        version = conn.execute('SELECT VERSION() AS v').fetchone()['v']
        cipher = conn.tls_cipher()
        print(f"✓ Connected to MySQL {version} at {where}")
        if cipher:
            checked = {'VERIFY_CA': 'certificate checked against MYSQL_SSL_CA',
                       'VERIFY_IDENTITY': 'certificate and host name checked'}.get(mode, 'certificate not checked')
            print(f"✓ TLS {cipher} ({checked})")
        else:
            print(f"! Not encrypted (MYSQL_SSL_MODE={mode})")
        name = f"ephemeral_check_{secrets.token_hex(4)}"
        conn.execute(f"CREATE TABLE {name} (id INT PRIMARY KEY, note VARCHAR(20)) "
                     "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        conn.execute(f"INSERT INTO {name} (id, note) VALUES (%s, %s)", (1, 'ok'))
        conn.execute(f"DROP TABLE {name}")
        conn.commit()
        print('✓ This account can create tables')
        tables = conn.execute('SHOW TABLES').fetchall()
        print(f"  {len(tables)} tables in {cfg['database']}" + (' (empty — tables are made at first start)' if not tables else ''))
    except Exception as e:
        print(f"✗ Connected to {where}, but couldn't use the database: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
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
    p = sub.add_parser('fetch-ephemeris', help='Download the Swiss Ephemeris data files (all of them, ~120 MB)')
    p.add_argument('--force', action='store_true', help='Download again even if present')
    p.add_argument('--minimal', action='store_true', help='Only the 1800–2399 AD files (~2.4 MB)')
    p = sub.add_parser('import-cities', help='Download and import the GeoNames cities5000 list')
    p.add_argument('--file', help='Import this cities5000.txt instead of downloading')
    p = sub.add_parser('backup-db', help='Write a consistent copy of the SQLite database')
    p.add_argument('file')
    sub.add_parser('check-db', help='Connect to the database and check it can be used')
    sub.add_parser('keys', help='Manage keys (key_manager.py; `ephemeral keys --help`)')
    sub.add_parser('cleanup', help='Remove old cache entries (cleanup.py; `ephemeral cleanup --help`)')

    args = parser.parse_args(argv)
    return {
        'version':         cmd_version,
        'reset-password':  cmd_reset_password,
        'fetch-ephemeris': cmd_fetch_ephemeris,
        'import-cities':   cmd_import_cities,
        'backup-db':       cmd_backup_db,
        'check-db':        cmd_check_db,
    }[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
