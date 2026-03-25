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
# key_manager.py                                                              #
################################################################################

#!/usr/bin/env python3

"""
CLI tool for managing encrypted API keys in the Ephemeral.REST database.

All keys are encrypted with Fernet (AES-128) using a key derived from
the SECRET_KEY in your .env file. The encrypted ciphertext is stored in
the database — the plaintext key is only shown once at creation.

Commands:
    create      Create a new API key (domain or user)
    list        List all keys
    show        Show details for a specific key by identifier
    rotate      Generate and store a new key for an existing record
    disable     Deactivate a key (keeps the record)
    enable      Reactivate a disabled key
    delete      Permanently remove a key record
    set-limits  Set rate limits for a specific key or a key class
    set-output  Set output configuration for a key (JSON file or inline JSON)
    class-limits Show or update class-level default rate limits
    migrate     Import keys from existing ./users/*.cfg files
    verify      Test whether a plaintext key is valid

Usage examples:
    python key_manager.py create
    python key_manager.py list
    python key_manager.py list --all
    python key_manager.py rotate --identifier cosmobiology.online
    python key_manager.py disable --identifier gavin@example.com
    python key_manager.py set-limits --identifier cosmobiology.online --per-minute 30 --per-hour 300 --per-day 2000
    python key_manager.py class-limits --type domain --per-minute 20 --per-hour 200 --per-day 1000
    python key_manager.py set-output --identifier cosmobiology.online --file config.json
    python key_manager.py migrate
    python key_manager.py verify
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()


def get_db():
    from database import DatabaseManager
    db_path = os.environ.get('DATABASE_PATH', 'ephemeral.db')
    return DatabaseManager(db_path)


def get_crypto():
    from key_crypto import KeyCrypto
    secret = os.environ.get('SECRET_KEY', '')
    if not secret:
        print("ERROR: SECRET_KEY is not set in .env")
        sys.exit(1)
    return KeyCrypto(secret)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args):
    """Create a new API key."""
    db     = get_db()
    crypto = get_crypto()

    print("\n--- Create API Key ---")

    # Key type
    key_type = args.type if hasattr(args, 'type') and args.type else None
    while key_type not in ('domain', 'user'):
        key_type = input("Key type [domain/user]: ").strip().lower()

    # Identifier
    identifier = args.identifier if hasattr(args, 'identifier') and args.identifier else None
    if not identifier:
        if key_type == 'domain':
            identifier = input("Domain (e.g. cosmobiology.online): ").strip()
        else:
            identifier = input("Email address: ").strip()

    if not identifier:
        print("ERROR: identifier is required")
        sys.exit(1)

    # Check for duplicates
    existing = db.get_all_api_keys(include_inactive=True)
    if any(k['identifier'] == identifier for k in existing):
        print(f"ERROR: A key already exists for '{identifier}'")
        print("Use 'rotate' to generate a new key for an existing record.")
        sys.exit(1)

    # Display name
    name = args.name if hasattr(args, 'name') and args.name else None
    if not name:
        name = input(f"Display name [{identifier}]: ").strip() or identifier

    # Admin flag
    admin = False
    if hasattr(args, 'admin') and args.admin:
        admin = True
    else:
        resp = input("Admin key? [y/N]: ").strip().lower()
        admin = resp == 'y'

    # Rate limits (blank = use class default)
    class_limits = db.get_key_class_limits(key_type)
    print(f"\nClass defaults for '{key_type}': "
          f"{class_limits['rate_per_minute']}/min, "
          f"{class_limits['rate_per_hour']}/hr, "
          f"{class_limits['rate_per_day']}/day")
    print("Leave blank to use class defaults.")

    rpm  = _read_int("Rate limit per minute", None)
    rph  = _read_int("Rate limit per hour",   None)
    rpd  = _read_int("Rate limit per day",    None)

    # Generate and encrypt key
    from key_crypto import KeyCrypto
    plaintext = KeyCrypto.generate_key()
    key_enc   = crypto.encrypt(plaintext)
    prefix    = crypto.prefix(plaintext)

    key_id = db.create_api_key(
        key_type=key_type,
        name=name,
        identifier=identifier,
        key_enc=key_enc,
        key_prefix=prefix,
        admin=admin,
        rate_per_minute=rpm,
        rate_per_hour=rph,
        rate_per_day=rpd,
    )

    print(f"\n{'='*60}")
    print(f"  Key created successfully (id={key_id})")
    print(f"  Type:       {key_type}")
    print(f"  Name:       {name}")
    print(f"  Identifier: {identifier}")
    print(f"  Admin:      {admin}")
    print(f"  Prefix:     {prefix}")
    print(f"\n  API KEY (copy this — it will not be shown again):")
    print(f"\n    {plaintext}\n")
    print(f"{'='*60}")
    print(f"\n  Add to X-API-Key header: X-API-Key: {plaintext}")
    print()


def cmd_list(args):
    """List all API keys."""
    db           = get_db()
    include_all  = hasattr(args, 'all') and args.all
    keys         = db.get_all_api_keys(include_inactive=include_all)

    if not keys:
        print("No API keys found. Use 'create' to add one.")
        return

    # Group by type
    domains = [k for k in keys if k['key_type'] == 'domain']
    users   = [k for k in keys if k['key_type'] == 'user']

    def print_group(title, group):
        if not group:
            return
        print(f"\n  {title}")
        print(f"  {'─'*70}")
        for k in group:
            status = "active" if k['active'] else "DISABLED"
            admin  = " [ADMIN]" if k.get('admin') else ""
            rpm    = k.get('rate_per_minute') or 'class default'
            print(f"  {k['identifier']:<35} {status:<10}{admin}")
            print(f"    Name: {k['name']}  |  Prefix: {k['key_prefix']}  |  "
                  f"rpm: {rpm}  |  id: {k['id']}")

    print(f"\n{'='*72}")
    print(f"  API Keys {'(including disabled)' if include_all else '(active only)'}")
    print(f"{'='*72}")
    print_group("DOMAIN KEYS", domains)
    print_group("USER KEYS", users)
    print()

    # Class limits
    for kt in ('domain', 'user'):
        lim = db.get_key_class_limits(kt)
        print(f"  {kt.capitalize()} class defaults: "
              f"{lim['rate_per_minute']}/min, "
              f"{lim['rate_per_hour']}/hr, "
              f"{lim['rate_per_day']}/day")
    print()


def cmd_show(args):
    """Show full details for a key by identifier."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    class_lim = db.get_key_class_limits(record['key_type'])

    print(f"\n{'='*60}")
    print(f"  Key: {record['identifier']}")
    print(f"{'='*60}")
    print(f"  ID:           {record['id']}")
    print(f"  Type:         {record['key_type']}")
    print(f"  Name:         {record['name']}")
    print(f"  Prefix:       {record['key_prefix']}")
    print(f"  Admin:        {bool(record.get('admin'))}")
    print(f"  Active:       {bool(record.get('active'))}")
    print(f"  Created:      {record.get('created_at', 'unknown')}")
    print(f"  Updated:      {record.get('updated_at', 'unknown')}")
    rpm_val = record.get('rate_per_minute') or ('class default (' + str(class_lim['rate_per_minute']) + ')')
    rph_val = record.get('rate_per_hour')   or ('class default (' + str(class_lim['rate_per_hour'])   + ')')
    rpd_val = record.get('rate_per_day')    or ('class default (' + str(class_lim['rate_per_day'])    + ')')
    print("  Rate limits (key-level):")
    print(f"    per_minute: {rpm_val}")
    print(f"    per_hour:   {rph_val}")
    print(f"    per_day:    {rpd_val}")

    cfg = record.get('output_config')
    if cfg:
        print(f"\n  Output config:")
        print(f"    {json.dumps(cfg, indent=4)}")
    else:
        print(f"\n  Output config: (inherits server defaults)")
    print()


