# Deploying ephemeralREST on nginx

This guide covers deploying the ephemeralREST API on a Linux server using nginx as a reverse proxy and Gunicorn as the WSGI server.

---

## Requirements

- Ubuntu 22.04 or Debian 12 (other distros work with minor adjustments)
- Python 3.10 or later
- nginx
- A domain name pointed at your server
- Root or sudo access

---

## Licensing

ephemeralREST is released under the **GNU Affero General Public License v3 (AGPL v3)**.

This licence was chosen because the Swiss Ephemeris library — which powers all calculations — is itself licensed under the AGPL v3. Under the terms of the AGPL, any software that incorporates or links against an AGPL-licensed library must also be distributed under the AGPL v3.

The key practical obligation of the AGPL v3 (beyond standard GPL v3) is the **network use clause**: if you run this software as a network service, users who interact with it over the network are entitled to receive the complete corresponding source code of the running application. Publishing the source on a public repository such as GitHub satisfies this requirement.

**What this means for you as an operator:**

- You may run this software as a public or private service
- You must make the source code of your running instance publicly available
- Any modifications you make must also be released under the AGPL v3
- You must include a visible link to the source code (a footer link on the landing page is conventional)

**What this means if you want to use the Swiss Ephemeris commercially without AGPL obligations:**

