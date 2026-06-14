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

---

## 5. Initialise the database

The database is created automatically on first start. Tables are created with `CREATE TABLE IF NOT EXISTS` — no separate migration step is required.

```bash
source .venv/bin/activate
python3 -c "from database import DatabaseManager; DatabaseManager('ephemeral.db'); print('Ready')"
```

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
