# Deploying ephemeralREST

This guide covers deploying the ephemeralREST API and ephemeralADMIN portal on a Linux server using nginx, Gunicorn, and PHP-FPM.

---

## Requirements

- Ubuntu 22.04 / Debian 12 (other distros work with minor adjustments)
- Python 3.10 or later
- PHP 8.2 or later with `php-fpm` and `php-curl`
- nginx
- A domain name pointed at your server
- Root or sudo access

---

## Licensing

ephemeralREST is released under the **GNU Affero General Public License v3 (AGPL v3)**.

This licence was chosen because the Swiss Ephemeris library is itself AGPL v3. Under the AGPL, running this as a network service means users who interact with it over the network are entitled to receive the source code. Publishing on a public repository such as GitHub satisfies this requirement.

---

## Docker deployment (alternative)

Steps 1–9 below cover a manual bare-metal install. If you'd rather run
everything in containers, `docker-compose.yml` (in the ephemeralREST repo)
sets up three services — the API, the admin portal, and an nginx front end
proxying both. This section covers the full path: MySQL already running on
the same VPS, real domains, and HTTPS via Let's Encrypt.

### Layout

```
some-parent-dir/
  ephemeralREST/    ← docker-compose.yml lives here; run `docker compose` from here
  ephemeralADMIN/   ← sibling directory; docker-compose.yml builds it via `../ephemeralADMIN`
```

### If MySQL is running directly on this VPS (not a separate managed database)

A container's "localhost" is itself, not the host machine — the API
container needs a specific route to reach MySQL on the host:

1. `docker-compose.yml` already sets `extra_hosts: host.docker.internal:host-gateway`
   on the `ephemeral-rest` service (Docker Engine 20.10+). Set
   `MYSQL_HOST=host.docker.internal` in `.env`.
2. MySQL needs to actually be listening somewhere the container can reach —
   its default `bind-address = 127.0.0.1` only accepts connections from the
   host itself. In `/etc/mysql/mysql.conf.d/mysqld.cnf`, change this to
   `bind-address = 0.0.0.0`, then `systemctl restart mysql`.
3. **This means MySQL is now listening on the VPS's public interface too —
   firewall port 3306 immediately**, allowing only local/loopback traffic:
   ```bash
   ufw deny 3306
   # or, if you use a VPS provider firewall/security group instead of ufw,
   # block 3306 there — the point is nothing outside this VPS should reach it.
   ```
4. The MySQL user needs to accept connections from the Docker bridge
   gateway, which isn't `'localhost'` from MySQL's point of view. Use `'%'`
   (any host) rather than `'localhost'` — safe here specifically *because*
   you just firewalled 3306 from the public internet in step 3:
   ```sql
   CREATE DATABASE ephemeral CHARACTER SET utf8mb4;
   CREATE USER 'ephemeral'@'%' IDENTIFIED BY 'your-mysql-password';
   GRANT ALL PRIVILEGES ON ephemeral.* TO 'ephemeral'@'%';
   FLUSH PRIVILEGES;
   ```

If MySQL is instead a separate managed database server, skip all of the
above — just set `MYSQL_HOST` to its hostname and make sure this VPS is
allowed to reach it (VPC/firewall rules on the database side).

### Real domains + HTTPS

1. Point DNS A records for both domains (e.g. `api.yourdomain.com`,
   `admin.yourdomain.com`) at this VPS before continuing — certbot's
   HTTP-01 challenge needs them resolving correctly.
2. Create the ACME challenge webroot nginx expects:
   ```bash
   mkdir -p /srv/ephemeral/certbot-webroot
   ```
3. Install certbot and obtain a certificate covering both domains as SANs
   (nginx isn't running yet at this point, so `--standalone` is simplest
   for this first issuance — it briefly binds port 80 itself):
   ```bash
   apt install certbot
   certbot certonly --standalone -d api.yourdomain.com -d admin.yourdomain.com
   ```
4. In `nginx.conf`, replace every occurrence of `api.yourdomain.com` and
   `admin.yourdomain.com` with your real domains (each appears twice).
5. Set up renewal — going forward, renewals use the webroot method (via
   the `/.well-known/acme-challenge/` location already in `nginx.conf`),
   so they don't need port 80 free or any downtime:
   ```bash
   certbot certonly --webroot -w /srv/ephemeral/certbot-webroot \
       -d api.yourdomain.com -d admin.yourdomain.com \
       --deploy-hook "docker compose -f /path/to/ephemeralREST/docker-compose.yml restart nginx"
   ```
   (certbot's own systemd timer / cron job, installed automatically, picks
   this up for future renewals since it re-reads the last-used method.)

### Bringing it up

```bash
cd ephemeralREST
cp .env.example .env    # or let the app write one on first run — see §4 below
nano .env                # SECRET_KEY, DB_TYPE=mysql + MYSQL_* values from above
docker compose up -d
```