Astrodienst AG offers a **Swiss Ephemeris Professional License** for a fee, which permits use in closed-source and proprietary applications. See [https://www.astro.com/swisseph/](https://www.astro.com/swisseph/) for details.

The full AGPL v3 licence text is in the `LICENSE` file at the root of the repository, and is also available at [https://www.gnu.org/licenses/agpl-3.0.html](https://www.gnu.org/licenses/agpl-3.0.html).

---

## 1. System preparation

Update the system and install dependencies.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx git curl
```

Create a dedicated user to run the service:

```bash
sudo useradd -m -s /bin/bash ephemeral
sudo mkdir -p /srv/ephemeral
sudo chown ephemeral:ephemeral /srv/ephemeral
```

---

## 2. Clone and install the application

```bash
sudo -u ephemeral -s
cd /srv/ephemeral
git clone https://github.com/gmelh/ephemeralREST.git app
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Swiss Ephemeris data files

The Swiss Ephemeris data files must be present for calculations. Place them in a directory accessible to the application.

```bash
mkdir -p /srv/ephemeral/app/sweph
# Copy your ephemeris data files (*.se1, *.eph) into this directory
```

The ephemeris path is configured in `.env` (see section 4).

---

## 4. Environment configuration

Copy the example environment file and edit it:

```bash
cp .env.example .env
nano .env
```

Minimum required configuration:

```
# Flask
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=false
SECRET_KEY=your-strong-random-secret-key-here

# Database
DATABASE_PATH=/srv/ephemeral/app/ephemeral.db

# Swiss Ephemeris
SWISS_EPHEMERIS_PATH=/srv/ephemeral/app/sweph

# Google Maps (for location geocoding)
GOOGLE_MAPS_API_KEY=your-google-maps-api-key

# Rate limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=30
RATE_LIMIT_PER_HOUR=300
RATE_LIMIT_PER_DAY=2000

# CORS — set to your frontend domain(s)
CORS_ORIGINS=https://yourdomain.com
```

Generate a secure `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 5. Initialise the database

Run the application once to create the database and tables:

```bash
source .venv/bin/activate
python3 app.py &
sleep 2
kill %1
```

Or initialise it directly:

```bash
python3 -c "from database import DatabaseManager; db = DatabaseManager('ephemeral.db'); print('Database ready')"
```

---

## 6. Create an admin key

Before the API can be used, at least one admin key must exist:

```bash
source .venv/bin/activate
python3 key_manager.py create --type domain --identifier admin.local --name "Admin" --admin
```

Save the key that is printed — it will not be shown again.

---

## 7. Gunicorn service

Create a systemd service file for Gunicorn:

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

Create the log directory and start the service:

```bash
sudo mkdir -p /var/log/ephemeral
sudo chown ephemeral:ephemeral /var/log/ephemeral
sudo systemctl daemon-reload
sudo systemctl enable ephemeral
sudo systemctl start ephemeral
sudo systemctl status ephemeral
```

---

## 8. nginx configuration

### API server block

```bash
sudo nano /etc/nginx/sites-available/ephemeral-api
```

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    # Redirect HTTP to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Security headers
    add_header X-Frame-Options        "SAMEORIGIN"  always;
    add_header X-Content-Type-Options "nosniff"     always;
    add_header X-XSS-Protection       "1; mode=block" always;
    add_header Referrer-Policy        "strict-origin-when-cross-origin" always;

    # Request size limit (increase if needed for large chart batches)
    client_max_body_size 2m;

    # Proxy to Gunicorn
    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # Health check endpoint — no auth required, cache for 10s
    location /health {
        proxy_pass         http://127.0.0.1:5000/health;
        proxy_set_header   Host $host;
        proxy_cache_valid  200 10s;
    }

    access_log /var/log/nginx/ephemeral-api-access.log;
    error_log  /var/log/nginx/ephemeral-api-error.log;
}
```

### Admin portal server block (PHP)

```bash
sudo nano /etc/nginx/sites-available/ephemeral-admin
```

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
    index landing.php index.php;

    # Security headers
    add_header X-Frame-Options        "SAMEORIGIN"  always;
    add_header X-Content-Type-Options "nosniff"     always;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        include        snippets/fastcgi-php.conf;
        fastcgi_pass   unix:/run/php/php8.2-fpm.sock;
        fastcgi_param  SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include        fastcgi_params;
    }

    # Block direct access to includes directory
    location ~ ^/includes/ {
        deny all;
        return 404;
    }

    access_log /var/log/nginx/ephemeral-admin-access.log;
    error_log  /var/log/nginx/ephemeral-admin-error.log;
}
```

Enable the sites:

```bash
sudo ln -s /etc/nginx/sites-available/ephemeral-api   /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/ephemeral-admin /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9. TLS certificates with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com -d admin.yourdomain.com
```

Certbot will handle automatic renewal. Verify:

```bash
sudo certbot renew --dry-run
```

---

## 10. PHP for the admin portal

```bash
sudo apt install -y php8.2-fpm php8.2-curl php8.2-mbstring
sudo systemctl enable php8.2-fpm
sudo systemctl start php8.2-fpm
```

Copy the admin portal files:

```bash
sudo mkdir -p /srv/ephemeral/admin
sudo cp -r /srv/ephemeral/app/admin/* /srv/ephemeral/admin/
sudo chown -R www-data:www-data /srv/ephemeral/admin
```

Edit `config.php` to point at the API:

```php
define('API_BASE',      'https://api.yourdomain.com');
define('ADMIN_API_KEY', 'your-admin-key-here');
define('SITE_NAME',     'ephemeralREST');
```

---

## 11. Configure SMTP

Once the admin portal is running, log in with your admin key and navigate to **SMTP Settings**. Enter your mail server credentials and send a test email to confirm delivery.

Alternatively, set SMTP environment variables in `.env` before starting the service:

```
SMTP_HOST=smtp.yourmailprovider.com
SMTP_PORT=587
SMTP_USER=no-reply@yourdomain.com
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=ephemeralREST <no-reply@yourdomain.com>
SMTP_TLS=true
ADMIN_EMAIL=admin@yourdomain.com
API_BASE_URL=https://api.yourdomain.com
PORTAL_URL=https://admin.yourdomain.com
```

> **`PORTAL_URL`** — the public URL of the ephemeralADMIN portal. Used in user verification emails to construct the link `{PORTAL_URL}/verify.php?t={token}`. If not set, verification links point to the API instead, which returns raw JSON rather than a user-friendly page. This variable must be set for user key self-registration to work correctly end-to-end.

SMTP settings configured via environment variables can be overridden at any time through the admin portal's SMTP Settings page. Database values take precedence over environment variables.
```

---

## 12. Firewall

Allow only HTTP, HTTPS, and SSH:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

---

## 13. Log rotation

Create a logrotate configuration:

```bash
sudo nano /etc/logrotate.d/ephemeral
```

```
/var/log/ephemeral/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        systemctl reload ephemeral > /dev/null 2>&1 || true
    endscript
}
```

---

## 14. Verify the deployment

```bash
# API health check
curl https://api.yourdomain.com/health

# Test an authenticated endpoint
curl -X POST https://api.yourdomain.com/calculate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"chart_name":"Test","datetime":"1985-06-12 14:30:00","location":"London"}'
```

---

## Maintenance

### Restart the API

```bash
sudo systemctl restart ephemeral
```

### View logs

```bash
# Application logs
sudo journalctl -u ephemeral -f

# Gunicorn access log
sudo tail -f /var/log/ephemeral/access.log

# nginx logs
sudo tail -f /var/log/nginx/ephemeral-api-access.log
```

### Update the application

```bash
sudo -u ephemeral -s
cd /srv/ephemeral/app
git pull
source .venv/bin/activate
pip install -r requirements.txt
exit
sudo systemctl restart ephemeral
```

### Manage API keys

```bash
sudo -u ephemeral -s
cd /srv/ephemeral/app
source .venv/bin/activate
python3 key_manager.py list
python3 key_manager.py create
```

---

## Gunicorn worker sizing

| Server RAM | Recommended workers |
|---|---|
| 1 GB | 2 |
| 2 GB | 4 |
| 4 GB | 8 |
| 8 GB+ | 12–16 |

Each Swiss Ephemeris calculation is CPU-bound. Using more workers than CPU cores provides diminishing returns for calculation-heavy workloads.

---

## Troubleshooting

**API returns 502 Bad Gateway**
Gunicorn is not running or is unreachable. Check `sudo systemctl status ephemeral` and `sudo journalctl -u ephemeral -n 50`.

**Calculations return errors about ephemeris files**
The `SWISS_EPHEMERIS_PATH` in `.env` points to a directory that doesn't contain the required `.se1` data files. Verify the path and file presence.

**Email not sending**
Check the SMTP settings via the admin portal. Confirm the credentials with your mail provider and verify port 587 is not blocked by your server firewall or hosting provider.

**Database errors on startup**
Ensure `DATABASE_PATH` in `.env` points to a directory that the `ephemeral` user has write access to.

**Google geocoding fails**
The `GOOGLE_MAPS_API_KEY` may be missing, expired, or the Maps Geocoding and Time Zone APIs may not be enabled in the Google Cloud Console.