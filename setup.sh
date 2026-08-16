#!/usr/bin/env bash
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
################################################################################
################################################################################
# setup.sh
#
# Interactive first-time setup for a Docker-based ephemeralREST +
# ephemeralADMIN deployment. Prompts for the handful of values that
# genuinely need a human decision (domains, database choice, geocoding
# mode), auto-generates or derives everything else, writes .env and
# nginx.conf, optionally downloads the Swiss Ephemeris and cities5000
# data files, brings the containers up, and optionally requests real
# HTTPS certificates.
#
# Run this from the ephemeralREST directory, with ephemeralADMIN checked
# out as a sibling directory (../ephemeralADMIN) — same layout as
# DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md assumes throughout.
################################################################################

set -euo pipefail

# ── Formatting helpers ───────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

heading() { echo -e "\n${BOLD}== $1 ==${RESET}"; }
info()    { echo -e "${DIM}$1${RESET}"; }
ok()      { echo -e "${GREEN}✓${RESET} $1"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $1"; }
fail()    { echo -e "${RED}✗ $1${RESET}"; exit 1; }

# Prompt with a default value. Usage: VAR=$(ask "Question" "default")
ask() {
    local prompt="$1" default="${2:-}" answer
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " answer
        echo "${answer:-$default}"
    else
        read -r -p "$prompt: " answer
        echo "$answer"
    fi
}

# Yes/no prompt, defaulting to yes. Usage: if confirm "Question?"; then
confirm() {
    local prompt="$1" answer
    read -r -p "$prompt [Y/n]: " answer
    [ -z "$answer" ] || [ "${answer,,}" = "y" ] || [ "${answer,,}" = "yes" ]
}

# Yes/no prompt, defaulting to no. Usage: if confirm_no "Question?"; then
confirm_no() {
    local prompt="$1" answer
    read -r -p "$prompt [y/N]: " answer
    [ "${answer,,}" = "y" ] || [ "${answer,,}" = "yes" ]
}

################################################################################
# 0. Pre-flight checks
################################################################################

heading "ephemeralREST — Interactive Setup"

if [ ! -f "config.py" ] || [ ! -f "docker-compose.yml" ]; then
    fail "Run this from the ephemeralREST directory (config.py and docker-compose.yml not found here)."
fi

if [ ! -d "../ephemeralADMIN" ]; then
    fail "../ephemeralADMIN not found — ephemeralADMIN needs to be checked out as a sibling directory. See DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 2."
fi

if ! command -v docker >/dev/null 2>&1; then
    fail "docker not found. Install Docker first — see DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 1."
fi

if ! docker compose version >/dev/null 2>&1; then
    fail "'docker compose' (the plugin, not the standalone docker-compose) not found."
fi

if ! command -v openssl >/dev/null 2>&1; then
    fail "openssl not found — needed to generate SECRET_KEY. Install it first (e.g. 'apt install openssl')."
fi

if ! command -v unzip >/dev/null 2>&1; then
    warn "unzip not found — the cities5000 dataset step below will fail if you use it."
    warn "Install it now if you plan to use offline geocoding: apt install unzip"
fi

if [ -f ".env" ]; then
    warn ".env already exists — this looks like it's already been set up once."
    if ! confirm_no "Overwrite it and start fresh?"; then
        info "Leaving .env untouched. Exiting."
        exit 0
    fi
fi

ok "Pre-flight checks passed."

################################################################################
# 1. Domains
################################################################################

heading "Domains"
info "These need real DNS A records pointing at this server's public IP"
info "before certificates can be issued later in this script — they don't"
info "need to resolve correctly yet to continue with the rest of setup."
echo ""

while true; do
    API_DOMAIN=$(ask "API domain (e.g. api.yourdomain.com)")
    [[ "$API_DOMAIN" == *.* ]] && [ -n "$API_DOMAIN" ] && break
    warn "That doesn't look like a domain — try again."
done

while true; do
    ADMIN_DOMAIN=$(ask "Admin portal domain (e.g. admin.yourdomain.com)")
    [[ "$ADMIN_DOMAIN" == *.* ]] && [ -n "$ADMIN_DOMAIN" ] && [ "$ADMIN_DOMAIN" != "$API_DOMAIN" ] && break
    if [ "$ADMIN_DOMAIN" = "$API_DOMAIN" ]; then
        warn "This needs to be different from the API domain."
    else
        warn "That doesn't look like a domain — try again."
    fi
done

ok "API: $API_DOMAIN"
ok "Admin: $ADMIN_DOMAIN"

################################################################################
# 2. Database
################################################################################

heading "Database"
echo "  1) SQLite  — zero-config, good for personal/low-traffic use"
echo "  2) MySQL   — recommended for production or higher traffic"
DB_CHOICE=$(ask "Choice" "1")

if [ "$DB_CHOICE" = "2" ]; then
    DB_TYPE="mysql"
    MYSQL_HOST=$(ask "MySQL host" "host.docker.internal")
    MYSQL_PORT=$(ask "MySQL port" "3306")
    MYSQL_USER=$(ask "MySQL user" "ephemeral")
    while true; do
        read -r -s -p "MySQL password: " MYSQL_PASSWORD; echo ""
        [ -n "$MYSQL_PASSWORD" ] && break
        warn "Password can't be empty."
    done
    MYSQL_DATABASE=$(ask "MySQL database name" "ephemeral")
    ok "MySQL configured (assumes the database and user already exist — see DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 4, if not)."
else
    DB_TYPE="sqlite"
    ok "SQLite selected — no further database setup needed."
fi

################################################################################
# 3. Geocoding
################################################################################

heading "Geocoding"
echo "  1) Offline (cities5000 dataset) — free, no API key, good coverage"
echo "  2) Google Maps API — more precise, requires a paid Google API key"
GEO_CHOICE=$(ask "Choice" "1")

if [ "$GEO_CHOICE" = "2" ]; then
    USE_GOOGLE="true"
    while true; do
        GOOGLE_MAPS_API_KEY=$(ask "Google Maps API key")
        [ -n "$GOOGLE_MAPS_API_KEY" ] && break
        warn "Required when Google Maps geocoding is selected."
    done
else
    USE_GOOGLE="false"
    GOOGLE_MAPS_API_KEY=""
    ok "Offline geocoding selected — the cities5000 dataset will be offered below."
fi

################################################################################
# 4. Branding
################################################################################

heading "Branding"
SITE_NAME=$(ask "Site name, shown throughout the portal" "ephemeralREST")

################################################################################
# 5. Generate SECRET_KEY
################################################################################

heading "Security"
SECRET_KEY=$(openssl rand -hex 32)
ok "Generated a new SECRET_KEY (never reuse this across deployments)."

################################################################################
# 6. Write .env
################################################################################

heading "Writing .env"

cat > .env <<EOF
# Generated by setup.sh on $(date -u +"%Y-%m-%d %H:%M UTC")

FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=false
SECRET_KEY=${SECRET_KEY}

USE_GOOGLE=${USE_GOOGLE}
GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY}

