# ephemeralREST — Architecture & Developer Guide

This document describes how the codebase is structured, how the components relate to each other, and how to navigate and modify the code. It is written for developers who are new to the project, or who want to understand how something works before changing it.

---

## Table of contents

1. [System overview](#1-system-overview)
2. [Directory structure](#2-directory-structure)
3. [The API backend](#3-the-api-backend)
   - Application factory
   - Configuration
   - Request lifecycle
   - Rate limiting
   - Middleware
4. [Authentication and key management](#4-authentication-and-key-management)
   - How keys are stored
   - How keys are verified
   - The user object
   - Key class limits
5. [Astronomy calculations](#5-astronomy-calculations)
   - AstronomyService
   - Swiss Ephemeris integration
   - Chart calculation flow
   - Derived charts
   - Apsides and lunations
6. [Database layer](#6-database-layer)
   - Schema overview
   - DatabaseManager
   - Table reference
7. [Output configuration system](#7-output-configuration-system)
   - How the three-level merge works
   - OutputConfig class
   - Storing and retrieving per-key config
8. [Location resolution](#8-location-resolution)
   - Resolution pipeline
   - Caching strategy
9. [Email service](#9-email-service)
   - Configuration priority
   - Adding a new email type
10. [Routes reference](#10-routes-reference)
11. [The admin portal (PHP)](#11-the-admin-portal-php)
    - File structure
    - Authentication flow
    - AJAX pattern
    - Adding a new page
12. [Registration and key provisioning](#12-registration-and-key-provisioning)
13. [Key manager CLI](#13-key-manager-cli)
14. [Adding a new endpoint](#14-adding-a-new-endpoint)
15. [Common patterns and conventions](#15-common-patterns-and-conventions)
16. [Configuration reference](#16-configuration-reference)

---

## Licence

ephemeralREST is licensed under the **GNU Affero General Public License v3 (AGPL v3)**. This licence was selected for compatibility with the Swiss Ephemeris library, which is itself AGPL v3. Because the Swiss Ephemeris is linked at runtime, the combined work must be distributed under the AGPL v3.

The critical difference between GPL v3 and AGPL v3 is the **network service clause**. Under AGPL v3, users who interact with the software over a network (i.e., anyone who calls the API) are legally entitled to receive the source code of the running application. A public GitHub repository satisfies this obligation.

Every Python source file includes the standard AGPL v3 notice in its header, including a separate notice about the Swiss Ephemeris dependency and the Astrodienst AGPL obligation.

The full licence text lives in `LICENSE` at the repository root.

---

## 1. System overview

ephemeralREST consists of two separate applications that communicate over HTTP:

```
┌─────────────────────────────────────────────────────────────────┐
│                         Internet / Client                        │
└───────────────┬─────────────────────────────────┬──────────────┘
                │                                 │
        API requests                       Browser requests
        (JSON, X-API-Key)                  (PHP portal)
                │                                 │
         ┌──────▼──────┐                  ┌───────▼──────┐
         │  nginx      │                  │  nginx       │
         │  :443       │                  │  :443        │
         │  api.domain │                  │  admin.domain│
         └──────┬──────┘                  └───────┬──────┘
                │  proxy_pass                      │  PHP-FPM
         ┌──────▼──────────────┐         ┌────────▼────────┐
         │  Gunicorn           │         │  PHP Admin      │
         │  Flask API          │         │  Portal         │
         │  Python 3.10+       │◄────────│  (reads/writes  │
         │  port 5000          │  HTTP   │   via API)      │
         └──────┬──────────────┘         └─────────────────┘
                │
         ┌──────▼──────────────┐
         │  SQLite database    │
         │  ephemeral.db           │
         └─────────────────────┘
```

**The Flask API** does all computation. It validates requests, calls the Swiss Ephemeris, stores results, and returns JSON.

**The PHP admin portal** is a thin client. It never touches the database directly — it makes HTTP requests to the Flask API using an admin API key, and renders the responses as HTML. This means the portal can run on a completely different server if needed.

**SQLite** stores everything: charts, keys, locations, SMTP config, registration requests. There is no separate database server — the database is a single file (`ephemeral.db`).

---

## 2. Directory structure

### API backend (Python)

```
/srv/ephemeral/app/
│
├── app.py                  Application factory, rate limiter setup
├── config.py               All configuration, loaded from .env
├── routes.py               All 44 API endpoints
├── astronomy.py            Swiss Ephemeris wrapper (AstronomyService)
├── database.py             SQLite wrapper (DatabaseManager)
├── auth.py                 API key authentication (AuthManager)
├── users.py                Builds the g.user object from a key record
├── key_crypto.py           Fernet encryption for stored keys (KeyCrypto)
├── key_manager.py          CLI tool for key administration
├── output_config.py        Output config defaults and merge logic (OutputConfig)
├── validators.py           Marshmallow request schemas
├── middleware.py           Request logging, error handling
├── email_service.py        SMTP transactional email (EmailService)
├── geocoding.py            Google Maps geocoding (GeocodingService)
├── location_normaliser.py  Normalises location strings for cache keys
├── place_repository.py     Location lookup and cache coordination
├── api_usage.py            Google API budget tracker (APIUsageTracker)
├── cleanup.py              Cache maintenance utilities
├── gunicorn_config.py      Gunicorn worker configuration
│
├── sweph/                  Swiss Ephemeris data files (*.se1, *.eph)
├── .env                    Environment configuration (not committed)
├── ephemeral.db                SQLite database (not committed)
└── requirements.txt
```

### Admin portal (PHP)

```
/srv/ephemeral/admin/
│
├── landing.php             Public marketing page + inline key request form
├── login.php               API key sign-in → session
├── logout.php              Clear session
│
├── portal-admin.php        Admin dashboard (server status, pending count)
├── portal-domain.php       Domain key holder self-service
├── portal-user.php         User key holder self-service
│
├── registrations.php       List and approve/reject registrations
├── keys.php                List all keys, edit limits and status
├── key-detail.php          Single key detail view
├── key-output.php          Per-key output configuration editor
├── class-limits.php        Edit key class rate limit defaults
├── smtp.php                SMTP server configuration
├── register-key.php        Public key request form (standalone page)
│
├── config.php              API_BASE, ADMIN_API_KEY, SITE_NAME constants
│
├── includes/
│   ├── api.php             cURL wrappers: api_get(), api_post()
│   ├── auth.php            Session helpers, my_api_get(), my_api_post()
│   ├── header.php          HTML <head>, sidebar nav, flash messages
│   └── footer.php          Closing HTML, shared JS (confirm, copy, flash)
│
└── assets/
    └── style.css           Full dark-mode stylesheet (CSS variables)
```

---

## 3. The API backend

### Application factory

The API uses Flask's application factory pattern. Nothing is initialised at import time — everything is created inside `create_app()`:

```python
# app.py
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    # ... register blueprint, limiter, middleware ...
    return app
```

Gunicorn calls it as:

```
gunicorn "app:create_app()"
```

This pattern makes it easy to pass a different config class for testing.

The application is a single Flask **Blueprint** (`api`) registered on the app. All 44 routes live in `routes.py` and are attached to this blueprint.

### Configuration

All configuration is loaded from the `.env` file via `config.py`. The `Config` class reads environment variables and exposes them as class attributes. A `validate()` method checks required fields at startup and raises a clear error if anything is missing.

```python
# config.py
class Config:
    SECRET_KEY          = os.environ.get('SECRET_KEY')
    DATABASE_PATH       = os.environ.get('DATABASE_PATH', 'ephemeral.db')
    SWISS_EPHEMERIS_PATH = os.environ.get('SWISS_EPHEMERIS_PATH', '')
    RATE_LIMIT_ENABLED  = os.environ.get('RATE_LIMIT_ENABLED', 'true').lower() == 'true'
    # ...

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY:
            raise ValueError("SECRET_KEY must be set in .env")
```

**To add a new configuration value:** add a class attribute to `Config`, then read it where needed. Never read `os.environ` directly in application code — always go through `Config`.

### Request lifecycle

Every authenticated API request goes through this sequence:

```
1. nginx receives HTTPS request
2. nginx forwards to Gunicorn (HTTP, port 5000)
3. Flask-Limiter checks rate limits
4. Middleware (middleware.py) logs the request
5. AuthManager.require_api_key() decorator runs (if protected endpoint):
   a. Reads X-API-Key header
   b. Looks up key by 8-char prefix in database
   c. Decrypts and compares candidate keys
   d. Loads key record and builds g.user dict via users.py
   e. Rejects (401/403) if key is invalid or inactive
6. Route handler runs (routes.py)
7. Handler calls into astronomy.py, database.py, etc.
8. Handler returns jsonify(result)
9. Middleware logs the response
10. Flask returns JSON response
```

The `g.user` object (Flask's per-request global) is available in every route handler after authentication. It contains the key's identity, rate limits, and output configuration.

### Rate limiting

Rate limiting uses **Flask-Limiter** with a per-user key function. The limiter is set up in `create_app()`:

```python
limiter = Limiter(
    app=app,
    key_func=_get_rate_limit_key,   # "user:42" or "ip:1.2.3.4"
    default_limits=[
        _per_user_limit('per_day',  config_class.RATE_LIMIT_PER_DAY),
        _per_user_limit('per_hour', config_class.RATE_LIMIT_PER_HOUR),
    ],
    storage_uri="memory://"
)
```

The `_per_user_limit()` function returns a callable that Flask-Limiter calls for each request. It checks `g.user` and returns either the key's specific limit or the class default:

- Admin keys always get `"999999 per <unit>"` — effectively unlimited
- Keys with a per-key limit use that value
- All other keys fall back to the class default from `key_class_limits` table

**Important:** Rate limits are stored in memory. They reset when the server restarts. For production multi-worker deployments, switch `storage_uri` to Redis.

### Middleware

`middleware.py` provides:

- **Request logging** — logs method, path, response status, and duration
- **Error handlers** — catches unhandled exceptions and returns consistent JSON error shapes with a `status` field

The `_error()` helper in `routes.py` should be used for all error returns:

```python
def _error(message: str, status: int) -> tuple:
    return jsonify({'error': message, 'status': status}), status
```

---

## 4. Authentication and key management

### How keys are stored

API keys are **never stored in plaintext**. The storage flow is:

```
Plaintext key (shown to user once)
        │
        ▼
KeyCrypto.encrypt()   — Fernet AES-128 symmetric encryption
        │
        ▼
key_enc column        — encrypted ciphertext stored in api_keys table
key_prefix column     — first 8 characters stored plaintext for fast lookup
```

The Fernet key is derived from `SECRET_KEY` in `.env`. **If `SECRET_KEY` changes, all stored keys become unreadable.** Never change it on a running system.

`key_crypto.py` exposes three methods:

```python
crypto = KeyCrypto(secret_key)

plaintext = KeyCrypto.generate_key()          # generate a new random key
encrypted = crypto.encrypt(plaintext)          # encrypt for storage
decrypted = crypto.decrypt(encrypted)          # decrypt for comparison
prefix    = crypto.prefix(plaintext)           # first 8 chars for lookup
```

### How keys are verified

Lookup is a two-step process designed to avoid decrypting every key on every request:

```python
# auth.py — simplified
prefix   = api_key[:8]
candidates = db_manager.get_api_keys_by_prefix(prefix)
# candidates is usually 0 or 1 records

for record in candidates:
    decrypted = crypto.decrypt(record['key_enc'])
    if secrets.compare_digest(decrypted, api_key):
        return record  # authenticated
```

`secrets.compare_digest` is used instead of `==` to prevent timing attacks.

### The user object

After successful authentication, `users.py` converts the raw database record into a clean dict and stores it in `g.user`:

```python
g.user = {
    'id':          '42',
    'name':        'Jane Smith',
    'identifier':  'myapp.com',
    'key_type':    'domain',      # 'domain', 'user', or 'wildcard'
    'is_domain':   True,
    'is_user':     False,
    'admin':       False,
    'active':      True,
    'rate_limits': {
        'per_minute': 20,
        'per_hour':   200,
        'per_day':    1000,
    },
    'output':      { ... },       # stored output_config JSON, may be empty dict
}
```

Route handlers access this as `user = getattr(g, 'user', {})`.

### Key class limits

The `key_class_limits` table stores default rate limits for each key class (`domain`, `user`, `wildcard`). When resolving limits for a specific key, the priority is:

```
key_record.rate_per_minute  (per-key override, may be NULL)
    └─► falls back to class_limits.rate_per_minute  (class default)
```

Class limits are editable via the admin portal (Class Limits page) and via `POST /admin/class-limits`.

---

## 5. Astronomy calculations

All Swiss Ephemeris work is encapsulated in `astronomy.py`. Nothing else in the codebase calls `swisseph` (pyswisseph) directly — everything goes through `AstronomyService`.

### AstronomyService

```python
svc = AstronomyService(ephemeris_path='/srv/ephemeral/app/sweph')
```

The service is instantiated once in `routes.py` at module load time and reused for every request. It holds no per-request state.

### Swiss Ephemeris integration

The Swiss Ephemeris (`swe`) operates on **Julian Day Numbers** (JD) — a continuous count of days since noon on 1 January 4713 BCE. All internal calculations use JD. Conversion between calendar dates and JD happens at the entry and exit points of `AstronomyService`.

Key `swe` functions used:

| Function | Purpose |
|---|---|
| `swe.calc_ut(jd, body, flags)` | Calculate position of a body at a JD |
| `swe.houses(jd, lat, lon, system)` | Calculate house cusps |
| `swe.julday(year, month, day, hour)` | Convert calendar date to JD |
| `swe.revjul(jd)` | Convert JD back to calendar date |
| `swe.nod_aps_ut(jd, body, method)` | Calculate lunar apsides |

The `FLG_SPEED` flag must be passed to `swe.calc_ut()` to get velocity data. Without it, speed fields are always zero. This is set on all calls in `_calculate_position()`.

### Chart calculation flow

When `POST /calculate` is called:

```
1. Validate request (validators.py — Marshmallow schema)
2. Check cache: does a chart with this datetime + location already exist?
   └─ Yes → return cached chart
3. Resolve location (geocoding / place_repository.py)
4. Convert datetime to UTC, then to Julian Day
5. Call AstronomyService.calculate_planetary_positions()
   a. _get_active_bodies() → filters bodies list from output config
   b. For each active body: _calculate_position(jd, body_id, flags)
   c. _calculate_houses(jd, lat, lon, system) → house cusps
   d. _angle_position() for ASC, MC, Vertex, East Point
   e. _derived_point() for Part of Fortune, nodes, Lilith
6. Build response dict
7. Save to cache (database.py)
8. Return JSON
```

`_calculate_position()` handles the swe flag arithmetic and assembles the position dict. All positions are returned in ecliptic coordinates with optional equatorial coordinates appended.

### Derived charts

Secondary progressions, solar arc, solar return, and lunar return all follow the same pattern:

1. Load the natal chart from the database using `chart_id`
2. Calculate the derived chart data
3. Save as a `derived_charts` record linked to the natal chart
4. Return the derived chart data plus a `derived_chart_id`

**Secondary progressions** advance each natal body by one day per year of life. The progressed Julian day is `natal_jd + age_in_days`, then `calculate_planetary_positions()` is called on that JD.

**Solar arc** first calculates the secondary progression to find the progressed Sun's longitude, then applies the arc `(progressed_sun - natal_sun)` uniformly to every natal body.

**Solar and lunar returns** use Newton's method to find the exact JD when the Sun/Moon returns to its natal longitude:

```python
# astronomy.py — _find_return_jd() simplified
jd = start_jd
for _ in range(50):              # max iterations
    pos  = swe.calc_ut(jd, body, flags)[0][0]   # current longitude
    diff = target_longitude - pos
    if abs(diff) < 0.0001:       # ~0.36 arc seconds — close enough
        return jd
    speed = swe.calc_ut(jd, body, flags)[0][3]  # longitude speed
    jd   += diff / speed         # Newton step
```

### Apsides and lunations

**Apside finding** (`calculate_next_apsides`) uses a distance speed sign-change approach: scan forward in time at coarse intervals, detect when `distance_speed` changes sign (the distance is at a minimum or maximum), then refine with `_refine_apside_jd()` using bisection.

**Lunation finding** (`find_lunations`) scans for when `sun_moon_angle mod 360` crosses the target angle (0°, 90°, 180°, 270°), then refines with Newton's method.

---

## 6. Database layer

### DatabaseManager

`DatabaseManager` is a wrapper around Python's built-in `sqlite3` module. There is no ORM — all queries are raw SQL. The manager is instantiated once in `routes.py` and injected into route handlers via closure.

```python
# routes.py
db_manager = DatabaseManager(config.DATABASE_PATH)
```

The `get_connection()` method uses a context manager that ensures connections are always closed:

```python
with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute(...)
```

`sqlite3.Row` is used as the row factory, which allows columns to be accessed by name (`row['column_name']`) as well as by index.

### Schema overview

```
api_keys ──────────────────────┐
  id, key_type, name,          │
  identifier, key_enc,         │
  key_prefix, admin, active,   │
  rate_per_minute/hour/day,    │
  output_config                │
                               │
key_class_limits               │
  key_type (domain/user/wildcard)
  rate_per_minute/hour/day     │
                               │
registration_requests          │
  id, api_key_id ──────────────┘
  domain, name, contact_email,
  reason, status, admin_note

email_verifications
  token, api_key_id, used, expires_at

smtp_config
  key, value  (key-value store)

charts ─────────────────────────┐
  id (UUID), chart_type,        │
  chart_name, datetime_utc,     │
  location_id, chart_data (JSON)│
  chart_hash                    │
                                │
derived_charts                  │
  id (UUID), chart_id ──────────┘
  secondary_chart_id,
  chart_type, chart_name,
  chart_data (JSON)

locations
  id, query_text, latitude,
  longitude, timezone_id,
  formatted_address

canonical_places ───────────────┐
  id, google_place_id,          │
  normalized_key, formatted_name│
                                │
place_aliases                   │
  alias → canonical_place_id ───┘

place_cache
  canonical_place_id, geo_data (JSON),
  expires_at

place_lookup_log
  query, result_type, duration_ms
```

### Table reference

| Table | Purpose |
|---|---|
| `api_keys` | All API keys — encrypted ciphertext, prefix, rate limits, output config |
| `key_class_limits` | Default rate limits per key class |
| `registration_requests` | Domain key registration submissions awaiting admin review |
| `email_verifications` | One-time tokens for email verification (user key activation) |
| `smtp_config` | SMTP server settings (key/value rows) |
| `charts` | Cached natal and event charts |
| `derived_charts` | Secondary progressions, returns, solar arc results |
| `locations` | Geocoded location records (legacy) |
| `canonical_places` | Deduplicated place records from Google |
| `place_aliases` | Multiple query strings mapping to the same canonical place |
| `place_cache` | Cached place resolution responses with expiry |
| `place_lookup_log` | Performance and usage logging for geocoding |

---

## 7. Output configuration system

### How the three-level merge works

Every calculation endpoint resolves an output configuration before touching the Swiss Ephemeris. The resolution happens in three layers, with later layers overriding earlier ones:

```
Layer 1:  OutputConfig.as_dict()     ← server-wide defaults (output_config.py)
Layer 2:  g.user['output']           ← per-key stored overrides (api_keys.output_config)
Layer 3:  request_body.get('output') ← per-request overrides (from the API caller)
```

The merge is applied in routes.py at the start of each calculation handler:

```python
user_output       = user.get('output', {})
request_overrides = data.get('output', {})

output_cfg = OutputConfig.merge(user_output)           # Layer 1 + Layer 2
output_cfg = OutputConfig.merge_onto(output_cfg, request_overrides)  # + Layer 3
```

**`OutputConfig.merge(overrides)`** starts from server defaults and applies `overrides` on top. Only keys present in `overrides` are changed.

**`OutputConfig.merge_onto(base, overrides)`** applies `overrides` on top of an already-merged dict. Same logic, but the starting point is `base` not the class defaults.

### OutputConfig class

`output_config.py` is a class of class attributes (no instance needed) plus classmethods:

```python
class OutputConfig:
    GEOCENTRIC   = True
    HELIOCENTRIC = True
    SUN          = True
    # ... all defaults ...

    @classmethod
    def as_dict(cls) -> dict:       # returns full nested dict of defaults
    @classmethod
    def merge(cls, overrides) -> dict:      # server defaults + overrides
    @classmethod
    def merge_onto(cls, base, overrides) -> dict:  # base dict + overrides
    @classmethod
    def from_cfg(cls, cfg_dict) -> dict:    # import from .cfg flat format
    @classmethod
    def to_cfg_dict(cls, stored) -> dict:   # export to flat format for forms
```

### Storing and retrieving per-key config

The `output_config` column in `api_keys` stores a **sparse JSON object** — only the values that differ from server defaults are saved. This means:

- If a key has `output_config = NULL`, it uses server defaults for everything
- If a key has `output_config = {"heliocentric": false}`, only heliocentric is overridden — everything else uses defaults
- The full effective config is always computed fresh by merging, never stored

When a user saves output config via the portal or `POST /me/output`, the full form state is sent but can be reduced to just the diffs. The current implementation stores whatever the user sends, which may include fields that match the server default — this is harmless since merge logic handles it correctly.

---

## 8. Location resolution

### Resolution pipeline

When a location string arrives in a request, `place_repository.py` coordinates the lookup:

```
1. Normalise the query string (location_normaliser.py)
   └─ lowercase, strip punctuation, canonical form

2. Check place_aliases table
   └─ Has this exact normalised string been looked up before?
   └─ Yes → get canonical_place_id → go to step 5

3. Check canonical_places table
   └─ Does a canonical place with this normalised key exist?
   └─ Yes → go to step 5

4. Google Maps geocoding (geocoding.py)
   └─ Call Maps Geocoding API
   └─ Track usage (api_usage.py)
   └─ Save to canonical_places
   └─ Save alias mapping

5. Check place_cache table
   └─ Is there a non-expired cache entry for this canonical place?
   └─ Yes → return cached data

6. Google Maps timezone lookup (geocoding.py)
   └─ Call Maps Timezone API
   └─ Save result to place_cache (expires 30 days)

7. Return location dict with lat, lon, timezone, UTC offset, DST flag
```

### Caching strategy

Two caches operate at different levels:

**Alias cache** (`place_aliases`): maps normalised query strings to `canonical_place_id`. This is permanent — "London" always resolves to the same canonical place.

**Place cache** (`place_cache`): stores the full geocoding + timezone data for a canonical place. Expires after 30 days. This handles DST changes — a place's UTC offset might change between summer and winter, so the full resolution is refreshed periodically.

The `cleanup.py` module has functions for removing expired cache entries. It can be run as a cron job.

---

## 9. Email service

### Configuration priority

`EmailService` loads its configuration fresh on each instantiation (i.e., each time an email is sent). This means admin changes to SMTP settings via the portal take effect immediately without a server restart.

The load order:

```python
# 1. Try database (smtp_config table)
db_cfg = db_manager.get_smtp_config()

# 2. Fall back to environment variable for any missing key
for key, (env_var, default) in _ENV_MAP.items():
    db_val = db_cfg.get(key, '').strip()
    cfg[key] = db_val if db_val else os.environ.get(env_var, default)
```

If `host`, `user`, and `password` are all set, `self.enabled = True` and email sending is active. If any are missing, sending is silently skipped with a warning log — this is intentional so the server can run without email configured (e.g. during development).

### Adding a new email type

1. Add a new method to `EmailService` following the existing pattern:

```python
def send_my_notification(self, to_email: str, name: str, data: str) -> bool:
    subject = "ephemeralREST — Your notification"
    text = f"""Hello {name},\n\n{data}\n\nephemeralREST\n"""
    html = f"""<!DOCTYPE html><html><body>
      <h2>Your notification</h2>
      <p>{data}</p>
    </body></html>"""
    return self._send(to_email, subject, text, html)
```

2. Call it from `routes.py` where the event occurs:

```python
svc = EmailService()
svc.send_my_notification(to_email=record['contact_email'], name=record['name'], data=some_data)
```

The `_send()` method handles the SMTP connection, TLS/SSL selection, and error logging.

---

## 10. Routes reference

All 44 routes are in `routes.py`. They are organised in this order in the file:

| Section | Routes |
|---|---|
| Location | `/autocomplete`, `/locations/resolve` |
| Charts | `/calculate`, `/chart/<id>`, `/cache/stats`, `/cache/cleanup`, `/health`, `/cors-test` |
| Derived charts | `/chart/<id>/progressions`, `/chart/<id>/solar-arc`, `/chart/<id>/solar-return`, `/chart/<id>/lunar-return`, `/chart/<id>/derived`, `/derived/<id>` (GET + DELETE) |
| Ephemeris | `/apsides`, `/lunations`, `/apsides/next`, `/ephemeris` |
| Registration | `/register/domain`, `/register/user`, `/register/verify` |
| Admin registrations | `/admin/registrations`, `/admin/registrations/<id>/approve`, `/admin/registrations/<id>/reject` |
| Self-service | `/me`, `/me/output` (GET + POST), `/me/rotate` |
| Admin keys | `/admin/keys` (GET), `/admin/keys/<id>` (GET + DELETE), `/admin/keys/<id>/disable`, `/admin/keys/<id>/enable`, `/admin/keys/<id>/rotate`, `/admin/keys/<id>/limits`, `/admin/keys/<id>/output` (GET + POST) |
| Admin class limits | `/admin/class-limits/<type>`, `/admin/class-limits` |
| Admin SMTP | `/admin/smtp` (GET + POST + DELETE), `/admin/smtp/test` |

The route handler for `/calculate` is the most complex in the codebase. It is worth reading through it in full to understand the calculation and caching flow before modifying anything in that area.

---

## 11. The admin portal (PHP)

### File structure

The portal is a set of standalone PHP scripts. There is no framework, router, or ORM. Each `.php` file is a complete page: it handles POST actions at the top, fetches data from the API, then includes `header.php`, outputs its HTML, and includes `footer.php`.

```php
<?php
require_once __DIR__ . '/config.php';
require_once __DIR__ . '/includes/api.php';
require_once __DIR__ . '/includes/auth.php';
auth_require('admin');        // redirect to login if not authenticated

$page_title = 'My Page';

// Handle POST at the top
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ...) {
    // ...
}

// Fetch data
$result = my_api_get('/some/endpoint');

// Render
require_once __DIR__ . '/includes/header.php';
?>

<!-- HTML here -->

<?php require_once __DIR__ . '/includes/footer.php'; ?>
```

### Authentication flow

The portal has its own session-based authentication separate from the API key authentication:

```
1. User visits any protected page
2. auth_require() in auth.php checks $_SESSION['logged_in']
3. Not logged in → redirect to /login.php
4. User enters API key in login.php
5. login.php calls auth_login($api_key)
6. auth_login() makes GET /me with the supplied key
7. /me returns the user's identity and key type
8. Identity stored in $_SESSION['user'], $_SESSION['api_key']
9. Redirected to appropriate portal:
   - admin    → portal-admin.php
   - domain   → portal-domain.php
   - user     → portal-user.php
```

Every subsequent API call uses `my_api_get()` / `my_api_post()` from `auth.php`, which reads `$_SESSION['api_key']` and includes it as `X-API-Key`. This means the portal always operates with the permissions of the signed-in user's key.

### AJAX pattern

All modal saves and in-page updates use a consistent AJAX pattern. The PHP file handles both regular page loads and AJAX requests, distinguished by the `X-Requested-With: XMLHttpRequest` header:

```php
// PHP side — detect AJAX
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_SERVER['HTTP_X_REQUESTED_WITH'])) {
    header('Content-Type: application/json');
    $input  = json_decode(file_get_contents('php://input'), true) ?? [];
    $action = $input['action'] ?? '';
    // ... handle and echo json_encode([...])
    exit;
}
```

```javascript
// JS side — make request
const res  = await fetch('/page.php', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ action: 'save', ...data })
});
const data = await res.json();
data.status = data.status ?? res.status;  // ensure status is always present
```

All JS error display uses the `apiError(data)` function (defined inline per page) which handles 429 specially and falls back through `data.error → data.message → 'HTTP N'`.

### Adding a new page

1. Copy the pattern from an existing page (e.g. `class-limits.php`)
2. Add it to the nav arrays in `includes/header.php`:

```php
// In the $admin_nav array:
'my-page' => ['label' => 'My Page', 'icon' => '◎'],

// In the nav loop:
<?php foreach (['portal-admin','registrations','keys','class-limits','smtp','my-page'] as $page): ?>
```

3. Add any new API endpoints it needs to `routes.py` and the protected endpoints list in `app.py`

---

## 12. Registration and key provisioning

### Domain key registration flow

```
User fills form → POST /register/domain
    │
    ├─ Creates api_keys record (active=0, key generated and encrypted)
    ├─ Creates registration_requests record (status='pending')
    └─ Sends confirmation email to registrant
       Sends notification email to admin

Admin reviews in portal → POST /admin/registrations/<id>/approve
    │
    ├─ Sets api_keys.active = 1
    ├─ Sets registration_requests.status = 'approved'
    └─ Sends approval email containing plaintext key
       (key is re-decrypted from database for this email only)
```

The key is generated at registration time and stored encrypted. The plaintext key is not stored anywhere after the initial creation — it is re-derived from the ciphertext using Fernet decryption when needed for the approval email.

### Key rotation

When a key is rotated (by admin via `POST /admin/keys/<id>/rotate`, or by the user via `POST /me/rotate`):

```
1. Generate new random plaintext key
2. Encrypt with KeyCrypto
3. Update api_keys.key_enc and api_keys.key_prefix
4. Return plaintext key in response (once only)
5. Old key is immediately invalid — Fernet decryption of the old
   ciphertext would produce a different string
```

---

## 13. Key manager CLI

`key_manager.py` is a command-line tool for managing keys without using the API. It connects directly to the database. Use it for initial setup and emergency administration.

```bash
source .venv/bin/activate
python3 key_manager.py --help

# Create an admin key
python3 key_manager.py create --type domain --identifier admin.local --name "Admin" --admin

# List all active keys
python3 key_manager.py list

# Show a key's details (including decrypted prefix confirmation)
python3 key_manager.py show --identifier myapp.com

# Rotate a key (prints new plaintext key)
python3 key_manager.py rotate --identifier myapp.com

# Set rate limits
python3 key_manager.py set-limits --identifier myapp.com --per-minute 30 --per-hour 400

# Set class defaults
python3 key_manager.py class-limits --type domain --per-minute 20 --per-hour 200 --per-day 1000

# Import from legacy .cfg files
python3 key_manager.py migrate
```

The CLI creates an `EmailService` instance on commands that send email (approval, rotation). If SMTP is not configured, the email step is skipped and a warning is printed.

---

## 14. Adding a new endpoint

This section walks through the full process of adding a new endpoint to the API.

### Step 1: Define the route in routes.py

```python
@api.route('/my-endpoint', methods=['POST'])
def my_endpoint():
    """
    Short description of what this does.
    """
    user = getattr(g, 'user', {})
    # Optional: admin check
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data = request.get_json(silent=True) or {}

    # Validate
    my_field = data.get('my_field', '').strip()
    if not my_field:
        return _error('my_field is required', 400)

    # Do the work
    result = some_service.do_something(my_field)

    return jsonify({'result': result})
```

### Step 2: Add to the protected endpoints list in app.py

If the endpoint requires authentication, add it:

```python
_protected = [
    # ... existing endpoints ...
    'api.my_endpoint',
]
```

If it is a public endpoint, do not add it — unauthenticated requests will pass through.

### Step 3: Add a validator if needed

For complex request bodies, add a Marshmallow schema in `validators.py`:

```python
class MyEndpointSchema(Schema):
    my_field   = fields.Str(required=True)
    optional   = fields.Int(load_default=10)

    @validates('my_field')
    def validate_my_field(self, value):
        if len(value) > 100:
            raise ValidationError('my_field must be 100 characters or less')
```

Then use the `@validate_request` decorator in `routes.py`:

```python
@api.route('/my-endpoint', methods=['POST'])
@validate_request(MyEndpointSchema)
def my_endpoint(validated_data):
    my_field = validated_data['my_field']
```

### Step 4: Update the documentation

Update `api-reference.md` with:
- A description of the endpoint
- The request field table
- A `curl` example
- The response structure

---

## 15. Common patterns and conventions

### Error returns

Always use `_error()`:

```python
return _error('chart_name is required', 400)
return _error('Chart not found', 404)
return _error('Admin access required', 403)
```

Never return raw strings or non-JSON bodies.

### Admin checks

Every admin endpoint starts with:

```python
user = getattr(g, 'user', {})
if not user.get('admin'):
    return _error('Admin access required', 403)
```

### Database access in routes

`db_manager` and `astro_service` are module-level objects in `routes.py`, available to all route handlers by closure.

### JSON column handling

The `output_config` and `chart_data` columns store JSON as text. `DatabaseManager` handles serialisation/deserialisation automatically in its methods. Do not call `json.dumps` or `json.loads` manually when using `db_manager` methods.

### UUIDs for chart IDs

Charts and derived charts use UUID4 as their primary key (stored as text). Generate with:

```python
import uuid
chart_id = str(uuid.uuid4())
```

### PHP: always use htmlspecialchars

All user-provided data rendered in PHP HTML must be escaped:

```php
<?= htmlspecialchars($value) ?>
```

Never echo unescaped strings.

### PHP: my_api_* vs api_*

- `my_api_get()` / `my_api_post()` — use the logged-in session key. Use for all authenticated pages.
- `api_get()` / `api_post()` — use `ADMIN_API_KEY` from `config.php`. Use only for public endpoints or in scripts that run without a session.

---

## 16. Configuration reference

All values are set in `.env`. The `Config` class in `config.py` exposes them.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | — | Used to derive Fernet encryption key. Never change on a live system |
| `DATABASE_PATH` | No | `ephemeral.db` | Path to SQLite database file |
| `SWISS_EPHEMERIS_PATH` | Yes | — | Path to directory containing `.se1` data files |
| `GOOGLE_MAPS_API_KEY` | Yes | — | Google Maps API key (Geocoding + Timezone APIs must be enabled) |
| `FLASK_HOST` | No | `127.0.0.1` | Host to bind to |
| `FLASK_PORT` | No | `5000` | Port to bind to |
| `FLASK_DEBUG` | No | `false` | Enable Flask debug mode — never true in production |
| `RATE_LIMIT_ENABLED` | No | `true` | Enable rate limiting |
| `RATE_LIMIT_PER_MINUTE` | No | `30` | Global fallback per-minute limit |
| `RATE_LIMIT_PER_HOUR` | No | `300` | Global fallback per-hour limit |
| `RATE_LIMIT_PER_DAY` | No | `2000` | Global fallback per-day limit |
| `CORS_ORIGINS` | No | `*` | Allowed CORS origins (comma-separated) |
| `MAX_MONTHLY_REQUESTS` | No | `5000` | Google API monthly budget cap |
| `USAGE_COUNT_FILE` | No | `api_usage.json` | Path for usage tracking file |
| `SMTP_HOST` | No | — | SMTP server hostname (can be set via portal instead) |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SMTP_USER` | No | — | SMTP username |
| `SMTP_PASSWORD` | No | — | SMTP password |
| `SMTP_FROM` | No | — | From address (defaults to SMTP_USER) |
| `SMTP_TLS` | No | `true` | Enable STARTTLS |
| `SMTP_SSL` | No | `false` | Use SSL connection (port 465) |
| `ADMIN_EMAIL` | No | — | Receives new registration notifications |
| `API_BASE_URL` | No | `http://localhost:5000` | Used in email links |