Steps 3 (Swiss Ephemeris data — the `sweph/` directory still needs the
`.se1` files present before starting) and 6 (first-run admin setup) below
still apply. For step 6, run it inside the running container:

```bash
docker compose exec ephemeral-rest python3 key_manager.py create
```

The portal's own configuration (`API_BASE`) is set automatically via the
`ephemeral-admin` service's environment in `docker-compose.yml`, pointed at
the `ephemeral-rest` service by its Compose DNS name rather than
`localhost`.

The API and portal are deliberately separate images (see
`ephemeralADMIN/Dockerfile`) — different licenses, and the portal has no
need for `SECRET_KEY` or database access at all.

### Verifying

```bash
curl https://api.yourdomain.com/health
```

Then open `https://admin.yourdomain.com` in a browser and log in with the
admin key created above.

---

## 1. System preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx git curl \
                    php8.2-fpm php8.2-curl php8.2-mbstring

sudo useradd -m -s /bin/bash ephemeral
sudo mkdir -p /srv/ephemeral
sudo chown ephemeral:ephemeral /srv/ephemeral
```

---

## 2. Install the API

```bash
sudo -u ephemeral -s
cd /srv/ephemeral
git clone https://github.com/your-org/ephemeralREST.git app
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Swiss Ephemeris data files

```bash
mkdir -p /srv/ephemeral/app/sweph
# Copy your *.se1 ephemeris data files into this directory
```

---

## 4. Environment configuration

```bash
cp .env.example .env
nano .env
```

Minimum required:

```bash
SECRET_KEY=your-strong-random-secret-key
DATABASE_PATH=/srv/ephemeral/app/ephemeral.db
SWISS_EPHEMERIS_PATH=/srv/ephemeral/app/sweph
GOOGLE_MAPS_API_KEY=your-google-maps-api-key   # omit if USE_GOOGLE=false
PORTAL_URL=https://admin.yourdomain.com
CORS_ORIGINS=https://admin.yourdomain.com
```

Generate a secure `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> **`PORTAL_URL`** is essential. Verification emails, password-reset emails, and set-password emails all link to pages on the portal. Without this, email links point to the API (returning raw JSON) rather than the portal (rendering a page).

### Choosing a database: SQLite or MySQL

`DB_TYPE` selects the backend. It defaults to `sqlite` and needs no further
configuration — the file at `DATABASE_PATH` is created automatically. This
is the right choice for most single-server deployments.

Set `DB_TYPE=mysql` to run against MySQL (or MariaDB) instead. The database
itself must already exist — this app creates its own tables on first run
but will not create the database:

```bash
DB_TYPE=mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=ephemeral
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=ephemeral
```

```sql
-- Run once, before first start:
CREATE DATABASE ephemeral CHARACTER SET utf8mb4;
CREATE USER 'ephemeral'@'localhost' IDENTIFIED BY 'your-mysql-password';
GRANT ALL PRIVILEGES ON ephemeral.* TO 'ephemeral'@'localhost';
FLUSH PRIVILEGES;
```

When `DB_TYPE=mysql`, `DATABASE_PATH` is ignored and `mysql-connector-python`
(already in `requirements.txt`) is used to connect. MySQL 8.0+ or an
equivalent MariaDB release is required (for `CHECK` constraint support).

Already have data in an existing SQLite database? `migrate_sqlite_to_mysql.py`
copies it into a MySQL database in one pass, preserving primary keys and
foreign key relationships — see `python migrate_sqlite_to_mysql.py --help`.

#### Federated service access — read-only companion-service user

If other services you run will read this same MySQL database to check API
key validity and service grants (see `ARCHITECTURE.md`, "Federated service
access"), give them their own MySQL user with read-only access, rather than
sharing the read-write user above:

```sql
CREATE USER 'ephemeral_ro'@'%' IDENTIFIED BY 'a-different-password';
GRANT SELECT ON ephemeral.api_keys         TO 'ephemeral_ro'@'%';
GRANT SELECT ON ephemeral.api_key_services TO 'ephemeral_ro'@'%';
GRANT SELECT ON ephemeral.key_class_limits TO 'ephemeral_ro'@'%';
FLUSH PRIVILEGES;
```

Adjust `'%'` to the specific host(s) your companion services run on if
you'd rather not allow this user to connect from anywhere. Grants
themselves are managed from ephemeral.rest — see `key_manager.py`'s
`grant-service` / `revoke-service` / `set-services` / `list-grants`
commands.

---

## 5. Initialise the database

The database is created automatically on first start. Tables are created with `CREATE TABLE IF NOT EXISTS` — no separate migration step is required.

```bash
source .venv/bin/activate
python3 -c "from database import create_database_manager; create_database_manager(); print('Ready')"
```

(This reads `DB_TYPE` and the corresponding SQLite/MySQL settings from `.env`, same as the app itself.)

---

## 6. First-run admin setup

The first admin account is created through the portal's setup page — no CLI required. When the database is empty, the portal automatically redirects to `/setup.php`.

If you prefer the CLI:

```bash
source .venv/bin/activate
python3 key_manager.py create --identifier admin@example.com --name "Admin" --admin
# When prompted for a password, enter one — this lets the admin log in immediately
```

---

## 7. Gunicorn service

```bash
sudo nano /etc/systemd/system/ephemeral.service
```

```ini
[Unit]
Description=ephemeralREST API
After=network.target