def cmd_rotate(args):
    """Generate a new key for an existing record."""
    db     = get_db()
    crypto = get_crypto()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    from key_crypto import KeyCrypto
    new_plaintext = KeyCrypto.generate_key()
    new_enc       = crypto.encrypt(new_plaintext)
    new_prefix    = crypto.prefix(new_plaintext)

    db.update_api_key(record['id'], key_enc=new_enc, key_prefix=new_prefix)

    print(f"\n{'='*60}")
    print(f"  Key rotated for: {args.identifier}")
    print(f"  New prefix:      {new_prefix}")
    print(f"\n  NEW API KEY (copy this — it will not be shown again):")
    print(f"\n    {new_plaintext}\n")
    print(f"{'='*60}\n")


def cmd_disable(args):
    """Deactivate a key."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    db.update_api_key(record['id'], active=0)
    print(f"Key for '{args.identifier}' disabled.")


def cmd_enable(args):
    """Reactivate a disabled key."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    db.update_api_key(record['id'], active=1)
    print(f"Key for '{args.identifier}' enabled.")


def cmd_delete(args):
    """Permanently delete a key record."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    confirm = input(f"Permanently delete key for '{args.identifier}'? [yes/N]: ").strip()
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return

    db.delete_api_key(record['id'])
    print(f"Key for '{args.identifier}' permanently deleted.")


def cmd_set_limits(args):
    """Set rate limits for a specific key."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    updates = {}
    if args.per_minute is not None:
        updates['rate_per_minute'] = args.per_minute
    if args.per_hour is not None:
        updates['rate_per_hour'] = args.per_hour
    if args.per_day is not None:
        updates['rate_per_day'] = args.per_day

    if not updates:
        print("No limits specified. Use --per-minute, --per-hour, --per-day.")
        return

    db.update_api_key(record['id'], **updates)
    print(f"Rate limits updated for '{args.identifier}': {updates}")


