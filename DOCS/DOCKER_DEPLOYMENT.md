# Ephemeral REST — Docker Deployment Guide

**Covers:** ephemeralREST · ephemeralADMIN · nginx reverse proxy · SSL/TLS via Let's Encrypt

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Server Preparation](#2-server-preparation)
3. [Project Structure](#3-project-structure)
4. [Clone the Repositories](#4-clone-the-repositories)
5. [Add the Docker Files](#5-add-the-docker-files)
6. [Swiss Ephemeris Data Files](#6-swiss-ephemeris-data-files)
7. [Environment Configuration](#7-environment-configuration)
8. [First Run — HTTP Only](#8-first-run--http-only)
9. [SSL — Obtaining a Certificate](#9-ssl--obtaining-a-certificate)
10. [SSL — Nginx Configuration](#10-ssl--nginx-configuration)
11. [SSL — Automatic Renewal](#11-ssl--automatic-renewal)
12. [Firewall and Port Security](#12-firewall-and-port-security)
13. [Going Live](#13-going-live)
14. [Ongoing Maintenance](#14-ongoing-maintenance)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Prerequisites

### What you need

- A Linux VPS or cloud instance running Ubuntu 22.04 LTS (recommended) or Debian 12
- A domain name with DNS you control — you need two records:
  - `api.your-domain.com` → your server IP (for the REST API)
  - `admin.your-domain.com` → your server IP (for the admin portal)
- SSH access to the server with sudo privileges
- A Google Maps API key if you want geocoding (place name → lat/lng resolution)

### What gets installed on the server

- Docker Engine
- Docker Compose plugin
- Certbot (for Let's Encrypt SSL certificates)

Nothing else needs to be installed directly on the host. Python, PHP, gunicorn, Apache, and all application dependencies run inside containers.

---

## 2. Server Preparation

### 2.1 Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

### 2.2 Install Docker Engine

Docker provides an official installation script. Do not use the version in Ubuntu's default repositories — it is typically outdated.

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Add your user to the `docker` group so you can run Docker commands without `sudo`:

```bash
sudo usermod -aG docker $USER
```

Log out and back in for the group change to take effect, then verify:

```bash
docker --version
docker compose version
```

Both commands should return version numbers without errors.

### 2.3 Install Certbot

```bash
sudo apt install -y certbot
```

Certbot is the official Let's Encrypt client. It runs on the host (not in a container) so it can bind to port 80 temporarily during the certificate challenge.

### 2.4 Install curl and git

```bash
sudo apt install -y curl git
```

---

## 3. Project Structure

The entire stack lives inside a single root directory. The final layout looks like this — refer back to it as you work through the steps:

```
ephemeral/
├── .env                              ← your secrets (never commit to git)
├── .env.example                      ← template
├── docker-compose.yml
├── nginx/
│   ├── nginx.conf                    ← active nginx config (HTTP or HTTPS)
│   └── ssl/
│       ├── cert.pem                  ← symlink → Let's Encrypt live cert
│       └── key.pem                   ← symlink → Let's Encrypt private key
├── ephemeralREST/                    ← cloned from GitHub
│   ├── Dockerfile                    ← already in repo
│   ├── sweph/
│   │   ├── sepl_18.se1
│   │   ├── semo_18.se1
│   │   └── seas_18.se1
│   └── ... (all other Python files)
└── ephemeralADMIN/                   ← cloned from GitHub
    ├── Dockerfile                    ← you add this
    ├── docker/
    │   └── config.php                ← you add this
    └── ... (all other PHP files)
```

Create the root directory:

```bash
mkdir ~/ephemeral && cd ~/ephemeral
```

All subsequent commands assume you are inside `~/ephemeral/` unless stated otherwise.

---

## 4. Clone the Repositories

```bash
git clone https://github.com/gmelh/ephemeralREST
git clone https://github.com/gmelh/ephemeralADMIN
```

Also create the required directories:

```bash
mkdir -p nginx/ssl
mkdir -p ephemeralREST/sweph
mkdir -p ephemeralADMIN/docker
```

---

## 5. Add the Docker Files

The files in this section do not exist in either repository. Create each one exactly as shown.

### 5.1 `docker-compose.yml`

Create `~/ephemeral/docker-compose.yml`:

```yaml
version: '3.8'

services:

  api:
    build:
      context: ./ephemeralREST
    container_name: ephemeral-api
    restart: unless-stopped
    expose:
      - "5000"
    environment:
      - FLASK_HOST=0.0.0.0
      - FLASK_PORT=5000
      - FLASK_DEBUG=${FLASK_DEBUG:-False}
      - SECRET_KEY=${SECRET_KEY}
      - GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY}
      - API_KEY=${API_KEY}
      - DATABASE_PATH=/app/data/ephemeral.db
      - SWISS_EPHEMERIS_PATH=/app/sweph
      - LOG_FILE=/app/logs/google_api_usage.log
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
      - USAGE_COUNT_FILE=/app/data/api_usage_count.json
      - MAX_MONTHLY_REQUESTS=${MAX_MONTHLY_REQUESTS:-10000}
      - CORS_ORIGINS=${CORS_ORIGINS:-*}
      - RATE_LIMIT_ENABLED=${RATE_LIMIT_ENABLED:-True}
      - RATE_LIMIT_PER_MINUTE=${RATE_LIMIT_PER_MINUTE:-10}
      - RATE_LIMIT_PER_HOUR=${RATE_LIMIT_PER_HOUR:-50}
      - RATE_LIMIT_PER_DAY=${RATE_LIMIT_PER_DAY:-200}
      - CACHE_EXPIRY_DAYS=${CACHE_EXPIRY_DAYS:-90}
      - SMTP_HOST=${SMTP_HOST:-}
      - SMTP_PORT=${SMTP_PORT:-587}
      - SMTP_USER=${SMTP_USER:-}
      - SMTP_PASS=${SMTP_PASS:-}
      - SMTP_FROM=${SMTP_FROM:-}
    volumes:
      - ./ephemeralREST/sweph:/app/sweph:ro
      - ephemeral-data:/app/data
      - ephemeral-logs:/app/logs
    networks:
      - ephemeral-net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  admin:
    build:
      context: ./ephemeralADMIN
    container_name: ephemeral-admin
    restart: unless-stopped
    expose:
      - "80"
    environment:
      - API_BASE=http://api:5000
      - ADMIN_API_KEY=${API_KEY}
    depends_on:
      api:
        condition: service_healthy
    networks:
      - ephemeral-net

  nginx:
    image: nginx:alpine
    container_name: ephemeral-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - api
      - admin
    networks:
      - ephemeral-net

volumes:
  ephemeral-data:
    driver: local
  ephemeral-logs:
    driver: local

networks:
  ephemeral-net:
    driver: bridge
```

### 5.2 `nginx/nginx.conf` — HTTP version (used before SSL)

Create `~/ephemeral/nginx/nginx.conf`. This is the initial HTTP-only config used to obtain your first SSL certificate. You will replace it with the HTTPS version in Section 10.

```nginx
user  nginx;
worker_processes  auto;
error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 10M;

    upstream api   { server api:5000  fail_timeout=30s max_fails=3; }
    upstream admin { server admin:80  fail_timeout=30s max_fails=3; }

    # REST API — HTTP only (temporary, before SSL)
    server {
        listen 80;
        server_name api.your-domain.com;

        location /health {
            proxy_pass http://api;
            proxy_set_header Host $host;
            access_log off;
        }

        location / {
            proxy_pass http://api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 120s;
        }
    }

    # Admin portal — HTTP only (temporary, before SSL)
    server {
        listen 80;
        server_name admin.your-domain.com;

        location / {
            proxy_pass http://admin;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 60s;
        }
    }
}
```

Replace `api.your-domain.com` and `admin.your-domain.com` with your actual domain names throughout.

### 5.3 `ephemeralADMIN/Dockerfile`

Create `~/ephemeral/ephemeralADMIN/Dockerfile`:

```dockerfile
FROM php:8.2-apache

RUN a2enmod rewrite

RUN docker-php-ext-install pdo pdo_mysql \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN printf '<Directory /var/www/html>\n\
    AllowOverride All\n\
    Require all granted\n\
</Directory>\n' > /etc/apache2/conf-available/app.conf \
    && a2enconf app

WORKDIR /var/www/html

COPY . .

# Override config.php with the Docker-aware version (reads env vars)
COPY docker/config.php /var/www/html/config.php

RUN chown -R www-data:www-data /var/www/html

EXPOSE 80
```

### 5.4 `ephemeralADMIN/docker/config.php`

The repository's `config.php` hardcodes `http://localhost:5000` which does not resolve between Docker containers. This file replaces it at build time:

Create `~/ephemeral/ephemeralADMIN/docker/config.php`:

```php
<?php
/**
 * Docker-aware configuration for ephemeralADMIN.
 * Copied over config.php at build time by the Dockerfile.
 * All values are read from environment variables set in docker-compose.yml.
 */

define('API_BASE',      getenv('API_BASE')      ?: 'http://api:5000');
define('ADMIN_API_KEY', getenv('ADMIN_API_KEY') ?: 'change-me');
define('SITE_NAME',     'ephemeralREST');
define('SITE_VERSION',  '1.0');
define('SESSION_TIMEOUT', 1800);

date_default_timezone_set('UTC');
```

---

## 6. Swiss Ephemeris Data Files

ephemeralREST uses the Swiss Ephemeris library to perform planetary calculations. The library requires binary data files (`.se1`) which are not included in the repository.

Download them from Astrodienst's FTP server. For dates spanning roughly 1800–2400 CE, you need three files per epoch:

```bash
cd ~/ephemeral/ephemeralREST/sweph

# Main planets
curl -O https://www.astro.com/ftp/sweph/ephe/sepl_18.se1

# Moon
curl -O https://www.astro.com/ftp/sweph/ephe/semo_18.se1

# Asteroids / extra bodies
curl -O https://www.astro.com/ftp/sweph/ephe/seas_18.se1

cd ~/ephemeral
```

The `18` in the filename refers to the epoch (1800–2400 CE). If your application needs dates outside that range, download additional epoch files from the same FTP directory. The `sweph/` directory is mounted read-only into the container, so you can add files at any time without rebuilding.

---

## 7. Environment Configuration

Create your `.env` file from the template:

```bash
cp .env.example .env
```

Then open it and fill in values:

```bash
nano .env
```

### Generating secure random values

```bash
# Generate SECRET_KEY (Flask session signing):
python3 -c "import secrets; print(secrets.token_hex(32))"

# Generate API_KEY (master API key):
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Minimum required values

```dotenv
# Flask security — generate with command above
SECRET_KEY=your-generated-hex-string

# Master API key — used by both the REST service and the admin portal
API_KEY=your-generated-urlsafe-string

# Google Maps — required for geocoding (place name → coordinates)
GOOGLE_MAPS_API_KEY=AIzaSy...

# CORS — restrict to your actual API consumers in production
CORS_ORIGINS=https://your-app-domain.com

# Keep debug off in production
FLASK_DEBUG=False
```

### SMTP (optional)

If ephemeralREST sends email notifications, also set:

```dotenv
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASS=your-smtp-password
SMTP_FROM=noreply@your-domain.com
```

---

## 8. First Run — HTTP Only

Before setting up SSL, start the stack on HTTP to confirm everything is wired correctly. The certificate authority needs to reach your server on port 80 to issue a certificate, so the stack must be running.

```bash
docker compose build
docker compose up -d
```

Watch the startup logs:

```bash
docker compose logs -f
```

The `api` service takes 30–40 seconds to pass its health check before the `admin` service starts. Once `ephemeral-admin` appears in the logs, everything is running.

### Verify HTTP is working

```bash
# API health check
curl http://api.your-domain.com/health

# Expected response:
# {"status": "healthy", ...}
```

Open `http://admin.your-domain.com` in a browser. You should see the ephemeralADMIN login page.

If either check fails, review the logs before proceeding to SSL:

```bash
docker compose logs api
docker compose logs admin
docker compose logs nginx
```

---

## 9. SSL — Obtaining a Certificate

Let's Encrypt certificates are free and automatically trusted by all major browsers. Certbot handles the issuance and can later handle automatic renewal.

### 9.1 Stop nginx temporarily

Certbot needs to bind to port 80 directly to complete the domain verification challenge. Stop the nginx container first:

```bash
docker compose stop nginx
```

### 9.2 Request the certificate

Run Certbot in standalone mode. Replace the domain names with your actual domains:

```bash
sudo certbot certonly \
  --standalone \
  --preferred-challenges http \
  -d api.your-domain.com \
  -d admin.your-domain.com \
  --email you@your-domain.com \
  --agree-tos \
  --non-interactive
```

On success, Certbot saves the certificates to `/etc/letsencrypt/live/api.your-domain.com/`.

### 9.3 Create symlinks for nginx

The nginx container mounts `./nginx/ssl/` and expects files at `/etc/nginx/ssl/cert.pem` and `/etc/nginx/ssl/key.pem`. Create symlinks from that directory to Let's Encrypt's managed paths:

```bash
# Remove any placeholder files
rm -f nginx/ssl/cert.pem nginx/ssl/key.pem

# Create symlinks pointing to the live certificate
sudo ln -s /etc/letsencrypt/live/api.your-domain.com/fullchain.pem \
           ~/ephemeral/nginx/ssl/cert.pem

sudo ln -s /etc/letsencrypt/live/api.your-domain.com/privkey.pem \
           ~/ephemeral/nginx/ssl/key.pem
```

### 9.4 Verify the symlinks are readable by Docker

The nginx container runs as the `nginx` user. Check that the certificate files are world-readable:

```bash
sudo chmod 644 /etc/letsencrypt/live/api.your-domain.com/fullchain.pem
sudo chmod 640 /etc/letsencrypt/live/api.your-domain.com/privkey.pem
sudo chmod 755 /etc/letsencrypt/live/ /etc/letsencrypt/archive/
sudo chmod 755 /etc/letsencrypt/live/api.your-domain.com/
sudo chmod 755 /etc/letsencrypt/archive/api.your-domain.com/
```

---

## 10. SSL — Nginx Configuration

Replace the HTTP-only `nginx/nginx.conf` with the full HTTPS configuration below. This version:

- Redirects all HTTP traffic to HTTPS
- Serves the REST API over HTTPS on port 443 (via `api.your-domain.com`)
- Serves the Admin portal over HTTPS on port 443 (via `admin.your-domain.com`)
- Applies security headers and modern TLS settings

Replace `api.your-domain.com` and `admin.your-domain.com` throughout:

```nginx
user  nginx;
worker_processes  auto;
error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
    use epoll;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" rt=$request_time';

    access_log  /var/log/nginx/access.log  main;

    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    client_max_body_size  10M;

    gzip on;
    gzip_vary on;
    gzip_min_length 1000;
    gzip_comp_level 6;
    gzip_types text/plain text/css application/json application/javascript;

    # ── Rate limiting ──────────────────────────────────────────────────────────
    limit_req_zone $binary_remote_addr zone=api_limit:10m  rate=10r/s;
    limit_req_zone $binary_remote_addr zone=calc_limit:10m rate=5r/s;

    # ── Upstreams (Docker internal DNS) ───────────────────────────────────────
    upstream api   { server api:5000  fail_timeout=30s max_fails=3; }
    upstream admin { server admin:80  fail_timeout=30s max_fails=3; }

    # ── Shared SSL settings ───────────────────────────────────────────────────
    ssl_certificate     /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # OCSP stapling (speeds up TLS handshakes)
    ssl_stapling        on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 1.1.1.1 valid=300s;
    resolver_timeout 5s;

    # ── HTTP → HTTPS redirect (catches both domains) ──────────────────────────
    server {
        listen 80;
        server_name api.your-domain.com admin.your-domain.com;
        return 301 https://$host$request_uri;
    }

    # ── REST API — HTTPS ───────────────────────────────────────────────────────
    server {
        listen 443 ssl http2;
        server_name api.your-domain.com;

        # Security headers
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
        add_header X-Content-Type-Options    "nosniff" always;
        add_header X-Frame-Options           "DENY" always;
        add_header X-XSS-Protection          "1; mode=block" always;
        add_header Referrer-Policy           "strict-origin-when-cross-origin" always;

        # Health check — no rate limit, no access log
        location /health {
            proxy_pass       http://api;
            proxy_set_header Host $host;
            access_log       off;
        }

        # Calculation endpoint — stricter rate limit
        location /calculate {
            limit_req zone=calc_limit burst=10 nodelay;

            proxy_pass             http://api;
            proxy_http_version     1.1;
            proxy_set_header       Host $host;
            proxy_set_header       X-Real-IP $remote_addr;
            proxy_set_header       X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header       X-Forwarded-Proto $scheme;
            proxy_read_timeout     120s;
            proxy_connect_timeout  30s;
        }

        # All other API routes
        location / {
            limit_req zone=api_limit burst=20 nodelay;

            proxy_pass             http://api;
            proxy_http_version     1.1;
            proxy_set_header       Upgrade $http_upgrade;
            proxy_set_header       Connection 'upgrade';
            proxy_set_header       Host $host;
            proxy_set_header       X-Real-IP $remote_addr;
            proxy_set_header       X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header       X-Forwarded-Proto $scheme;
            proxy_cache_bypass     $http_upgrade;
            proxy_read_timeout     120s;
            proxy_connect_timeout  30s;
        }
    }

    # ── Admin portal — HTTPS ───────────────────────────────────────────────────
    server {
        listen 443 ssl http2;
        server_name admin.your-domain.com;

        # Security headers (admin is not an API — frame restriction relaxed to SAMEORIGIN)
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
        add_header X-Content-Type-Options    "nosniff" always;
        add_header X-Frame-Options           "SAMEORIGIN" always;
        add_header X-XSS-Protection          "1; mode=block" always;
        add_header Referrer-Policy           "strict-origin-when-cross-origin" always;

        location / {
            proxy_pass             http://admin;
            proxy_http_version     1.1;
            proxy_set_header       Host $host;
            proxy_set_header       X-Real-IP $remote_addr;
            proxy_set_header       X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header       X-Forwarded-Proto $scheme;
            proxy_read_timeout     60s;
            proxy_connect_timeout  10s;
        }
    }
}
```

Start nginx with the new config:

```bash
docker compose start nginx
```

### Test SSL

```bash
# API
curl -I https://api.your-domain.com/health

# Check the certificate details
echo | openssl s_client -connect api.your-domain.com:443 2>/dev/null | openssl x509 -noout -dates
```

The `curl` response should show `HTTP/2 200` and the `openssl` output should show a valid expiry date 90 days in the future.

---

## 11. SSL — Automatic Renewal

Let's Encrypt certificates expire after 90 days. Certbot handles renewal, but you need to ensure nginx reloads after each renewal so it picks up the new certificate.

### 11.1 Create a renewal hook

Certbot supports deploy hooks — scripts that run after a successful renewal. Create one that restarts nginx:

```bash
sudo nano /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

Paste the following. Replace `/home/your-user/ephemeral` with the actual path to your project:

```bash
#!/bin/bash
# Reload nginx after Let's Encrypt certificate renewal
cd /home/your-user/ephemeral
docker compose exec -T nginx nginx -s reload
```

Make it executable:

```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

### 11.2 Test the renewal process

```bash
sudo certbot renew --dry-run
```

A dry run simulates the full renewal process without actually replacing the certificate. If it completes without errors, automatic renewal is correctly configured.

### 11.3 Verify the renewal timer

Certbot installs a systemd timer that runs the renewal check twice daily:

```bash
sudo systemctl status certbot.timer
```

You should see `Active: active (waiting)`. No further configuration is needed — the timer is enabled by default.

---

## 12. Firewall and Port Security

### 12.1 Configure ufw

```bash
# Allow SSH (essential — do this before enabling ufw)
sudo ufw allow OpenSSH

# Allow HTTP and HTTPS (public-facing)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable the firewall
sudo ufw enable

# Confirm the rules
sudo ufw status
```

### 12.2 Restrict the admin portal

The admin portal is behind `admin.your-domain.com` on port 443. If you want to restrict it to specific IP addresses only, use nginx's `allow`/`deny` directives inside the admin server block:

```nginx
server {
    listen 443 ssl http2;
    server_name admin.your-domain.com;

    # Allow only your IP(s) — replace with your actual IP
    allow 203.0.113.10;
    allow 203.0.113.20;
    deny all;

    location / {
        # ...
    }
}
```

Reload nginx after making this change:

```bash
docker compose exec nginx nginx -s reload
```

### 12.3 Do not expose container ports directly

The `docker-compose.yml` only exposes ports `80` and `443` on the host, via nginx. The `api` and `admin` services use `expose:` (internal only) rather than `ports:`, so they are not directly reachable from the internet. This is the intended design — all traffic passes through nginx.

---

## 13. Going Live

Once HTTP verification passed and HTTPS is working, run through this checklist before considering the deployment production-ready:

- [ ] `FLASK_DEBUG=False` in `.env`
- [ ] `CORS_ORIGINS` set to actual consuming domains (not `*`)
- [ ] `SECRET_KEY` is a randomly generated 64-character hex string
- [ ] `API_KEY` is a randomly generated urlsafe string
- [ ] `GOOGLE_MAPS_API_KEY` is restricted to your server's IP in the Google Cloud Console
- [ ] Both domains load over HTTPS with no browser certificate warnings
- [ ] `curl https://api.your-domain.com/health` returns 200
- [ ] `curl http://api.your-domain.com/health` returns 301 redirect
- [ ] Admin portal is accessible at `https://admin.your-domain.com`
- [ ] Certbot dry-run completes without errors
- [ ] ufw is enabled with only ports 22, 80, 443 open
- [ ] `.env` is not committed to any git repository (check `.gitignore`)
- [ ] A database backup strategy is in place (see Maintenance)

---

## 14. Ongoing Maintenance

### Pulling code updates

```bash
cd ~/ephemeral

# Pull the latest code for each repo
cd ephemeralREST && git pull && cd ..
cd ephemeralADMIN && git pull && cd ..

# Rebuild the affected images
docker compose build api
docker compose build admin

# Restart with zero-downtime rolling restart
docker compose up -d --no-deps api
docker compose up -d --no-deps admin
```

### Backing up the database

The SQLite database lives in the `ephemeral-data` Docker volume. Back it up with:

```bash
# Create a backup inside the running container
docker compose exec api sqlite3 /app/data/ephemeral.db \
  ".backup '/app/data/backup.db'"

# Copy it to the host
docker cp ephemeral-api:/app/data/backup.db \
  ~/backups/ephemeral-$(date +%Y%m%d-%H%M%S).db
```

Set this up as a cron job for automated daily backups:

```bash
crontab -e
```

Add:

```cron
0 3 * * * cd /home/your-user/ephemeral && \
  docker compose exec -T api sqlite3 /app/data/ephemeral.db ".backup '/app/data/backup.db'" && \
  docker cp ephemeral-api:/app/data/backup.db \
    /home/your-user/backups/ephemeral-$(date +\%Y\%m\%d).db
```

### Viewing logs

```bash
# All services, live
docker compose logs -f

# Single service, last 100 lines
docker compose logs --tail=100 api
docker compose logs --tail=100 admin
docker compose logs --tail=100 nginx

# nginx access log specifically
docker compose exec nginx tail -f /var/log/nginx/access.log
```

### Checking service health

```bash
docker compose ps
```

All three services should show `Up` with `(healthy)` for the `api` service.

### Stopping and starting

```bash
# Stop all (containers are preserved)
docker compose stop

# Start all
docker compose start

# Full restart
docker compose restart

# Stop and remove containers (volumes are preserved)
docker compose down

# Stop and remove containers AND volumes — wipes database
docker compose down -v
```

### Updating nginx config

After editing `nginx/nginx.conf`, reload without restarting the container:

```bash
# Validate the config first
docker compose exec nginx nginx -t

# If valid, reload
docker compose exec nginx nginx -s reload
```

---

## 15. Troubleshooting

### Admin portal shows "Connection refused" or blank page

The admin container calls the API on `http://api:5000`. This requires the `api` service to be healthy. Check:

```bash
docker compose ps
docker compose logs api
```

If the `api` container is not healthy, the `admin` container will not start (the `depends_on: condition: service_healthy` in `docker-compose.yml` enforces this).

### Certificate not loading / SSL handshake fails

Check that the symlinks in `nginx/ssl/` are not broken:

```bash
ls -la nginx/ssl/
# Both cert.pem and key.pem should show -> /etc/letsencrypt/live/...

# Check the files they point to exist
sudo ls -la /etc/letsencrypt/live/api.your-domain.com/
```

If the links point at a directory that does not exist (e.g., after a renewal changed the path), remove the symlinks and recreate them as in Section 9.3.

### Certbot fails with "Problem binding to port 80"

If nginx is still running when you try to issue a certificate, Certbot cannot bind to port 80. Stop nginx first:

```bash
docker compose stop nginx
sudo certbot certonly --standalone -d api.your-domain.com -d admin.your-domain.com
docker compose start nginx
```

### nginx shows 502 Bad Gateway

The upstream service (`api` or `admin`) is not responding. Check:

```bash
# Is the api container running and healthy?
docker compose ps api

# Can nginx reach the api container on the internal network?
docker compose exec nginx curl -s http://api:5000/health

# Check api logs for errors
docker compose logs --tail=50 api
```

### Rate limit errors (429 Too Many Requests)

nginx applies rate limits at the connection level. If you are hitting 429s during testing, temporarily comment out the `limit_req` directives in `nginx.conf` and reload:

```bash
docker compose exec nginx nginx -s reload
```

Remember to re-enable them before going to production.

### ephemeris calculation errors

If the API returns errors related to ephemeris data, the `.se1` files are either missing or not being found. Check:

```bash
# Verify the files are in the right place on the host
ls -la ephemeralREST/sweph/

# Verify they are visible inside the container
docker compose exec api ls -la /app/sweph/

# Check that SWISS_EPHEMERIS_PATH is correct
docker compose exec api env | grep SWISS
```

### Viewing all environment variables inside a container

```bash
docker compose exec api env
docker compose exec admin env
```

Useful for confirming that `.env` values are being picked up correctly.