CITIES_FOLDER=./cities

DB_TYPE=${DB_TYPE}
DATABASE_PATH=/app/data/ephemeral.db
EOF

if [ "$DB_TYPE" = "mysql" ]; then
cat >> .env <<EOF
MYSQL_HOST=${MYSQL_HOST}
MYSQL_PORT=${MYSQL_PORT}
MYSQL_USER=${MYSQL_USER}
MYSQL_PASSWORD=${MYSQL_PASSWORD}
MYSQL_DATABASE=${MYSQL_DATABASE}
EOF
fi

cat >> .env <<EOF

SWISS_EPHEMERIS_PATH=/app/sweph

LOG_LEVEL=INFO

MAX_MONTHLY_REQUESTS=10000

CORS_ORIGINS=https://${ADMIN_DOMAIN}
CORS_METHODS=GET,POST,PUT,DELETE,OPTIONS
CORS_HEADERS=Content-Type,Authorization,X-Requested-With,X-API-Key

RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=10
RATE_LIMIT_PER_HOUR=50
RATE_LIMIT_PER_DAY=200

CACHE_EXPIRY_DAYS=90

TRUSTED_DEVICE_DAYS=28
TWO_FACTOR_CODE_EXPIRY_MINUTES=10

API_BASE_URL=https://${API_DOMAIN}
PORTAL_URL=https://${ADMIN_DOMAIN}
API_PUBLIC_URL=https://${API_DOMAIN}

SITE_NAME=${SITE_NAME}
ALLOW_ADMIN_PROMOTION=true
EOF

chmod 600 .env
ok "Wrote .env (permissions restricted to your user — it contains real secrets)."

################################################################################
# 7. Generate nginx.conf from the template
################################################################################

heading "Writing nginx.conf"

if [ ! -f "nginx.conf.example" ]; then
    fail "nginx.conf.example not found — can't generate nginx.conf without it."
fi

sed \
    -e "s/api\.yourdomain\.com/${API_DOMAIN}/g" \
    -e "s/admin\.yourdomain\.com/${ADMIN_DOMAIN}/g" \
    nginx.conf.example > nginx.conf

ok "Wrote nginx.conf with your real domains (this file is gitignored — future"
info "  git pulls will never revert it back to the placeholder template)."

################################################################################
# 8. Swiss Ephemeris data files
################################################################################

heading "Swiss Ephemeris data files"
mkdir -p sweph

