# ephemeralREST — Architecture & Developer Guide

This document describes how the codebase is structured, how the components relate to each other, and how to navigate and modify the code.

---

## Table of contents

1. [System overview](#1-system-overview)
2. [Directory structure](#2-directory-structure)
3. [The API backend](#3-the-api-backend)
4. [Authentication and key management](#4-authentication-and-key-management)
5. [Login, 2FA, and trusted devices](#5-login-2fa-and-trusted-devices)
6. [Astronomy calculations](#6-astronomy-calculations)
7. [Database layer](#7-database-layer)
8. [Output configuration system](#8-output-configuration-system)
9. [Location resolution](#9-location-resolution)
10. [Email service](#10-email-service)
11. [Portal settings](#11-portal-settings)
12. [Routes reference](#12-routes-reference)
13. [The admin portal (PHP)](#13-the-admin-portal-php)
14. [Registration and key provisioning](#14-registration-and-key-provisioning)
15. [Key manager CLI](#15-key-manager-cli)
16. [Adding a new endpoint](#16-adding-a-new-endpoint)
17. [Common patterns and conventions](#17-common-patterns-and-conventions)
18. [Configuration reference](#18-configuration-reference)

---

## Licence

ephemeralREST is licensed under the **GNU Affero General Public License v3 (AGPL v3)**. This licence was selected for compatibility with the Swiss Ephemeris library, which is itself AGPL v3. Because the Swiss Ephemeris is linked at runtime, the combined work must be distributed under the AGPL v3.

The critical difference between GPL v3 and AGPL v3 is the **network service clause**. Under AGPL v3, users who interact with the software over a network are legally entitled to receive the source code of the running application. A public GitHub repository satisfies this obligation.

Every Python source file includes the standard AGPL v3 notice in its header.

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
         │  SQLite or MySQL    │
         │  ephemeral.db /     │
         │  MySQL database     │
         └─────────────────────┘
```

**The Flask API** does all computation and data storage. It validates requests, calls the Swiss Ephemeris, manages users and keys, and returns JSON.

**The PHP admin portal** is a thin client. It never touches the database directly — it makes HTTP requests to the Flask API using the logged-in user's session key, and renders the responses as HTML.

**The database** stores everything: charts, keys, locations, SMTP config, portal settings. There is no separate cache layer. `DB_TYPE` selects SQLite (default, no server required) or MySQL; see §7.

---

## 2. Directory structure

### API backend (Python)

```
/srv/ephemeral/app/
│
├── app.py                  Application factory, rate limiter, protected endpoint list
├── config.py               All configuration, loaded from .env
├── routes.py               All API endpoints (~55 routes)
├── astronomy.py            Swiss Ephemeris wrapper (AstronomyService)
├── database.py             SQLite wrapper (DatabaseManager)
├── auth.py                 API key authentication (AuthManager)
├── users.py                Builds the g.user object from a key record
├── key_crypto.py           Fernet encryption for stored API keys (KeyCrypto)
├── key_manager.py          CLI tool for key administration
├── output_config.py        Output config defaults and merge logic (OutputConfig)
├── validators.py           Marshmallow request schemas
├── middleware.py           Request logging, error handling
├── email_service.py        SMTP transactional email (EmailService)
├── geocoding.py            Google Maps geocoding wrapper
├── location_normaliser.py  Normalises location strings for cache keys
├── place_repository.py     Location lookup and cache coordination
├── api_usage.py            Google API budget tracker
├── cleanup.py              Cache maintenance utilities
├── gunicorn_config.py      Gunicorn worker configuration
│
├── sweph/                  Swiss Ephemeris data files (*.se1, *.eph)
├── .env                    Environment configuration (not committed)
├── ephemeral.db            SQLite database (not committed)
└── requirements.txt
```

### Admin portal (PHP)

```
/srv/ephemeral/admin/
│
├── landing.php             Public home page
├── login.php               Email + password sign-in
├── logout.php              Clear session and redirect
├── setup.php               First-run admin account creation (disabled after first use)
├── 2fa.php                 Two-factor authentication code entry
├── forgot-password.php     Request a password reset email
├── set-password.php        Set or reset a password (token or current-password flows)
├── verify.php              Email verification landing page
├── register-user.php       Self-service account registration form
│
├── portal-admin.php        Admin dashboard
├── portal-user.php         Standard user self-service
│
├── keys.php                List and manage all API keys (admin)
├── key-detail.php          Single key detail, limits, admin flag (admin)
├── key-output.php          Per-key output configuration editor (admin + user)
├── class-limits.php        Default rate limit settings (admin)
├── smtp.php                SMTP configuration (admin)
├── email-templates.php     Customise transactional email templates (admin)
├── portal-settings.php     Portal behaviour settings (admin)
├── api-tester.php          Interactive API explorer
│
├── config.php              API_BASE and SITE_NAME only — two lines
│
├── includes/
│   ├── api.php             HTTP client: api_get/post(), portal_setting() helpers
│   ├── auth.php            Session auth, 2FA, trusted-device cookie management
│   ├── header.php          HTML head, sidebar nav, flash messages
│   └── footer.php          Closing HTML, shared JS
│
└── assets/
    └── style.css           Dark-mode stylesheet
```

---

## 3. The API backend

### Application factory

The API uses Flask's application factory pattern. Everything is initialised inside `create_app()`:

```python
def create_app(config_class=Config):
    app = Flask(__name__)
    # register Blueprint, Limiter, middleware
    return app
```

Gunicorn calls it as `gunicorn "app:create_app()"`.

All routes live in `routes.py`, attached to a single Blueprint (`api`).

### Configuration

All configuration is loaded from `.env` via `config.py`. The `Config` class reads environment variables and exposes them as class attributes. Never read `os.environ` directly in application code — always go through `Config`.

A bootstrap function writes a `.env` template on first run if none exists, then exits so the operator can fill it in before starting the service.

### Request lifecycle

Every authenticated API request goes through:

```
1. nginx → Gunicorn (port 5000)
2. Flask-Limiter checks rate limits
3. Middleware logs the request
4. AuthManager.require_api_key() runs (if protected endpoint):
   a. Reads X-API-Key header
   b. Looks up key by 8-char prefix in database
   c. Decrypts candidate keys and compares with secrets.compare_digest
   d. Builds g.user dict via users.py
   e. Returns 401/403 if key is invalid, inactive, or must_change_password is set
5. Route handler runs
6. Middleware logs the response
```

The `g.user` object is available in every route handler after authentication.

### Rate limiting

Flask-Limiter with per-user key function. Admin keys are effectively unlimited (limit set to `999999`). Per-key overrides take priority over class defaults.

**Important:** Rate limits are stored in memory and reset on server restart. Switch to Redis for multi-worker production.

---

## 4. Authentication and key management

### Key storage

API keys use **Fernet (AES-128) symmetric encryption** — not one-way hashing. This means the plaintext key can be recovered server-side when needed (e.g. to deliver it to the user after email verification or to include it in the login response).

```
Plaintext key (shown once to user)
    │
    ▼
KeyCrypto.encrypt()   ← Fernet, keyed from SECRET_KEY
    │
    ▼
key_enc column        ← encrypted ciphertext
key_prefix column     ← first 8 chars, plaintext, for fast prefix lookup
```

**If `SECRET_KEY` changes, all stored keys become unreadable.** Never change it on a live system.

### Key verification (X-API-Key flow)

```python
prefix     = api_key[:8]
candidates = db_manager.get_api_keys_by_prefix(prefix)   # usually 0 or 1
for record in candidates:
    decrypted = crypto.decrypt(record['key_enc'])
    if secrets.compare_digest(decrypted, api_key):
        return record
```

### Password authentication

Alongside the API key, every user account now has a **bcrypt password** (via `werkzeug.security`). The password is used to authenticate through the portal's login flow. The API key itself is never entered by the user — it is decrypted server-side on successful login and stored in the PHP session.

Schema additions to `api_keys`:

| Column | Type | Purpose |
|---|---|---|
| `password_hash` | TEXT nullable | werkzeug password hash |
| `must_change_password` | INTEGER (default 1) | Forces set-password flow before login completes |

### The g.user object

```python
g.user = {
    'id':                   '42',
    'name':                 'Jane Smith',
    'identifier':           'jane@example.com',   # always an email address
    'admin':                False,
    'active':               True,
    'must_change_password': False,
    'rate_limits': {
        'per_minute': 20,
        'per_hour':   200,
        'per_day':    1000,
    },
    'output': { ... },    # sparse JSON, may be empty dict
}
```

Admin keys have `rate_limits` nulled — they bypass rate limiting entirely.

### Key class limits

The `key_class_limits` table holds a single `'user'` row with default rate limits. Per-key overrides take priority. Editable via `POST /admin/class-limits` or the portal's Rate Limits page.

---

## 5. Login, 2FA, and trusted devices

The portal uses a multi-step login flow. The API provides the endpoints; the portal handles the UI.

### Login flow

```
POST /login  (email + password + optional device_token)
    │
    ├─ must_change_password = true
    │   └─ Response: { must_change_password: true }
    │      Portal → set-password.php
    │
    ├─ Admin + SMTP not configured
    │   └─ Skip 2FA (avoids chicken-and-egg on fresh install)
    │      Response: identity + decrypted API key
    │
    ├─ Valid trusted-device token in cookie
    │   └─ Skip 2FA
    │      Response: identity + decrypted API key
    │
    └─ Normal case
        └─ Send 2FA code via email
           Response: { 2fa_required: true }

POST /login/2fa  (email + code + remember_device)
    └─ Verify code
       Response: identity + decrypted API key
                 + device_token (if remember_device was true)
```

The decrypted API key is stored in `$_SESSION['user']['api_key']` and used for all subsequent `my_api_*` portal calls. It is never sent to the browser.

### Trusted devices

When a user ticks "Remember this device", the API creates a `trusted_devices` row and returns a `device_token`. The portal stores this in the `epht_device` cookie as a **JSON map keyed by email address**, allowing multiple accounts on the same browser:

```json
{
  "alice@example.com": "token_abc...",
  "bob@example.com":   "token_xyz..."
}
```

On subsequent logins, the token for the signing-in email is extracted and sent to `POST /login`. If the token is valid and unexpired (default 28 days, configurable via portal settings), 2FA is skipped.

**Logout does not revoke the trusted-device token** — the device remains trusted for future logins. To explicitly forget a device, `auth_forget_device()` can be called, which revokes the token via `POST /me/forget-device` and removes it from the cookie map.

### 2FA bypass for admin without SMTP

If the account is an admin and the SMTP `host` field is not set in the database, the 2FA step is skipped entirely. This prevents a catch-22 on fresh installs where the admin cannot log in to configure SMTP because 2FA requires SMTP.

### Password reset flow

```
POST /password/forgot  (email)
    └─ Generates token, sends password-reset-required email
       Always returns the same generic message (no enumeration)

GET /set-password.php?t=TOKEN  (portal)
    └─ Calls POST /password/set with { token, new_password }
       Sets password_hash, clears must_change_password
       Invalidates all trusted devices for the account
```

### First-run setup

`GET /setup/status` — public endpoint, returns `{ setup_required: true }` when the database is empty. The portal checks this on every page load and redirects to `setup.php` if true.

`POST /setup` — creates the first admin account (name, email, password). Returns the decrypted API key in the response — shown once on the success screen. Only works when zero keys exist.

---

## 6. Astronomy calculations

All Swiss Ephemeris work is encapsulated in `astronomy.py`. Nothing else calls `swisseph` directly.

### AstronomyService

Instantiated once in `routes.py` at module load time. Stateless — holds no per-request state.

### Swiss Ephemeris integration

The Swiss Ephemeris operates on **Julian Day Numbers (JD)** — a continuous day count since noon on 1 January 4713 BCE. All internal calculations use JD. Conversion between calendar dates and JD happens at entry/exit points only.

| Function | Purpose |
|---|---|
| `swe.calc_ut(jd, body, FLG_SPEED)` | Position + velocity (speed flag is mandatory) |
| `swe.houses(jd, lat, lon, system)` | House cusps |
| `swe.julday(year, month, day, hour)` | Date → JD |
| `swe.revjul(jd)` | JD → date |
| `swe.nod_aps_ut(jd, body, method)` | Lunar apsides |

### Chart calculation flow

```
POST /calculate
    1. Validate request (Marshmallow)
    2. Check cache (chart_hash lookup)
       └─ Cache hit → return cached chart
    3. Resolve location (place_repository.py)
    4. Convert datetime → UTC → Julian Day
    5. AstronomyService.calculate_planetary_positions()
       a. Filter bodies from output config
       b. _calculate_position() for each body
       c. _calculate_houses() for house cusps
       d. Angles (ASC, MC, Vertex, East Point)
       e. Derived points (Part of Fortune, nodes, Lilith)
    6. Build response dict
    7. Save to cache + chart_archive
    8. Return JSON
```

### Derived charts

Secondary progressions, solar arc, solar return, and lunar return all:
1. Load the natal chart by `chart_id`
2. Calculate the derived data
3. Save as a `derived_charts` record
4. Return data + `derived_chart_id`

**Solar and lunar returns** use Newton's method to find the exact JD when the body returns to its natal longitude (converges in ≤50 iterations to ~0.36 arc-second precision).

### Apsides and lunations

**Apsides** — scan for `distance_speed` sign changes at coarse intervals, refine with bisection.

**Lunations** — scan for `sun_moon_angle mod 360` crossing target angles (0°, 90°, 180°, 270°), refine with Newton's method.

---

## 7. Database layer

### DatabaseManager

Raw SQL wrapper — no ORM. Instantiated once in `routes.py` via `create_database_manager(config)`, which reads `DB_TYPE` (`sqlite`, default, or `mysql`) and the matching connection settings.

Against SQLite it wraps Python's `sqlite3` directly, using `sqlite3.Row` as row factory so columns are accessible by name. Against MySQL it wraps `mysql-connector-python` behind `_MySQLConnectionWrapper`/`_MySQLCursorWrapper`, which translate the handful of SQLite idioms the query methods are written against (`?` placeholders, `INSERT OR IGNORE`/`OR REPLACE`, the `ON CONFLICT...DO UPDATE` upsert syntax, and `datetime('now')` comparisons) into their MySQL equivalents, and normalise MySQL's return types (`Decimal`, `datetime`) back to plain values — so every method below this layer is written once and shared by both backends.

Schema is initialised on startup by `_init_schema_sqlite()` or `_init_schema_mysql()`, both called from `init_database()`, using `CREATE TABLE IF NOT EXISTS`. The two are separate, hand-maintained schemas rather than one translated definition: MySQL requires an explicit key length on any indexed `TEXT`/`BLOB` column, so IDs, hashes, and tokens that are `TEXT PRIMARY KEY`/`UNIQUE` in SQLite become sized `VARCHAR` columns (`VARCHAR(36)` for UUIDs, `VARCHAR(32)` for MD5 hashes, `VARCHAR(255)` for general unique text) in the MySQL schema. MySQL support targets fresh deployments only — `_init_schema_mysql()` creates tables with their final column set directly rather than replaying the SQLite migration history below.

Migrations (SQLite only) run inline: `PRAGMA table_info` checks column presence, then `ALTER TABLE ADD COLUMN` adds missing columns.

**Special migration:** the `api_keys.key_type` column had a `NOT NULL CHECK (key_type IN ('domain','user'))` constraint in older databases with no default. `_init_schema_sqlite()` detects this and recreates the table (preserving all data) with the corrected constraint before adding new columns.

### Schema overview

**Core keys:**

| Table | Purpose |
|---|---|
| `api_keys` | All accounts. `key_enc` (Fernet), `key_prefix`, `password_hash`, `must_change_password`, `admin`, `active`, rate overrides, output config |
| `key_class_limits` | Single `'user'` row — default rate limits applied when key has no override |
| `api_key_services` | Federated service grants — see below |

**Authentication:**

| Table | Purpose |
|---|---|
| `email_verifications` | One-time tokens for email verification and password set/reset. `used` flag, `expires_at` |
| `login_2fa_codes` | Short-lived 6-digit codes for the 2FA login step. `used` flag, `expires_at` |
| `trusted_devices` | Long-lived device tokens that skip 2FA. `api_key_id` FK, `expires_at` |

**Charts:**

| Table | Purpose |
|---|---|
| `charts` | Cached natal/event charts. UUID PK, `chart_data` JSON |
| `derived_charts` | Progressions, returns, solar arc. FK → `charts` |
| `chart_archive` | Append-only permanent record (`INSERT OR IGNORE`) |
| `chart_recalculations` | Audit trail of recalculations with optional note |

**Views:**

| Table | Purpose |
|---|---|
| `views` | UUID-keyed opaque JSON blobs for sharing. `GET /views?v=UUID` is public |

**Location:**

| Table | Purpose |
|---|---|
| `canonical_places` | Deduplicated place records |
| `place_aliases` | Query strings → canonical place |
| `place_cache` | Geocoding + timezone data (30-day expiry) |
| `place_lookup_log` | Performance and usage logging |
| `locations` | Legacy simple geocode cache |

**Configuration:**

| Table | Purpose |
|---|---|
| `smtp_config` | Key/value SMTP settings. Loaded fresh on each email send |
| `portal_settings` | Key/value portal behaviour settings. Cached in PHP session |
| `email_templates` | Per-template styling and content overrides |

### Federated service access

ephemeral.rest can act as the shared identity provider for a cluster of
independent companion services, without any of this being specific to a
particular deployment — it's a general capability of the software, available
to anyone self-hosting it.

**The idea:** holding a valid API key is always sufficient to call
ephemeral.rest itself. The `api_key_services` table additionally lets a key
be granted access to any number of arbitrarily-named external services —
`grant_key_service(key_id, 'my-other-app')`, `key_has_service(key_id,
'my-other-app')`, and so on (see `database.py`, "Federated service access
grants"). Service names are free text chosen by whoever runs the companion
service; ephemeral.rest itself never references any specific service by
name and doesn't consult this table when authenticating its own requests.

**Intended pattern for a companion service:**

1. Run it under `DB_TYPE=mysql`, pointed at the same MySQL database as
   ephemeral.rest (see §4 of `SETUP.md`). A read-only MySQL user — `SELECT`
   only on `api_keys`, `api_key_services`, and `key_class_limits` — is
   recommended, since the companion service should never need to write to
   ephemeral.rest's own tables.
2. On each request, resolve the caller's `X-API-Key` the same way
   ephemeral.rest does (`key_prefix` lookup, then verify against `key_enc`
   — see §4, "Key verification"), then check `key_has_service(key_id,
   '<this service's own name>')` before proceeding.
3. Grants are managed from ephemeral.rest's side — via `key_manager.py` or
   the admin portal — the same place all other key administration already
   happens.

This keeps auth resolution local to each service (no per-request callback
to ephemeral.rest, no added network dependency) while keeping key issuance,
rate limits, and admin status centralised in one place. It's an entirely
optional feature — a deployment that only ever runs ephemeral.rest itself
can ignore `api_key_services` completely.

---

## 8. Output configuration system

Three-level merge applied at the start of every calculation:

```
Layer 1: OutputConfig.as_dict()      ← server-wide defaults
Layer 2: g.user['output']            ← per-key stored overrides
Layer 3: request_body.get('output')  ← per-request overrides
```

`api_keys.output_config` stores only the diffs from server defaults (sparse JSON). `NULL` means use all defaults.

Users can manage their own output config via `GET/POST /me/output` and the Output Config page in the portal. Admins can manage any key's config via `GET/POST /admin/keys/<id>/output`.

---

## 9. Location resolution

```
Query string
    → location_normaliser.py (lowercase, strip punctuation)
    → place_aliases (alias → canonical_place_id?)
    → canonical_places (normalised key match?)
    → Google Maps Geocoding API (if no hit)
        → save canonical_places + alias
    → place_cache (non-expired entry?)
    → Google Maps Timezone API (if cache miss)
        → save place_cache (expires 30 days)
    → return {lat, lon, timezone, utc_offset, dst_flag}
```

With `USE_GOOGLE=false`, Google calls are replaced by cities5000 lookups.

---

## 10. Email service

`EmailService` loads SMTP config fresh on each instantiation. Database values override environment variables. If `host`, `user`, or `password` are missing, `self.enabled = False` and sending is silently skipped.

### portal_url resolution

Email links to the portal (`verify.php`, `set-password.php`) use `self.portal_url`, resolved in this order:

1. `smtp_config` table `portal_url` field
2. `portal_settings` table `portal_url` field
3. `PORTAL_URL` environment variable
4. `self.base_url` fallback — **wrong for portal links** — logs a warning

Always configure `portal_url` via Settings → Portal Settings, or set `PORTAL_URL` in `.env`.

### Template system

Every email type (except raw 2FA codes) can be customised via the Email Templates admin page. Templates support `{variable}` substitution. The `_render_template_html()` method:
- Splits `body_text` into `<p>` blocks
- Auto-links bare `https://` URLs with `<a href>` tags
- Applies styling from the template's appearance fields (bg colour, panel colour, content width, etc.)

### Email types and their template names

| Template name | Sent when |
|---|---|
| `registration-verification` | User registers — contains `{verify_url}` |
| `set-password` | Email verified — contains `{set_password_url}` |
| `password-reset-required` | Admin forces reset or user requests forgot-password — contains `{set_password_url}` |
| `user-activated` | User sets password for first time — contains `{api_key}` |
| `2fa-code` | Login requires 2FA — contains `{code}`, `{expiry_minutes}` |
| `key-rotated` | Key rotated — contains `{api_key}` |
| `test` | SMTP test send |

### Adding a new email type

1. Add a `send_*` method to `EmailService` following the existing pattern — accept `template: dict = None`, use `_substitute()` and `_render_template_html()` for the template path, and provide hardcoded fallback HTML.
2. Add the template name and defaults to `_TEMPLATE_CONTENT_DEFAULTS` in `routes.py`.
3. Add the template definition (label, desc, vars, defaults) to the `$templates` array in `email-templates.php`.
4. Call the method from `routes.py` at the appropriate point, passing `template=_resolve_template('template-name')`.

---

## 11. Portal settings

Portal behaviour is configurable at runtime without touching files. Settings are stored in the `portal_settings` database table (key/value) with built-in defaults in `DatabaseManager.PORTAL_SETTINGS_DEFAULTS`.

| Setting | Default | Description |
|---|---|---|
| `site_name` | `ephemeralREST` | Displayed in browser title and sidebar |
| `site_version` | `1.0` | Shown in sidebar footer |
| `session_timeout` | `1800` | PHP session idle timeout in seconds |
| `trusted_device_days` | `28` | Trusted-device cookie lifetime |
| `allow_admin_promotion` | `true` | Whether admins can promote/demote other admins via portal |
| `logout_redirect_url` | `/login.php` | Where to redirect after sign-out |
| `portal_url` | `''` | Public URL of the portal (used in email links) |

The PHP portal reads these via `portal_settings_get()` (in `includes/api.php`), which caches the result in `$_SESSION['portal_settings']` for the duration of the session. After saving via the portal's Settings page, the cache is immediately busted with `unset($_SESSION['portal_settings'])`.

Endpoints: `GET /admin/portal-settings`, `POST /admin/portal-settings`, `DELETE /admin/portal-settings/<key>`.

`config.php` in the portal now contains **only two values**:
```php
define('API_BASE',  'https://api.yourdomain.com');
define('SITE_NAME', 'ephemeralREST');
```
Everything else is controlled via the portal settings UI.

---

## 12. Routes reference

Routes are organised in this order in `routes.py`:

| Section | Key routes |
|---|---|
| First-run setup | `GET /setup/status`, `POST /setup` |
| Registration | `POST /register`, `GET /register/verify` |
| Login / auth | `POST /login`, `POST /login/2fa`, `POST /password/forgot`, `POST /password/set` |
| Infrastructure | `GET /ping`, `GET /health`, `GET /cache/stats`, `POST /cache/cleanup` |
| Location | `GET /autocomplete`, `POST /locations/resolve` |
| Charts | `POST /calculate`, `GET /chart/<id>` |
| Derived charts | `/chart/<id>/progressions`, `/solar-arc`, `/solar-return`, `/lunar-return`, `/derived` |
| Ephemeris | `POST /apsides`, `/apsides/next`, `/lunations`, `/ephemeris`, `/eclipses` |
| Views | `POST /views`, `PUT /views/<uuid>`, `GET /views?v=<uuid>` |
| Archive | `GET /archive`, `GET /archive/<chart_id>` |
| Self-service | `GET /me`, `GET/POST /me/output`, `POST /me/rotate`, `POST /me/forget-device` |
| Admin — keys | `GET /admin/keys`, `GET/DELETE /admin/keys/<id>`, `/disable`, `/enable`, `/rotate`, `/limits`, `/output`, `/force-password-reset` |
| Admin — config | `GET/POST /admin/class-limits`, `GET/POST/DELETE /admin/smtp`, `POST /admin/smtp/test` |
| Admin — portal | `GET/POST /admin/portal-settings`, `DELETE /admin/portal-settings/<key>` |
| Admin — templates | `GET/POST /admin/email-templates/<name>`, `POST /admin/email-templates/<name>/reset` |

**Public endpoints** (no `X-API-Key` required): `/setup/status`, `/setup`, `/register`, `/register/verify`, `/login`, `/login/2fa`, `/password/forgot`, `/password/set`, `/ping`, `/autocomplete`, `/chart/<id>` (public for sharing), `GET /views?v=<uuid>`.

---

## 13. The admin portal (PHP)

### Authentication flow

```
1. Any page → auth_require() → checks $_SESSION['logged_in']
2. Not logged in → /login.php
3. Also checks /setup/status → redirects to setup.php if DB is empty
4. User submits email + password
5. auth_attempt_login() → POST /login
6. Response:
   - must_change_password → /set-password.php
   - 2fa_required → /2fa.php
   - logged_in (trusted device or admin+no SMTP) → portal
7. If 2FA required: user enters code → POST /login/2fa
8. On success: identity + decrypted API key stored in $_SESSION['user']
9. Admin → portal-admin.php, user → portal-user.php
```

`my_api_get()` / `my_api_post()` in `includes/auth.php` use `$_SESSION['user']['api_key']` for all subsequent calls.

`api_get()` / `api_post()` in `includes/api.php` can optionally authenticate with the session key (`$auth=true`, default) or make unauthenticated calls (`$auth=false`) for public endpoints.

### AJAX pattern

PHP files handle both page loads and AJAX requests distinguished by `X-Requested-With: XMLHttpRequest`:

```php
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_SERVER['HTTP_X_REQUESTED_WITH'])) {
    header('Content-Type: application/json');
    $input = json_decode(file_get_contents('php://input'), true) ?? [];
    // handle, echo json_encode([...])
    exit;
}
```

### Adding a new page

1. Create `my-page.php` following the pattern of an existing page (e.g. `class-limits.php`)
2. Start with: `require_once __DIR__ . '/config.php';` → `api.php` → `auth.php` → `auth_require('admin');`
3. Add to the appropriate nav array in `includes/header.php`:

```php
$admin_nav['my-page'] = ['label' => 'My Page', 'icon' => '◎', 'section' => null];
```

4. Add any new API endpoints to `routes.py` and the `_protected` list in `app.py`

---

## 14. Registration and key provisioning

### Self-serve registration flow

```
POST /register  (name + email)
    └─ Creates api_keys record (active=0, must_change_password=1)
       Creates email_verifications token
       Sends registration-verification email → {portal_url}/verify.php?t=TOKEN

User clicks link → verify.php?t=TOKEN (portal)
    └─ Portal calls GET /register/verify?t=TOKEN
       API activates account, issues set-password token
       Sends set-password email → {portal_url}/set-password.php?t=TOKEN
       Portal renders success page

User clicks link → set-password.php?t=TOKEN (portal)
    └─ User sets password → POST /password/set { token, new_password }
       API sets password_hash, clears must_change_password
       Sends user-activated email containing decrypted API key
       User can now log in
```

### Forgot password flow

```
POST /password/forgot  (email)
    └─ Generates token, sends password-reset-required email
       Generic response regardless of whether email is registered

User clicks reset link → set-password.php?t=TOKEN (portal)
    └─ Same set-password flow as above
       does NOT resend user-activated email (is_new_account = False)
```

### Admin-forced password reset

```
POST /admin/keys/<id>/force-password-reset
    └─ Sets must_change_password=1
       Deletes all trusted_devices for key
       Generates token, sends password-reset-required email
```

### Key rotation

```
POST /me/rotate  or  POST /admin/keys/<id>/rotate
    1. Generate new plaintext key
    2. Encrypt with KeyCrypto
    3. Update key_enc and key_prefix in api_keys
    4. Return plaintext in response (once only)
    5. Send key-rotated email if SMTP configured
```

### First-run admin setup

```
POST /setup  (name + email + password)
    └─ Only works when api_keys table is empty
       Creates admin account: active=1, admin=1, must_change_password=0
       Returns plaintext API key in response (shown once on setup.php)
       After this, /setup always returns 403
```

---

## 15. Key manager CLI

`key_manager.py` connects directly to the database for emergency administration without needing the API.

```bash
source .venv/bin/activate

# Create an admin key (prompts for password)
python3 key_manager.py create --identifier admin@example.com --name "Admin" --admin

# List all keys
python3 key_manager.py list

# Rotate a key
python3 key_manager.py rotate --identifier admin@example.com

# Set per-key rate limits
python3 key_manager.py set-limits --identifier user@example.com --per-minute 30

# Set class defaults
python3 key_manager.py class-limits --per-minute 10 --per-hour 100 --per-day 500
```

`create` now prompts for an optional password during key creation. If left blank, `must_change_password=1` is set and the user must set a password via the portal before they can log in.

---

## 16. Adding a new endpoint

### Step 1: Define the route in routes.py

```python
@api.route('/my-endpoint', methods=['POST'])
def my_endpoint():
    """Short description."""
    user = getattr(g, 'user', {})
    if not user.get('admin'):
        return _error('Admin access required', 403)

    data     = request.get_json(silent=True) or {}
    my_field = data.get('my_field', '').strip()
    if not my_field:
        return _error('my_field is required', 400)

    result = db_manager.some_method(my_field)
    return jsonify({'result': result})
```

### Step 2: Add to _protected in app.py (if authenticated)

```python
_protected = [
    # ...
    'api.my_endpoint',
]
```

Public endpoints (login, register, setup, etc.) must NOT be in `_protected`.

### Step 3: Add a validator if needed

```python
# validators.py
class MyEndpointSchema(Schema):
    my_field = fields.Str(required=True, validate=validate.Length(max=100))

# routes.py
@api.route('/my-endpoint', methods=['POST'])
@validate_request(MyEndpointSchema)
def my_endpoint(validated_data):
    my_field = validated_data['my_field']
```

### Step 4: Update the docs

Update `API_REFERENCE.md` with a description, field table, curl example, and response structure.

---

## 17. Common patterns and conventions

### Error returns

Always use `_error()`:
```python
return _error('chart_name is required', 400)
return _error('Chart not found', 404)
return _error('Admin access required', 403)
```

### Admin checks

```python
user = getattr(g, 'user', {})
if not user.get('admin'):
    return _error('Admin access required', 403)
```

### Public endpoint pattern

Public endpoints (no auth) simply omit the admin/user check. They must not be added to `_protected` in `app.py`.

### PHP: always escape output

```php
<?= htmlspecialchars($value) ?>
```

### PHP: my_api_* vs api_*

- `my_api_get()` / `my_api_post()` — use the session key. For all authenticated pages.
- `api_get()` / `api_post($url, $body, $auth=false)` — use `$auth=false` for public endpoints.

### PHP: portal_setting()

Read portal settings instead of PHP constants:
```php
$timeout = (int)portal_setting('session_timeout', 1800);
$days    = (int)portal_setting('trusted_device_days', 28);
```

---

## 18. Configuration reference

All values set in `.env`. The `Config` class in `config.py` exposes them.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | — | Fernet encryption key derivation. Never change on a live system |
| `DATABASE_PATH` | No | `ephemeral.db` | Path to SQLite database file |
| `SWISS_EPHEMERIS_PATH` | Yes | — | Path to directory containing `.se1` data files |
| `GOOGLE_MAPS_API_KEY` | If `USE_GOOGLE=true` | — | Google Maps API key |
| `USE_GOOGLE` | No | `true` | `false` = offline mode using cities5000 only |
| `FLASK_HOST` | No | `127.0.0.1` | Host to bind to |
| `FLASK_PORT` | No | `5000` | Port to bind to |
| `FLASK_DEBUG` | No | `false` | Never true in production |
| `RATE_LIMIT_ENABLED` | No | `true` | Enable rate limiting |
| `RATE_LIMIT_PER_MINUTE` | No | `30` | Global fallback rate limit |
| `RATE_LIMIT_PER_HOUR` | No | `300` | Global fallback rate limit |
| `RATE_LIMIT_PER_DAY` | No | `2000` | Global fallback rate limit |
| `CORS_ORIGINS` | No | `*` | Allowed CORS origins (comma-separated) |
| `CACHE_EXPIRY_DAYS` | No | `90` | Chart cache TTL |
| `TRUSTED_DEVICE_DAYS` | No | `28` | Trusted-device token lifetime (database value takes precedence) |
| `TWO_FACTOR_CODE_EXPIRY_MINUTES` | No | `10` | 2FA code validity window |
| `PORTAL_URL` | Recommended | — | Public URL of the admin portal. Used in email links. Set this or use portal settings. |
| `API_BASE_URL` | No | `http://localhost:5000` | Used in verification email links |

SMTP settings can be set via `.env` or via the admin portal's SMTP Settings page. Database values take precedence.

| SMTP Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | — | Mail server hostname |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_FROM` | SMTP_USER | From address |
| `SMTP_TLS` | `true` | Enable STARTTLS |
| `SMTP_SSL` | `false` | Use SSL (port 465) |
| `SMTP_ADMIN_EMAIL` | — | Receives admin notifications |