def cmd_class_limits(args):
    """Show or update class-level default rate limits."""
    db = get_db()

    if not any([args.per_minute, args.per_hour, args.per_day]):
        # Show current limits
        for kt in ('domain', 'user'):
            lim = db.get_key_class_limits(kt)
            print(f"\n  {kt.capitalize()} class defaults:")
            print(f"    per_minute: {lim['rate_per_minute']}")
            print(f"    per_hour:   {lim['rate_per_hour']}")
            print(f"    per_day:    {lim['rate_per_day']}")
        print()
        return

    if not args.type:
        print("ERROR: --type [domain|user] is required when setting limits")
        sys.exit(1)

    current = db.get_key_class_limits(args.type)
    rpm = args.per_minute if args.per_minute is not None else current['rate_per_minute']
    rph = args.per_hour   if args.per_hour   is not None else current['rate_per_hour']
    rpd = args.per_day    if args.per_day    is not None else current['rate_per_day']

    db.set_key_class_limits(args.type, rpm, rph, rpd)
    print(f"Class limits updated for '{args.type}': {rpm}/min, {rph}/hr, {rpd}/day")


def cmd_set_output(args):
    """Set output configuration for a key."""
    db     = get_db()
    keys   = db.get_all_api_keys(include_inactive=True)
    record = next((k for k in keys if k['identifier'] == args.identifier), None)

    if not record:
        print(f"ERROR: No key found for '{args.identifier}'")
        sys.exit(1)

    cfg = None
    if args.file:
        with open(args.file) as f:
            cfg = json.load(f)
    elif args.json:
        cfg = json.loads(args.json)
    else:
        print("Provide --file <path.json> or --json <json-string>")
        sys.exit(1)

    db.update_api_key(record['id'], output_config=cfg)
    print(f"Output config updated for '{args.identifier}'")


def cmd_migrate(args):
    """
    Import keys from existing ./users/*.cfg files into the database.
    Each cfg file maps to a new domain key (identifier = cfg filename stem).
    """
    from pathlib import Path
    import configparser

    db     = get_db()
    crypto = get_crypto()

    users_dir = Path('./users')
    if not users_dir.exists():
        print("ERROR: ./users/ directory not found")
        sys.exit(1)

    cfg_files = sorted(users_dir.glob('*.cfg'))
    if not cfg_files:
        print("No .cfg files found in ./users/")
        return

    existing = {k['identifier'] for k in db.get_all_api_keys(include_inactive=True)}
    created  = 0
    skipped  = 0

    for cfg_path in cfg_files:
        parser = configparser.ConfigParser(allow_no_value=True)
        parser.read(cfg_path)

        if not parser.has_section('user'):
            print(f"  SKIP {cfg_path.name}: missing [user] section")
            skipped += 1
            continue

        name        = parser.get('user', 'name', fallback=cfg_path.stem).strip()
        api_key_env = parser.get('user', 'api_key_env', fallback='').strip()
        admin       = parser.get('user', 'admin', fallback='false').strip().lower() == 'true'

        # Use the stem as the identifier (e.g. 'cosmobiology_online' or 'mindforge')
        identifier = cfg_path.stem

        if identifier in existing:
            print(f"  SKIP {cfg_path.name}: '{identifier}' already in database")
            skipped += 1
            continue

        # Try to read the existing key from the environment
        plaintext = os.environ.get(api_key_env, '').strip() if api_key_env else ''
        if not plaintext:
            from key_crypto import KeyCrypto
            plaintext = KeyCrypto.generate_key()
            print(f"  NOTE {cfg_path.name}: {api_key_env} not in .env — generated new key")

        key_enc = crypto.encrypt(plaintext)
        prefix  = crypto.prefix(plaintext)

        # Parse rate limits
        rpm = rpd = rph = None
        if parser.has_section('rate_limits'):
            def pint(val):
                try: return int(val.strip()) if val and val.strip() else None
                except: return None
            rpm = pint(parser.get('rate_limits', 'per_minute', fallback=''))
            rph = pint(parser.get('rate_limits', 'per_hour',   fallback=''))
            rpd = pint(parser.get('rate_limits', 'per_day',    fallback=''))

        # Parse output config sections
        output = {}
        for section, keys_list in [
            ('output',         ['geocentric','heliocentric','right_ascension','declination',
                               'longitude_speed','latitude_speed','declination_speed',
                               'retrograde','default_house_system']),
            ('output.angles',  ['asc','mc','vertex','east_point','armc']),
            ('output.bodies',  ['sun','moon','mercury','venus','mars','jupiter','saturn',
                               'uranus','neptune','pluto','earth','asteroids','ceres',
                               'pallas','juno','vesta','chiron','mean_node','true_node',
                               'south_node','part_of_fortune','mean_lilith','true_lilith']),
            ('output.meta',    ['api_usage','from_cache']),
        ]:
            if parser.has_section(section):
                sub = {}
                for k in keys_list:
                    v = parser.get(section, k, fallback='').strip()
                    if v:
                        if k == 'default_house_system':
                            sub[k] = v
                        else:
                            sub[k] = v.lower() in ('true','1','yes','on')
                if sub:
                    label = section.replace('output.', '') if '.' in section else None
                    if label:
                        output.setdefault(label, {}).update(sub)
                    else:
                        output.update(sub)

        key_id = db.create_api_key(
            key_type='domain',
            name=name,
            identifier=identifier,
            key_enc=key_enc,
            key_prefix=prefix,
            admin=admin,
            rate_per_minute=rpm,
            rate_per_hour=rph,
            rate_per_day=rpd,
            output_config=output if output else None,
        )

        print(f"  CREATED {cfg_path.name}: '{identifier}' (id={key_id}, prefix={prefix})")
        if plaintext:
            print(f"           Key: {plaintext}")
        created += 1

    print(f"\nMigration complete: {created} created, {skipped} skipped")
    print("The ./users/*.cfg files can now be removed.")