download_sweph_file() {
    local name="$1"
    local url="https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/${name}"
    local dest="sweph/${name}"

    if [ -f "$dest" ]; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null || echo 0)
        if [ "$size" -gt 10000 ]; then
            ok "$name already present (${size} bytes) — skipping."
            return 0
        else
            warn "$name exists but is suspiciously small (${size} bytes) — re-downloading."
        fi
    fi

    curl -sL -o "$dest" "$url"
    local size
    size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null || echo 0)

    # A real .se1 file here is always well over 10KB — anything smaller
    # almost certainly means the download silently saved an error page
    # instead of real data. This exact failure mode (a 14-byte file that
    # looked fine to a plain `ls` for days) is what this check exists to
    # catch immediately instead of letting it surface later as
    # mysterious "chart calculation failed" errors.
    if [ "$size" -lt 10000 ]; then
        warn "$name downloaded but is only ${size} bytes — this download likely failed."
        rm -f "$dest"
        return 1
    fi

    ok "$name (${size} bytes)"
    return 0
}

if confirm "Download the required .se1 ephemeris data files now?"; then
    SWEPH_OK=true
    download_sweph_file "sepl_18.se1" || SWEPH_OK=false
    download_sweph_file "semo_18.se1" || SWEPH_OK=false
    download_sweph_file "seas_18.se1" || SWEPH_OK=false
    if [ "$SWEPH_OK" = false ]; then
        warn "One or more ephemeris files failed to download correctly."
        warn "Chart calculations will fail until this is resolved — see"
        warn "DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 3."
    fi
else
    warn "Skipped — you'll need to place real .se1 files in ./sweph/ yourself"
    warn "before chart calculations will work. See DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 3."
fi

################################################################################
# 9. Cities5000 geocoding dataset (offline mode only)
################################################################################

if [ "$USE_GOOGLE" = "false" ]; then
    heading "Cities5000 geocoding dataset"
    mkdir -p cities

    if confirm "Download the cities5000 geocoding dataset now?"; then
        TMP_ZIP=$(mktemp)
        curl -sL -o "$TMP_ZIP" "https://download.geonames.org/export/dump/cities5000.zip"
        if unzip -q -o "$TMP_ZIP" -d cities/ 2>/dev/null; then
            ok "Downloaded and extracted cities5000.txt — it will be imported"
            info "  automatically the first time the API container starts (this"
            info "  makes that first startup noticeably slower — expected)."
        else
            warn "Download or extraction failed — you'll need to place"
            warn "cities5000.txt in ./cities/ yourself. See DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md."
        fi
        rm -f "$TMP_ZIP"
    else
        warn "Skipped — location lookups won't work until a cities5000.txt file"
        warn "is placed in ./cities/. See DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md."
    fi
fi

################################################################################
# 10. Build and start
################################################################################

heading "Build and start containers"

if confirm "Build and start all containers now?"; then
    docker compose up -d --build
    ok "Containers started."
    echo ""
    info "Checking status in 5 seconds..."
    sleep 5
    docker compose ps
else
    info "Skipped. Run this when ready:"
    info "  docker compose up -d --build"
fi

################################################################################
# 11. Certificates
################################################################################

heading "HTTPS certificates"
info "This step needs:"
info "  - DNS for both domains already pointing at this server's public IP"
info "  - Port 80 reachable from the internet (see DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md,"
info "    Phase 5 — firewall, including your VPS provider's own network-level"
info "    firewall if it has one, separate from ufw)"
info "  - nginx not already bound to port 80 (certbot's standalone mode needs it free)"
echo ""

if confirm_no "Attempt to obtain real HTTPS certificates now via certbot?"; then
    if ! command -v certbot >/dev/null 2>&1; then
        fail "certbot not found. Install it first (see DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md, Phase 7), then re-run:
  sudo certbot certonly --standalone -d ${API_DOMAIN} -d ${ADMIN_DOMAIN}"
    fi

    info "Stopping nginx temporarily so certbot can bind port 80..."
    docker compose stop nginx || true

    if sudo certbot certonly --standalone -d "$API_DOMAIN" -d "$ADMIN_DOMAIN"; then
        ok "Certificate obtained."
        docker compose up -d nginx
        ok "nginx restarted with the new certificate."
    else
        warn "certbot failed — nginx.conf's HTTPS server blocks won't work until"
        warn "this is resolved. Restarting nginx anyway (it'll still serve the"
        warn "non-HTTPS parts correctly)."
        docker compose up -d nginx
    fi
else
    info "Skipped. When ready:"
    info "  docker compose stop nginx"
    info "  sudo certbot certonly --standalone -d ${API_DOMAIN} -d ${ADMIN_DOMAIN}"
    info "  docker compose up -d nginx"
fi

################################################################################
# 12. Summary
################################################################################

heading "Setup complete"
echo ""
echo "  API:    https://${API_DOMAIN}"
echo "  Portal: https://${ADMIN_DOMAIN}"
echo ""
info "Next steps:"
info "  1. curl https://${API_DOMAIN}/health   — confirm the API is reachable"
info "  2. Create your first admin account:"
info "       docker compose exec ephemeral-rest python3 key_manager.py create"
info "  3. Log into the portal at https://${ADMIN_DOMAIN} with that account"
echo ""
info "Full walkthrough, including firewall setup if you skipped it above:"
info "  DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md"