[Service]
User=ephemeral
Group=ephemeral
WorkingDirectory=/srv/ephemeral/app
Environment="PATH=/srv/ephemeral/app/.venv/bin"
EnvironmentFile=/srv/ephemeral/app/.env
ExecStart=/srv/ephemeral/app/.venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5000 \
    --timeout 120 \
    --access-logfile /var/log/ephemeral/access.log \
    --error-logfile /var/log/ephemeral/error.log \
    "app:create_app()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/log/ephemeral
sudo chown ephemeral:ephemeral /var/log/ephemeral
sudo systemctl daemon-reload
sudo systemctl enable --now ephemeral
sudo systemctl status ephemeral
```

---

## 8. Install the portal

```bash
sudo mkdir -p /srv/ephemeral/admin
sudo cp -r /path/to/ephemeralADMIN/* /srv/ephemeral/admin/
sudo chown -R www-data:www-data /srv/ephemeral/admin
```

Edit `config.php` — **only two values needed**:

```php
define('API_BASE',  'https://api.yourdomain.com');
define('SITE_NAME', 'ephemeralREST');
```

Everything else (session timeout, trusted device days, portal URL, site version, etc.) is configured through the portal's Settings page after first login.

---

## 9. nginx configuration

### API

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    add_header X-Frame-Options        "SAMEORIGIN"  always;
    add_header X-Content-Type-Options "nosniff"     always;

    client_max_body_size 2m;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    access_log /var/log/nginx/ephemeral-api-access.log;
    error_log  /var/log/nginx/ephemeral-api-error.log;
}
```

### Admin portal

```nginx
server {
    listen 80;
    server_name admin.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name admin.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/admin.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/admin.yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    root  /srv/ephemeral/admin;
    index index.php;

    add_header X-Frame-Options        "SAMEORIGIN"  always;
    add_header X-Content-Type-Options "nosniff"     always;

    # Block direct access to includes
    location ~ ^/includes/ {
        deny all;
        return 404;
    }

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        include        snippets/fastcgi-php.conf;
        fastcgi_pass   unix:/run/php/php8.2-fpm.sock;
        fastcgi_param  SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include        fastcgi_params;
    }

    access_log /var/log/nginx/ephemeral-admin-access.log;
    error_log  /var/log/nginx/ephemeral-admin-error.log;
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/ephemeral-api   /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/ephemeral-admin /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## 10. TLS with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com -d admin.yourdomain.com
sudo certbot renew --dry-run
```

---

## 11. First login and post-setup

1. Navigate to `https://admin.yourdomain.com`
2. The portal detects the empty database and redirects to `/setup.php`
3. Enter your name, email address, and a password
4. The setup page shows your API key **once** — note it, though you will rarely need it directly
5. Sign in with your email and password
6. Go to **Settings → SMTP Settings** and configure your mail server
7. Go to **Settings → Portal Settings** and verify `Portal URL` is set to `https://admin.yourdomain.com`
8. Send a test email from the SMTP Settings page to confirm delivery

---

## 12. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 13. Maintenance

```bash
# Restart the API
sudo systemctl restart ephemeral

# View logs
sudo journalctl -u ephemeral -f
sudo tail -f /var/log/ephemeral/access.log

# Update
sudo -u ephemeral -s
cd /srv/ephemeral/app && git pull
source .venv/bin/activate && pip install -r requirements.txt
exit
sudo systemctl restart ephemeral
```

---

## Troubleshooting

**502 Bad Gateway** — Gunicorn not running: `sudo systemctl status ephemeral`

**Emails link to the API (raw JSON) instead of a portal page** — `PORTAL_URL` not set. Add it to `.env` or set it via Settings → Portal Settings.

**2FA code never arrives** — SMTP not configured. Admins bypass 2FA when SMTP is absent (intentional), but regular users cannot log in. Configure SMTP via the portal.

**Verification link says "invalid or expired"** — tokens expire after 24 hours and can only be used once. The user should re-register to receive a new link.

**"Call to undefined function auth_is_domain()"** — old portal files deployed. Replace all `.php` files with the current versions.

**`NOT NULL constraint failed: api_keys.key_type`** — existing database has old schema. The migration runs automatically on startup — ensure the latest `database.py` is deployed and restart the API.

**Google geocoding fails** — check `GOOGLE_MAPS_API_KEY` is set and the Geocoding and Time Zone APIs are enabled in Google Cloud Console.