def cmd_verify(args):
    """Test whether a plaintext key resolves correctly."""
    from users import init_users, get_user_by_key

    db = get_db()
    init_users(db)

    key = args.key if hasattr(args, 'key') and args.key else input("API Key to test: ").strip()

    user = get_user_by_key(key)
    if user:
        print(f"\n  VALID key")
        print(f"  Resolves to: {user['name']} ({user['identifier']})")
        print(f"  Type:        {user['key_type']}")
        print(f"  Admin:       {user['admin']}")
        print(f"  Rate limits: {user['rate_limits']}")
    else:
        print(f"\n  INVALID or unrecognised key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_int(prompt: str, default) -> int | None:
    val = input(f"{prompt} [{default if default is not None else 'class default'}]: ").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"  Invalid integer — using default")
        return default


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Ephemeral.REST — Key Management CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # create
    p = sub.add_parser('create', help='Create a new API key')
    p.add_argument('--type',       choices=['domain', 'user'])
    p.add_argument('--identifier', help='Domain or email address')
    p.add_argument('--name',       help='Display name')
    p.add_argument('--admin',      action='store_true')

    # list
    p = sub.add_parser('list', help='List all API keys')
    p.add_argument('--all', action='store_true', help='Include disabled keys')

    # show
    p = sub.add_parser('show', help='Show details for a key')
    p.add_argument('--identifier', required=True)

    # rotate
    p = sub.add_parser('rotate', help='Rotate key for an existing record')
    p.add_argument('--identifier', required=True)

    # disable
    p = sub.add_parser('disable', help='Disable a key')
    p.add_argument('--identifier', required=True)

    # enable
    p = sub.add_parser('enable', help='Enable a disabled key')
    p.add_argument('--identifier', required=True)

    # delete
    p = sub.add_parser('delete', help='Permanently delete a key')
    p.add_argument('--identifier', required=True)

    # set-limits
    p = sub.add_parser('set-limits', help='Set rate limits for a key')
    p.add_argument('--identifier',  required=True)
    p.add_argument('--per-minute',  type=int, default=None)
    p.add_argument('--per-hour',    type=int, default=None)
    p.add_argument('--per-day',     type=int, default=None)

    # class-limits
    p = sub.add_parser('class-limits', help='Show or update class-level limits')
    p.add_argument('--type',       choices=['domain', 'user'])
    p.add_argument('--per-minute', type=int, default=None)
    p.add_argument('--per-hour',   type=int, default=None)
    p.add_argument('--per-day',    type=int, default=None)

    # set-output
    p = sub.add_parser('set-output', help='Set output config for a key')
    p.add_argument('--identifier', required=True)
    p.add_argument('--file',       help='Path to JSON config file')
    p.add_argument('--json',       help='Inline JSON string')

    # migrate
    sub.add_parser('migrate', help='Import from ./users/*.cfg files')

    # verify
    p = sub.add_parser('verify', help='Test a plaintext key')
    p.add_argument('--key', help='The plaintext API key to test')

    args = parser.parse_args()

    commands = {
        'create':       cmd_create,
        'list':         cmd_list,
        'show':         cmd_show,
        'rotate':       cmd_rotate,
        'disable':      cmd_disable,
        'enable':       cmd_enable,
        'delete':       cmd_delete,
        'set-limits':   cmd_set_limits,
        'class-limits': cmd_class_limits,
        'set-output':   cmd_set_output,
        'migrate':      cmd_migrate,
        'verify':       cmd_verify,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()