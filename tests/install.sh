#!/usr/bin/env bash
# ephemeralREST installer: https://github.com/gmelh/ephemeralREST
#
#   curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash
#
# On a fresh Ubuntu or Debian server, this:
#   1. installs Docker (if it isn't there),
#   2. checks your addresses point at this server,
#   3. sets up the server's shared Caddy (the `studioapps` command), which Studio apps on the
#      same server use too, and which gets and renews the HTTPS certificates,
#   4. sets up ephemeralREST behind it,
#   5. downloads the Swiss Ephemeris files and the GeoNames cities list,
#   6. adds an `ephemeral` command for updates, backups and logs, and a daily backup.
#
# Running it again is safe: it keeps your data and settings and updates the setup. An install
# that ran its own Caddy is moved onto the shared Caddy, with its certificate.
#
# Options (all optional; you're asked for anything missing):
#   --domain api.example.com    the address ephemeralREST will live at (the API and web app)
#   --admin-domain admin.example.com
#                               the address administrators use; with one, administration works
#                               only there (none: it's on the main address, as before)
#   --email you@example.com     for certificate expiry notices from Let's Encrypt
#   --google-key KEY            look places up with Google Maps (otherwise offline, from the cities list)
#   --mysql-host HOST           use a MySQL database (e.g. Vultr Managed MySQL) instead of the
#                               built-in SQLite one. The connection is checked before starting.
#   --mysql-port PORT           (default 3306; Vultr's is on the database's page)
#   --mysql-user USER
#   --mysql-password-file FILE  the password, read from a file (or you're asked for it; or
#                               --mysql-password PASSWORD, which other users of the server can see)
#   --mysql-database NAME       the database, which must already exist (default ephemeral)
#   --mysql-ca FILE             the database's CA certificate (Vultr: Download Signed Certificate),
#                               so the server's certificate is checked
#   --mysql-ssl-mode MODE       VERIFY_CA (the default with --mysql-ca), REQUIRED (the default
#                               without), VERIFY_IDENTITY, PREFERRED or DISABLED
#   --switch-to-mysql           move an existing install to MySQL, starting with an empty
#                               database (its SQLite data isn't copied; the file is kept)
#   --dir DIR                   where it's installed (default /opt/studioapps/ephemeral; an
#                               existing install in /opt/ephemeral stays there)
#   --image IMAGE               the ephemeralREST container image to run
#   --caddy-extra FILE          extra lines for its sites in the shared Caddy, e.g. a tls block
#   --edge-script FILE          use this copy of the shared Caddy's script (it's still checked)
#   --no-data                   don't download the ephemeris files or cities list
#   --yes                       don't ask questions (needs --domain)
#   --skip-dns-check            don't check the domain points here
#   --dry-run                   write the files, but don't install or start anything
#   --help                      show this
#   (Anything else goes in $DIR/.env — see the end of that file.)

# Plain `sh` (dash on Ubuntu) can't run this script. Say so before it trips over Bash syntax.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "ephemeralREST's installer needs bash. Run it like this:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash" >&2
  exit 1
fi

set -Eeuo pipefail
# Never stop silently: say where it went wrong.
trap 'printf "\nError: the installer stopped unexpectedly (line %s). Please report this, with the output above.\n" "$LINENO" >&2' ERR

# The major version, so a future 2.0 with breaking changes never arrives through
# `ephemeral update` by surprise.
DEFAULT_IMAGE="ghcr.io/gmelh/ephemeralrest:1"
DEFAULT_DIR="/opt/studioapps/ephemeral"
OLD_DEFAULT_DIR="/opt/ephemeral"  # installs from before the shared Caddy live here, and stay here
APP_UID=10001  # the container's ephemeral user

# The server's shared Caddy (github.com/studioappsorg/studio-edge), installed as the
# `studioapps` command. This is the version this release was tested with: never a newer one
# by surprise, and never one whose SHA-256 doesn't match.
EDGE_VERSION="0.1.0"
EDGE_SHA256="e52391e30d05f40ff1c0c42074e36c4658f9892d0be76277d058d7a6cb066bef"
EDGE_URL="https://getstudioapps.com/edge/edge-${EDGE_VERSION}.sh"
STUDIOAPPS=/usr/local/bin/studioapps
EDGE_DIR="${STUDIOAPPS_EDGE_DIR:-/opt/studioapps/edge}"
EDGE_CONTAINER="studioapps-edge"
EDGE_IP="172.31.250.2"   # fixed by studio-edge; the app trusts forwarded addresses from here only
SITE="ephemeral"         # this app's site name in the shared Caddy, and its network alias prefix

# Everything runs from main(), at the very end, so a half-downloaded script does nothing.

bold=""; dim=""; red=""; green=""; reset=""
if [ -t 1 ]; then
  bold=$'\e[1m'; dim=$'\e[2m'; red=$'\e[31m'; green=$'\e[32m'; reset=$'\e[0m'
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
ok()   { printf '%s✓%s %s\n' "$green" "$reset" "$*"; }
warn() { printf '%s!%s %s\n' "$red" "$reset" "$*" >&2; }
die()  { printf '\n%sError:%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

# Questions read from the terminal, because stdin is this script when piped from curl.
has_tty() { [ -r /dev/tty ] && [ -w /dev/tty ] && { : < /dev/tty; } 2>/dev/null; }
ask() {
  local prompt=$1 default=${2:-} answer
  if [ -n "$default" ]; then prompt="$prompt [$default]"; fi
  printf '%s: ' "$prompt" > /dev/tty
  IFS= read -r answer < /dev/tty || answer=""
  printf '%s' "${answer:-$default}"
}

valid_domain() {
  [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]
}

usage() { sed -n '2,50p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }

edge_running() {
  command -v docker >/dev/null 2>&1 &&
    [ "$(docker inspect -f '{{.State.Running}}' "$EDGE_CONTAINER" 2>/dev/null || true)" = "true" ]
}


# ---------------------------------------------------------------------------

parse_args() {
  DOMAIN=""; ADMIN_DOMAIN=""; ACME_EMAIL=""; DIR=""; IMAGE=""; YES=0; DRY_RUN=0; SKIP_DNS=0
  GOOGLE_KEY=""; NO_DATA=0; EDGE_SCRIPT=""; CADDY_EXTRA=""
  MYSQL_HOST=""; MYSQL_PORT=""; MYSQL_USER=""; MYSQL_PASSWORD=""; MYSQL_DATABASE=""
  MYSQL_CA_FILE=""; MYSQL_SSL_MODE=""; SWITCH_TO_MYSQL=0; local password_file=""
  while [ $# -gt 0 ]; do
    case $1 in
      --domain) DOMAIN=${2:-}; shift 2 ;;
      --admin-domain) ADMIN_DOMAIN=${2:-}; shift 2 ;;
      --edge-script) EDGE_SCRIPT=${2:-}; shift 2 ;;
      --caddy-extra) CADDY_EXTRA=${2:-}; shift 2 ;;
      --email) ACME_EMAIL=${2:-}; shift 2 ;;
      --google-key) GOOGLE_KEY=${2:-}; shift 2 ;;
      --mysql-host) MYSQL_HOST=${2:-}; shift 2 ;;
      --mysql-port) MYSQL_PORT=${2:-}; shift 2 ;;
      --mysql-user) MYSQL_USER=${2:-}; shift 2 ;;
      --mysql-password) MYSQL_PASSWORD=${2:-}; shift 2 ;;
      --mysql-password-file) password_file=${2:-}; shift 2 ;;
      --mysql-database) MYSQL_DATABASE=${2:-}; shift 2 ;;
      --mysql-ca) MYSQL_CA_FILE=${2:-}; shift 2 ;;
      --mysql-ssl-mode) MYSQL_SSL_MODE=$(printf '%s' "${2:-}" | tr '[:lower:]-' '[:upper:]_'); shift 2 ;;
      --switch-to-mysql) SWITCH_TO_MYSQL=1; shift ;;
      --dir) DIR=${2:-}; shift 2 ;;
      --image) IMAGE=${2:-}; shift 2 ;;
      --no-data) NO_DATA=1; shift ;;
      --yes|-y) YES=1; shift ;;
      --skip-dns-check) SKIP_DNS=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *) die "Unknown option: $1 (try --help)" ;;
    esac
  done
  DOMAIN=$(clean_domain "$DOMAIN")
  ADMIN_DOMAIN=$(clean_domain "$ADMIN_DOMAIN")
  if [ -z "$DIR" ]; then
    if [ -f "$OLD_DEFAULT_DIR/.env" ]; then DIR=$OLD_DEFAULT_DIR; else DIR=$DEFAULT_DIR; fi
  fi
  if [ -n "$CADDY_EXTRA" ]; then
    [ -f "$CADDY_EXTRA" ] || die "--caddy-extra: there's no file at $CADDY_EXTRA"
    CADDY_EXTRA=$(cd "$(dirname "$CADDY_EXTRA")" && printf '%s/%s' "$PWD" "$(basename "$CADDY_EXTRA")")
  fi
  if [ -n "$password_file" ]; then
    [ -f "$password_file" ] || die "--mysql-password-file: there's no file at $password_file"
    MYSQL_PASSWORD=$(head -n 1 "$password_file" | tr -d '\r\n')
  fi
  if [ -n "$MYSQL_CA_FILE" ]; then
    [ -f "$MYSQL_CA_FILE" ] || die "--mysql-ca: there's no file at $MYSQL_CA_FILE"
    grep -q 'BEGIN CERTIFICATE' "$MYSQL_CA_FILE" || die "--mysql-ca: $MYSQL_CA_FILE isn't a PEM certificate"
  fi
  case $MYSQL_SSL_MODE in
    ''|DISABLED|PREFERRED|REQUIRED|VERIFY_CA|VERIFY_IDENTITY) ;;
    *) die "--mysql-ssl-mode: use VERIFY_CA, VERIFY_IDENTITY, REQUIRED, PREFERRED or DISABLED" ;;
  esac
  if [ -n "$MYSQL_PORT" ] && ! [[ $MYSQL_PORT =~ ^[0-9]{1,5}$ ]]; then die "--mysql-port: $MYSQL_PORT isn't a port number"; fi
  WANT_MYSQL=0
  if [ -n "$MYSQL_HOST$MYSQL_USER$MYSQL_DATABASE$MYSQL_CA_FILE" ] || [ "$SWITCH_TO_MYSQL" -eq 1 ]; then WANT_MYSQL=1; fi
}

clean_domain() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's|^https\{0,1\}://||; s|/.*$||'; }

# Settings the installer manages in .env. Every other line there is yours and is kept.
MANAGED='^(EPHEMERAL_DOMAIN|ADMIN_DOMAIN|ACME_EMAIL|EPHEMERAL_IMAGE|SECRET_KEY|USE_GOOGLE|GOOGLE_MAPS_API_KEY|DB_TYPE|MYSQL_HOST|MYSQL_PORT|MYSQL_USER|MYSQL_PASSWORD|MYSQL_DATABASE|MYSQL_SSL_CA|MYSQL_SSL_MODE)='

# Inside the container, ./data is /data. A CA given with --mysql-ca is copied here.
MYSQL_CA_NAME="mysql-ca.pem"


load_existing() {
  SECRET_KEY=""; USE_GOOGLE=""; EXTRA_DOMAINS=""; DB_TYPE=""; MYSQL_SSL_CA=""
  if [ -f "$DIR/.env" ]; then
    local key value
    while IFS='=' read -r key value; do
      case $key in
        EPHEMERAL_DOMAIN) [ -n "$DOMAIN" ] || DOMAIN=$value ;;
        ADMIN_DOMAIN) [ -n "$ADMIN_DOMAIN" ] || ADMIN_DOMAIN=$value ;;
        ACME_EMAIL) [ -n "$ACME_EMAIL" ] || ACME_EMAIL=$value ;;
        EPHEMERAL_IMAGE) [ -n "$IMAGE" ] || IMAGE=$value ;;
        SECRET_KEY) SECRET_KEY=$value ;;
        USE_GOOGLE) USE_GOOGLE=$value ;;
        GOOGLE_MAPS_API_KEY) [ -n "$GOOGLE_KEY" ] || GOOGLE_KEY=$value ;;
        EXTRA_DOMAINS) EXTRA_DOMAINS=$(printf '%s' "$value" | tr ',' ' ') ;;
        DB_TYPE) DB_TYPE=$value ;;
        MYSQL_HOST) [ -n "$MYSQL_HOST" ] || MYSQL_HOST=$value ;;
        MYSQL_PORT) [ -n "$MYSQL_PORT" ] || MYSQL_PORT=$value ;;
        MYSQL_USER) [ -n "$MYSQL_USER" ] || MYSQL_USER=$(unquote "$value") ;;
        MYSQL_PASSWORD) [ -n "$MYSQL_PASSWORD" ] || MYSQL_PASSWORD=$(unquote "$value") ;;
        MYSQL_DATABASE) [ -n "$MYSQL_DATABASE" ] || MYSQL_DATABASE=$value ;;
        MYSQL_SSL_CA) MYSQL_SSL_CA=$value ;;
        MYSQL_SSL_MODE) [ -n "$MYSQL_SSL_MODE" ] || MYSQL_SSL_MODE=$value ;;
      esac
    done < "$DIR/.env"
    EXISTING=1
  else
    EXISTING=0
  fi
  # Before the shared Caddy each install ran its own (a `caddy` service in its compose.yml).
  OWN_CADDY=0
  if [ "$EXISTING" -eq 1 ] && [ -f "$DIR/compose.yml" ] && grep -q '^  caddy:' "$DIR/compose.yml"; then
    OWN_CADDY=1
  fi
  [ -n "$IMAGE" ] || IMAGE=$DEFAULT_IMAGE
  # The secret key encrypts every stored API key: made once, never changed.
  [ -n "$SECRET_KEY" ] || SECRET_KEY=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  if [ -n "$GOOGLE_KEY" ]; then USE_GOOGLE=true; else USE_GOOGLE=false; fi
  DB_TYPE=$(printf '%s' "${DB_TYPE:-sqlite}" | tr '[:upper:]' '[:lower:]')
  WAS_MYSQL=0
  if [ "$DB_TYPE" = mysql ]; then WAS_MYSQL=1; fi
  if [ "$WANT_MYSQL" -eq 1 ]; then DB_TYPE=mysql; fi
}

# docker compose reads .env values without quotes with $ and # meaning something, so the MySQL user
# and password are written in single quotes (taken literally), and read back without them.
unquote() {
  local v=$1
  if [ "${#v}" -ge 2 ] && [ "${v:0:1}" = "'" ] && [ "${v: -1}" = "'" ]; then v=${v:1:${#v}-2}; fi
  printf '%s' "$v"
}

check_system() {
  step "Checking this server"
  [ "$(uname -s)" = "Linux" ] || die "ephemeralREST installs on Linux servers."
  if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "Run it as root: curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash"
  fi
  case $(uname -m) in
    x86_64|amd64|aarch64|arm64) ;;
    *) die "This processor ($(uname -m)) isn't supported. Use a 64-bit Intel/AMD or ARM server." ;;
  esac
  local memory_mb disk_mb
  memory_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "$memory_mb" -gt 0 ] && [ "$memory_mb" -lt 900 ]; then
    warn "This server has ${memory_mb} MB of memory. 1 GB or more is recommended."
  fi
  local probe=$DIR  # the nearest folder that exists: on some servers even /opt doesn't yet
  while [ ! -d "$probe" ]; do probe=$(dirname "$probe"); done
  disk_mb=$(df -Pm "$probe" 2>/dev/null | awk 'NR==2 {print $4}' || true)
  if [ -n "$disk_mb" ] && [ "$disk_mb" -lt 3072 ]; then
    warn "Only ${disk_mb} MB of disk space is free. 3 GB or more is recommended (the ephemeris files and cities list need room)."
  fi
  # shellcheck disable=SC1091
  ok "$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}"), $(uname -m), ${memory_mb} MB memory"
}

check_internet() {
  local problem=""
  if command -v ip >/dev/null 2>&1 && ! ip -4 route show default 2>/dev/null | grep -q .; then
    problem="This server has no public IPv4 address (it has no IPv4 route to the internet).
ephemeralREST needs one: your domain's A record points to it, and it's how the server downloads
Docker and ephemeralREST. In your hosting provider's dashboard, make sure the server has a public
IPv4 address (some providers offer IPv6-only or private-network-only servers). Then restart the
server and run the installer again."
  elif command -v curl >/dev/null 2>&1 && ! curl -4 -fsS --max-time 15 -o /dev/null https://get.docker.com 2>/dev/null; then
    problem="This server can't reach the internet: https://get.docker.com didn't answer.
Check its network and firewall settings in your hosting provider's dashboard (outgoing
connections must be allowed), then run the installer again."
  fi
  [ -z "$problem" ] && return
  if [ "$DRY_RUN" -eq 1 ]; then warn "(dry run) $problem"; return; fi
  die "$problem"
}

choose_domain() {
  step "Your ephemeralREST address"
  if [ -z "$DOMAIN" ]; then
    if [ "$YES" -eq 1 ] || ! has_tty; then die "Give the address with --domain, e.g. --domain astro.example.com"; fi
    say "ephemeralREST needs its own address, like ${bold}astro.example.com${reset}."
    say "In your domain's DNS settings, add an ${bold}A record${reset} for it pointing to this server's IP address."
    while :; do
      DOMAIN=$(ask "Address" | tr '[:upper:]' '[:lower:]' | sed 's|^https\{0,1\}://||; s|/.*$||')
      valid_domain "$DOMAIN" && break
      warn "That doesn't look like a domain name. Try something like astro.example.com"
    done
  fi
  valid_domain "$DOMAIN" || die "\"$DOMAIN\" isn't a valid domain name."
  if [ -z "$ADMIN_DOMAIN" ] && [ "$EXISTING" -eq 0 ] && [ "$YES" -eq 0 ] && has_tty; then
    say "Administrators can have an address of their own, like ${bold}admin.${DOMAIN#*.}${reset}: administration then"
    say "works only there. Leave it empty to administer at https://$DOMAIN."
    while :; do
      ADMIN_DOMAIN=$(clean_domain "$(ask "Admin address (optional)" "")")
      if [ -z "$ADMIN_DOMAIN" ] || valid_domain "$ADMIN_DOMAIN"; then break; fi
      warn "That doesn't look like a domain name."
    done
  fi
  if [ -n "$ADMIN_DOMAIN" ]; then
    valid_domain "$ADMIN_DOMAIN" || die "\"$ADMIN_DOMAIN\" isn't a valid domain name."
    [ "$ADMIN_DOMAIN" != "$DOMAIN" ] || die "The admin address must be different from the main one."
  fi
  if [ -z "$ACME_EMAIL" ] && [ "$YES" -eq 0 ] && has_tty; then
    ACME_EMAIL=$(ask "Your email, for certificate notices (optional)" "")
  fi
  ok "https://$DOMAIN${ADMIN_DOMAIN:+ (administration at https://$ADMIN_DOMAIN)}"
}

choose_geocoding() {
  step "Looking up places"
  if [ "$EXISTING" -eq 0 ] && [ -z "$GOOGLE_KEY" ] && [ "$YES" -eq 0 ] && has_tty; then
    say "Birth places are looked up offline, in the free GeoNames list of cities over 5,000 people."
    say "With a Google Maps API key (Geocoding and Time Zone APIs) any address can be found instead."
    GOOGLE_KEY=$(ask "Google Maps API key (optional — press Enter to stay offline)" "")
    if [ -n "$GOOGLE_KEY" ]; then USE_GOOGLE=true; fi
  fi
  if [ "$USE_GOOGLE" = true ]; then ok "Google Maps, with the cities list for suggestions"; else ok "Offline, from the cities list"; fi
}

choose_database() {
  step "The database"
  if [ "$DB_TYPE" != mysql ] && [ "$EXISTING" -eq 0 ] && [ "$YES" -eq 0 ] && has_tty; then
    say "ephemeralREST keeps its data in a built-in SQLite database, in $DIR/data."
    say "It can use a MySQL database instead, such as Vultr Managed MySQL."
    MYSQL_HOST=$(ask "MySQL host (optional — press Enter for the built-in database)" "")
    if [ -n "$MYSQL_HOST" ]; then DB_TYPE=mysql; fi
  fi
  if [ "$DB_TYPE" != mysql ]; then ok "Built-in (SQLite), in $DIR/data"; return; fi

  # An install with data in SQLite isn't switched without being asked: the data isn't copied.
  if [ "$EXISTING" -eq 1 ] && [ "$WAS_MYSQL" -eq 0 ] && [ -s "$DIR/data/ephemeral.db" ] && [ "$SWITCH_TO_MYSQL" -eq 0 ]; then
    die "This install keeps its data in SQLite ($DIR/data/ephemeral.db). Moving to MySQL starts with
an empty database: accounts, keys and settings aren't copied. To do it anyway, add --switch-to-mysql
(the SQLite file is kept). To keep the data, install fresh with MySQL and set it up again."
  fi

  local interactive=0
  if [ "$YES" -eq 0 ] && has_tty; then interactive=1; fi
  if [ -z "$MYSQL_HOST" ]; then
    [ "$interactive" -eq 1 ] || die "Give the database's address with --mysql-host"
    MYSQL_HOST=$(ask "MySQL host" "")
  fi
  if [ -z "$MYSQL_PORT" ]; then
    if [ "$interactive" -eq 1 ]; then MYSQL_PORT=$(ask "MySQL port" "3306"); else MYSQL_PORT=3306; fi
  fi
  if [ -z "$MYSQL_USER" ]; then
    [ "$interactive" -eq 1 ] || die "Give the MySQL user with --mysql-user"
    MYSQL_USER=$(ask "MySQL user" "")
  fi
  if [ -z "$MYSQL_DATABASE" ]; then
    if [ "$interactive" -eq 1 ]; then MYSQL_DATABASE=$(ask "Database name (it must exist already)" "ephemeral"); else MYSQL_DATABASE=ephemeral; fi
  fi
  if [ -z "$MYSQL_PASSWORD" ]; then
    [ "$interactive" -eq 1 ] || die "Give the MySQL password with --mysql-password-file"
    printf 'MySQL password (not shown): ' > /dev/tty
    IFS= read -rs MYSQL_PASSWORD < /dev/tty || MYSQL_PASSWORD=""
    printf '\n' > /dev/tty
    [ -n "$MYSQL_PASSWORD" ] || die "The MySQL password is needed."
  fi
  if [ -z "$MYSQL_CA_FILE" ] && [ -z "$MYSQL_SSL_CA" ] && [ "$interactive" -eq 1 ]; then
    say "For a check that it's really your database, give its CA certificate: on Vultr, the"
    say "database's page has ${bold}Download Signed Certificate${reset}. Copy that file to this server first."
    MYSQL_CA_FILE=$(ask "CA certificate file (optional)" "")
    if [ -n "$MYSQL_CA_FILE" ]; then
      [ -f "$MYSQL_CA_FILE" ] || die "There's no file at $MYSQL_CA_FILE"
      grep -q 'BEGIN CERTIFICATE' "$MYSQL_CA_FILE" || die "$MYSQL_CA_FILE isn't a PEM certificate"
    fi
  fi
  valid_mysql_host "$MYSQL_HOST" || die "\"$MYSQL_HOST\" isn't a host name or IP address."
  [[ $MYSQL_PORT =~ ^[0-9]{1,5}$ ]] || die "\"$MYSQL_PORT\" isn't a port number."
  [[ $MYSQL_DATABASE =~ ^[A-Za-z0-9_$-]{1,64}$ ]] || die "\"$MYSQL_DATABASE\" isn't a database name MySQL accepts."
  case $MYSQL_USER$MYSQL_PASSWORD in
    *$'\n'*) die "The MySQL user or password has a line break in it." ;;
    *"'"*) die "The MySQL user or password has a ' in it, which the settings file can't hold. Choose a password without one." ;;
  esac

  if [ -n "$MYSQL_CA_FILE" ]; then MYSQL_SSL_CA="/data/$MYSQL_CA_NAME"; fi
  if [ -z "$MYSQL_SSL_MODE" ]; then
    if [ -n "$MYSQL_SSL_CA" ]; then MYSQL_SSL_MODE=VERIFY_CA; else MYSQL_SSL_MODE=REQUIRED; fi
  fi
  case $MYSQL_SSL_MODE in
    VERIFY_*) [ -n "$MYSQL_SSL_CA" ] || die "--mysql-ssl-mode $MYSQL_SSL_MODE needs the CA certificate: --mysql-ca FILE" ;;
  esac
  local tls="TLS, certificate checked"
  case $MYSQL_SSL_MODE in
    REQUIRED) tls="TLS, certificate not checked (add --mysql-ca to check it)" ;;
    PREFERRED) tls="TLS if offered, not checked" ;;
    DISABLED) tls="not encrypted" ;;
  esac
  ok "MySQL: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE ($tls)"
}

valid_mysql_host() {
  [[ $1 =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,252})$ ]] || [[ $1 =~ ^[0-9a-fA-F:]+$ ]]
}

install_docker() {
  step "Docker"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    local version
    version=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || version=""
    if [ -z "$version" ] && [ "$DRY_RUN" -eq 0 ]; then
      systemctl start docker >/dev/null 2>&1 || true
      version=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || version=""
      [ -n "$version" ] || die "Docker is installed but isn't running. Try: systemctl start docker"
    fi
    ok "Docker ${version:+$version }is ready"
    return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then say "${dim}(dry run: would install Docker from get.docker.com)${reset}"; return; fi
  command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
  say "Installing Docker (a minute or two)..."
  curl -fsSL https://get.docker.com | sh >/tmp/ephemeral-docker-install.log 2>&1 \
    || die "Installing Docker failed. Details: /tmp/ephemeral-docker-install.log"
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 || die "Docker installed, but 'docker compose' isn't available."
  ok "Docker installed"
}

public_ip() {
  curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null \
    || curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null \
    || true
}

resolved_ips() {
  getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u
}

check_dns() {
  step "Checking the addresses point to this server"
  if [ "$SKIP_DNS" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then say "${dim}(skipped)${reset}"; return; fi
  local ip
  ip=$(public_ip)
  [ -n "$ip" ] || { warn "Couldn't find this server's public IP address; skipping the check."; return; }
  check_one_dns "$DOMAIN" "$ip"
  if [ -n "$ADMIN_DOMAIN" ]; then check_one_dns "$ADMIN_DOMAIN" "$ip"; fi
}

check_one_dns() {
  local name=$1 ip=$2 found
  while :; do
    found=$(resolved_ips "$name" | tr '\n' ' ')
    if printf ' %s ' "$found" | grep -q " $ip "; then ok "$name → $ip"; return; fi
    warn "$name points to: ${found:-nothing yet}. It needs to point to this server: $ip"
    say "Add (or fix) an A record for $name with the value ${bold}$ip${reset} at your domain's DNS provider."
    say "Changes usually show up within a few minutes, sometimes longer."
    if [ "$YES" -eq 1 ] || ! has_tty; then die "The domain doesn't point here yet. Run the installer again once it does."; fi
    ask "Press Enter to check again (or Ctrl+C to stop)" "" >/dev/null
  done
}

# Ports 80 and 443 belong to the server's shared Caddy, which Studio apps use too. Before anything
# is installed, check nothing else holds them. ephemeralREST's own Caddy from before is fine: it's
# moved onto the shared Caddy below. The shared Caddy repeats this check itself, and opens the
# firewall.
check_ports() {
  [ "$DRY_RUN" -eq 1 ] && return
  edge_running && return
  local busy="" others="" name project dir
  if command -v ss >/dev/null 2>&1; then
    busy=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v docker-proxy || true)
  fi
  if command -v docker >/dev/null 2>&1; then
    others=$(for port in 80 443; do
        docker ps --filter "publish=$port" \
          --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null
      done | sort -u || true)
  fi
  local problem=0 studio_apps=" mailstudio ledgerstudio formstudio bookingstudio astrologystudio "
  if [ -n "$busy" ]; then
    warn "Something else is using port 80 or 443 on this server:"
    printf '%s\n' "$busy" >&2
    say "If it's a web server you don't need: systemctl disable --now nginx apache2"
    problem=1
  fi
  while IFS='|' read -r name project dir; do
    [ -n "$name" ] || continue
    [ "$dir" = "$DIR" ] && continue
    case $studio_apps in
      *" $project "*)
        warn "$project's own Caddy ($name, in ${dir:-?}) is using ports 80 and 443."
        say "Update $project with its newest installer first: that moves it onto the shared Caddy." ;;
      *)
        warn "The container $name (${dir:-not from docker compose}) is using ports 80 and 443." ;;
    esac
    problem=1
  done <<< "$others"
  if [ "$problem" -eq 1 ]; then
    say "Apps on this server share ports 80 and 443 through one Caddy, so nothing else can hold them."
    die "Nothing has been changed. Stop what's using the ports (or update that app), then run this again."
  fi
}


# ---------------------------------------------------------------------------
# Files

write_env() {
  local own=""
  if [ -f "$DIR/.env" ]; then
    own=$(grep -Ev "$MANAGED" "$DIR/.env" | grep -Ev '^# (ephemeralREST settings|Written by the installer)' || true)
  fi
  {
    cat <<EOF
# ephemeralREST settings, read by docker compose. Edit, then run: ephemeral restart
# Written by the installer — re-running it keeps every line below the managed ones.
EPHEMERAL_DOMAIN=$DOMAIN
ADMIN_DOMAIN=$ADMIN_DOMAIN
ACME_EMAIL=$ACME_EMAIL
EPHEMERAL_IMAGE=$IMAGE
SECRET_KEY=$SECRET_KEY
USE_GOOGLE=$USE_GOOGLE
GOOGLE_MAPS_API_KEY=$GOOGLE_KEY
EOF
    if [ "$DB_TYPE" = mysql ]; then
      cat <<EOF
DB_TYPE=mysql
MYSQL_HOST=$MYSQL_HOST
MYSQL_PORT=$MYSQL_PORT
MYSQL_USER='$MYSQL_USER'
MYSQL_PASSWORD='$MYSQL_PASSWORD'
MYSQL_DATABASE=$MYSQL_DATABASE
MYSQL_SSL_CA=$MYSQL_SSL_CA
MYSQL_SSL_MODE=$MYSQL_SSL_MODE
EOF
    fi
    if [ -n "$(printf '%s' "$own" | tr -d '[:space:]')" ]; then
      printf '%s\n' "$own"
    else
      cat <<'EOF'

# Your own settings. Anything ephemeralREST understands can go here, for example:
# SITE_NAME=ephemeralREST
# ALLOW_REGISTRATION=true
# ALLOW_ADMIN_PROMOTION=true
# CORS_ORIGINS=https://your-site.example.com
# RATE_LIMIT_PER_MINUTE=10
# RATE_LIMIT_PER_HOUR=50
# RATE_LIMIT_PER_DAY=200
# SESSION_TIMEOUT=1800
# TRUSTED_DEVICE_DAYS=28
# More addresses for this server (e.g. old ones), then re-run the installer:
# EXTRA_DOMAINS=api.example.com admin.example.com
# To use MySQL instead of the built-in SQLite database, run the installer again
# with --mysql-host and friends (see --help): it checks the connection first.
EOF
    fi
  } > "$DIR/.env.new"
  mv "$DIR/.env.new" "$DIR/.env"
  chmod 600 "$DIR/.env"
}

write_compose() {
  cat > "$DIR/compose.yml" <<EOF
# Written by the ephemeralREST installer; re-running it replaces this file.
# Your settings are in .env; your data is in ./data. For changes of your own
# (another network, extra ports), use compose.override.yml, which is never replaced.
# The server's shared Caddy (studioapps) serves it, using the site file $SITE.caddy beside this one.
name: ephemeral

services:
  app:
    image: \${EPHEMERAL_IMAGE}
    restart: unless-stopped
    env_file: .env
    environment:
      PUBLIC_URL: https://\${EPHEMERAL_DOMAIN}
      # Forwarded client addresses are believed from the shared Caddy only. Other apps'
      # containers share its network, and could otherwise claim to be any address.
      TRUST_PROXY: "true"
      TRUSTED_PROXIES: "$EDGE_IP"
    volumes:
      - ./data:/data
    networks:
      default: {}
      studioapps:
        aliases: [$SITE-app]
    logging:
      driver: local
      options:
        max-size: 10m

networks:
  # Created by the shared Caddy. Only this service joins it.
  studioapps:
    external: true
EOF
}

# The site file this installer adds to the shared Caddy: the main address (with any EXTRA_DOMAINS
# from .env, such as old addresses) and, if there is one, the admin address. Extra lines
# (--caddy-extra) are kept in site-extra.caddy, so a re-run keeps them.
write_site() {
  local extra="" d
  for d in $EXTRA_DOMAINS; do valid_domain "$d" && extra="$extra, $d"; done
  if [ -n "$CADDY_EXTRA" ] && [ "$CADDY_EXTRA" != "$DIR/site-extra.caddy" ]; then
    cp "$CADDY_EXTRA" "$DIR/site-extra.caddy"
  fi
  {
    printf '# Written by the ephemeralREST installer (%s); re-running it replaces this file.\n' "$DIR"
    printf '# The shared Caddy serves it: studioapps site add %s --file <this file>\n' "$SITE"
    printf '%s%s {\n' "$DOMAIN" "$extra"
    printf '\timport studio %s\n' "$SITE"
    printf '\trequest_body {\n\t\tmax_size 10MB\n\t}\n'
    site_extra
    printf '\treverse_proxy %s-app:8080\n' "$SITE"
    printf '}\n'
    if [ -n "$ADMIN_DOMAIN" ]; then
      printf '\n# Administration (the app refuses administration on any other address).\n'
      printf '%s {\n' "$ADMIN_DOMAIN"
      printf '\timport studio %s\n' "$SITE"
      printf '\trequest_body {\n\t\tmax_size 10MB\n\t}\n'
      site_extra
      printf '\treverse_proxy %s-app:8080\n' "$SITE"
      printf '}\n'
    fi
  } > "$DIR/$SITE.caddy"
}

site_extra() {
  if [ -f "$DIR/site-extra.caddy" ]; then sed -e '/^[[:space:]]*$/d' -e 's/^/\t/' "$DIR/site-extra.caddy"; fi
}

write_files() {
  step "Writing the setup to $DIR"
  mkdir -p "$DIR/data" "$DIR/backups"
  write_env
  write_compose
  write_site
  if [ -n "$MYSQL_CA_FILE" ] && [ "$(readlink -f "$MYSQL_CA_FILE")" != "$(readlink -f "$DIR/data/$MYSQL_CA_NAME")" ]; then
    cp "$MYSQL_CA_FILE" "$DIR/data/$MYSQL_CA_NAME"
  fi
  if [ -f "$DIR/data/$MYSQL_CA_NAME" ]; then chmod 644 "$DIR/data/$MYSQL_CA_NAME"; fi
  if [ "$DRY_RUN" -eq 0 ]; then chown -R "$APP_UID:$APP_UID" "$DIR/data"; fi
  chmod 700 "$DIR/backups"
  ok "compose.yml, $SITE.caddy and .env"
}

# ---------------------------------------------------------------------------
# The shared Caddy

# Keep an installed `studioapps` if it's this version or newer: another app may have put it there.
edge_current() {
  [ -x "$STUDIOAPPS" ] || return 1
  local have
  have=$("$STUDIOAPPS" version 2>/dev/null || true)
  [ -n "$have" ] && [ "$(printf '%s\n%s\n' "$EDGE_VERSION" "$have" | sort -V | head -n 1)" = "$EDGE_VERSION" ]
}

install_edge() {
  step "The shared Caddy"
  if edge_current; then
    ok "The studioapps command is installed ($("$STUDIOAPPS" version))"
    return
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    say "${dim}(dry run: would install ${EDGE_SCRIPT:-$EDGE_URL} as $STUDIOAPPS)${reset}"
    return
  fi
  local fetched
  fetched=$(mktemp)
  if [ -n "$EDGE_SCRIPT" ]; then
    cp "$EDGE_SCRIPT" "$fetched"
  elif ! curl -fsSL --max-time 30 "$EDGE_URL" -o "$fetched"; then
    rm -f "${fetched:?}"
    die "Couldn't download the shared Caddy's script from $EDGE_URL"
  fi
  if ! printf '%s  %s\n' "$EDGE_SHA256" "$fetched" | sha256sum -c --quiet - >/dev/null 2>&1; then
    rm -f "${fetched:?}"
    die "The shared Caddy's script doesn't match the one this installer was tested with, so it wasn't used."
  fi
  install -m 0755 "$fetched" "$STUDIOAPPS"
  rm -f "${fetched:?}"
  ok "Installed the studioapps command ($EDGE_VERSION)"
}

# Start the shared Caddy, or update its files. It keeps an email it already has: Let's Encrypt
# notices are for the whole server, and another app may have set one.
ensure_edge() {
  local args=()
  if [ -n "$ACME_EMAIL" ] && ! grep -qs '^ACME_EMAIL=.' "$EDGE_DIR/.env"; then
    args=(--email "$ACME_EMAIL")
  fi
  "$STUDIOAPPS" edge ensure "${args[@]}"
}

install_command() {
  local bin=/usr/local/bin/ephemeral cron=/etc/cron.daily/ephemeral-backup
  if [ "$DRY_RUN" -eq 1 ]; then bin="$DIR/ephemeral"; cron="$DIR/ephemeral-backup.cron"; fi
  write_ctl > "$bin"
  chmod 755 "$bin"
  printf '#!/bin/sh\n# Daily ephemeralREST backup, keeping two weeks.\n%s backup --keep 14 --quiet\n' "$bin" > "$cron"
  chmod 755 "$cron"
  ok "Added the 'ephemeral' command and a daily backup"
}

# The `ephemeral` command installed on the server.
write_ctl() {
  printf '#!/usr/bin/env bash\nEPHEMERAL_DIR=%q\n' "$DIR"
  cat <<'EOF'
# Manage ephemeralREST. Installed by the ephemeralREST installer.
set -euo pipefail
cd "$EPHEMERAL_DIR"
compose() { docker compose --project-directory "$EPHEMERAL_DIR" "$@"; }
domain() { sed -n 's/^EPHEMERAL_DOMAIN=//p' .env; }
app() { compose exec -T app ephemeral "$@"; }

usage() {
  cat <<USAGE
Usage: ephemeral COMMAND

  status                     Is it running? Shows the address and version.
  logs                       Follow ephemeralREST's log (Ctrl+C to stop).
  logs caddy                 Follow the server's shared Caddy's log (certificates, errors).
  update                     Get the latest version and restart. Your data is backed up first.
  backup [--keep N]          Back up the database to $EPHEMERAL_DIR/backups.
  restore FILE               Replace the database with a backup (asks first).
  restart | stop | start
  reset-password EMAIL       Print a link to set a new password, for someone who's locked out.
  keys ...                   Manage keys from here: ephemeral keys --help
  fetch-ephemeris            Download any Swiss Ephemeris files that are missing (all of them, ~120 MB).
  import-cities              Download and import the GeoNames cities list again, with region names.
  cleanup ...                Remove old cached charts: ephemeral cleanup --help
  check-db                   Check the connection to the database (and, for MySQL, its TLS).
  version

The shared Caddy, which Studio apps on this server use too, has its own command:
studioapps sites | edge status | edge logs ephemeral
USAGE
}

backup() {
  local keep=0 quiet=0 stamp file
  while [ $# -gt 0 ]; do
    case $1 in --keep) keep=$2; shift 2 ;; --quiet) quiet=1; shift ;; *) shift ;; esac
  done
  if grep -q '^DB_TYPE=mysql' .env; then
    [ "$quiet" -eq 1 ] || echo "The database is MySQL: it's backed up by your MySQL provider (Vultr Managed MySQL does it automatically), or with mysqldump. Nothing else needs a backup: the ephemeris files and cities list can be downloaded again."
    return 0
  fi
  stamp=$(date +%Y-%m-%d-%H%M%S)
  app backup-db "/data/snapshot-$stamp.db" >/dev/null
  file="backups/ephemeral-$stamp.tar.gz"
  local parts=("snapshot-$stamp.db")
  for extra in federation api_usage_count.json; do
    if [ -e "data/$extra" ]; then parts+=("$extra"); fi
  done
  tar -czf "$file" -C data "${parts[@]}"
  rm -f "data/snapshot-$stamp.db"
  chmod 600 "$file"
  if [ "$keep" -gt 0 ]; then
    find backups -maxdepth 1 -name 'ephemeral-*.tar.gz' | sort -r | tail -n +"$((keep + 1))" | xargs -r rm -f
  fi
  [ "$quiet" -eq 1 ] || echo "Backed up to $EPHEMERAL_DIR/$file"
}

restore() {
  local file=${1:-} work answer
  [ -f "$file" ] || { echo "Usage: ephemeral restore backups/ephemeral-....tar.gz" >&2; exit 1; }
  printf 'This replaces the current database with %s. A backup of the current one is made first.\nType "restore" to continue: ' "$file"
  read -r answer
  [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
  backup
  work=$(mktemp -d)
  tar -xzf "$file" -C "$work"
  compose stop app
  rm -f data/ephemeral.db data/ephemeral.db-wal data/ephemeral.db-shm
  cp "$work"/snapshot-*.db data/ephemeral.db
  if [ -d "$work/federation" ]; then rm -rf data/federation && cp -r "$work/federation" data/federation; fi
  if [ -f "$work/api_usage_count.json" ]; then cp "$work/api_usage_count.json" data/; fi
  chown -R 10001:10001 data
  rm -rf "$work"
  compose start app
  echo "Restored. Open https://$(domain)"
}

case ${1:-help} in
  status)
    compose ps
    printf '\nAddress: https://%s   API: https://%s/api/v1\n' "$(domain)" "$(domain)"
    curl -fsS --max-time 5 "https://$(domain)/api/v1/ping" && echo || echo "Not answering at https://$(domain) yet."
    ;;
  logs)
    if [ "${2:-}" = "caddy" ]; then docker logs -f --tail 200 studioapps-edge
    else compose logs -f --tail 200 app
    fi ;;
  update)
    backup
    compose pull --ignore-pull-failures  # an image built on this server has nothing to pull
    compose up -d --remove-orphans
    docker image prune -f >/dev/null
    echo "Updated. The database is updated automatically on start."
    ;;
  backup) shift; backup "$@" ;;
  restore) shift; restore "$@" ;;
  restart) compose up -d --force-recreate app ;;  # recreated, so .env changes take effect
  stop) compose stop ;;
  start) compose up -d ;;
  reset-password) app reset-password "${2:?Usage: ephemeral reset-password EMAIL}" ;;
  keys|cleanup|fetch-ephemeris|import-cities|check-db) app "$@" ;;
  version) app version ;;
  help|--help|-h) usage ;;
  *) usage; exit 1 ;;
esac
EOF
}

# ---------------------------------------------------------------------------

# Start the app, then give the shared Caddy its site. Returns non-zero (after saying why) if either
# fails, so a move from the old setup can put that back.
start() {
  step "Starting ephemeralREST"
  if [ "$DRY_RUN" -eq 1 ]; then say "${dim}(dry run: would run docker compose pull and up, then studioapps site add)${reset}"; return; fi
  if ! (cd "$DIR" && docker compose pull --quiet --ignore-pull-failures); then
    warn "Couldn't get the ephemeralREST image ($IMAGE)."
    return 1
  fi
  check_database || return 1
  if ! (cd "$DIR" && docker compose up -d --remove-orphans); then
    warn "Starting ephemeralREST failed. See: cd $DIR && docker compose logs"
    return 1
  fi
  if ! "$STUDIOAPPS" site add "$SITE" --file "$DIR/$SITE.caddy"; then
    warn "The shared Caddy refused ephemeralREST's site file ($DIR/$SITE.caddy). Its message is above."
    return 1
  fi
}

# With MySQL, connect once before starting, so a wrong password, a missing trusted source or a
# certificate that doesn't match is one clear message rather than a container that keeps restarting.
check_database() {
  [ "$DB_TYPE" = mysql ] || return 0
  say "Checking the connection to MySQL at $MYSQL_HOST..."
  local out
  if out=$(cd "$DIR" && docker compose run --rm --no-deps -T app ephemeral check-db 2>&1); then
    printf '%s\n' "$out" | grep -v -e '^Note: no Swiss' -e '^ *Run .ephemeral fetch-ephemeris' || true
    return 0
  fi
  printf '%s\n' "$out" | grep -v -e '^Note: no Swiss' -e '^ *Run .ephemeral fetch-ephemeris' >&2 || true
  local ip
  ip=$(public_ip)
  warn "ephemeralREST can't use the MySQL database, so it wasn't started."
  say "Check the settings above. On Vultr Managed MySQL, the database's page lists its host, port,"
  say "user and password, and its ${bold}Trusted Sources${reset} must include this server${ip:+ ($ip)}."
  say "The settings are in $DIR/.env; run the installer again with the right --mysql-... options."
  return 1
}

# ephemeralREST used to run its own Caddy. Move it onto the shared Caddy, bringing its certificates
# (so Let's Encrypt isn't asked again), and put everything back if a step fails.
move_to_shared_caddy() {
  step "Moving ephemeralREST onto the server's shared Caddy"
  if [ "$DRY_RUN" -eq 1 ]; then
    say "${dim}(dry run: would back up, stop its own Caddy, copy its certificates and start behind the shared Caddy)${reset}"
    write_files
    return
  fi
  local keep old certs edge_was_running=0
  keep="$DIR/backups/before-shared-caddy-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$keep"
  chmod 700 "$keep"
  cp -p "$DIR/compose.yml" "$DIR/.env" "$keep/"
  if [ -f "$DIR/Caddyfile" ]; then cp -p "$DIR/Caddyfile" "$keep/"; fi
  if edge_running; then edge_was_running=1; fi

  # The data first, while the app is still running with the command from the old installer.
  if [ -x /usr/local/bin/ephemeral ] && /usr/local/bin/ephemeral backup --quiet >/dev/null 2>&1; then
    ok "Backed up the data to $DIR/backups"
  else
    warn "Couldn't make a data backup first (is ephemeralREST running?). Its data isn't touched by this move."
  fi

  # Its certificates and Caddy's account, from the old Caddy's storage.
  old=$(cd "$DIR" && docker compose ps -a -q caddy 2>/dev/null | head -n 1 || true)
  certs="$keep/caddy-data"
  if [ -n "$old" ] && docker cp "$old:/data/caddy" "$certs" >/dev/null 2>&1; then
    ok "Copied the certificates from ephemeralREST's own Caddy"
  else
    certs=""
    warn "Couldn't copy the old Caddy's certificates: the shared Caddy will get new ones."
  fi

  # Free ports 80 and 443 for the shared Caddy.
  (cd "$DIR" && docker compose stop caddy >/dev/null 2>&1) || true

  if ! ensure_edge; then
    put_back "$keep" "$edge_was_running" "The shared Caddy didn't start."
  fi
  # Into the shared Caddy's storage, never replacing anything it already has.
  if [ -n "$certs" ]; then
    mkdir -p "$EDGE_DIR/data/caddy"
    tar -C "$certs" -cf - . | tar -C "$EDGE_DIR/data/caddy" --skip-old-files -xf -
  fi

  write_files
  if ! start; then
    put_back "$keep" "$edge_was_running" "ephemeralREST didn't start behind the shared Caddy."
  fi
  rm -f "${DIR:?}/Caddyfile"
  ok "Moved onto the shared Caddy. The old setup is kept in $keep"
  MOVED_FROM_OWN_CADDY=1
}

# Undo a move that failed part-way: ephemeralREST runs with its own Caddy again, as before.
put_back() {
  local keep=$1 edge_was_running=$2 why=$3
  warn "$why Putting the old setup back..."
  if edge_running; then
    "$STUDIOAPPS" site remove "$SITE" >/dev/null 2>&1 || true
    if [ "$edge_was_running" -eq 0 ] && ! ls "$EDGE_DIR"/sites/*.caddy >/dev/null 2>&1; then
      docker stop "$EDGE_CONTAINER" >/dev/null 2>&1 || true   # it started for us alone; free the ports
    fi
  fi
  cp -p "$keep/compose.yml" "$keep/.env" "$DIR/"
  if [ -f "$keep/Caddyfile" ]; then cp -p "$keep/Caddyfile" "$DIR/"; fi
  rm -f "${DIR:?}/${SITE:?}.caddy"
  if [ "$edge_was_running" -eq 0 ] && (cd "$DIR" && docker compose up -d --remove-orphans >/dev/null 2>&1); then
    die "ephemeralREST is running as it was before, with its own Caddy. The problem is described above."
  fi
  die "Couldn't put ephemeralREST's own Caddy back. Its old files are in $DIR again, and copies are in $keep.
Fix the problem described above, then run the installer again."
}

wait_until_answering() {
  [ "$DRY_RUN" -eq 1 ] && return
  say "Waiting for the HTTPS certificate (usually under a minute)..."
  local i
  for i in $(seq 1 60); do
    if curl -fsS --max-time 5 "https://$DOMAIN/api/v1/ping" >/dev/null 2>&1; then
      ok "Running at https://$DOMAIN"
      return
    fi
    sleep 3
    [ $((i % 10)) -eq 0 ] && say "${dim}still waiting...${reset}"
  done
  warn "ephemeralREST started, but https://$DOMAIN isn't answering yet."
  say "Check the domain's A record, and that ports 80 and 443 are open in your provider's firewall."
  say "Then see what the shared Caddy says: ephemeral logs caddy"
}

download_data() {
  step "Ephemeris files and cities list"
  if [ "$NO_DATA" -eq 1 ]; then say "${dim}(skipped — later: ephemeral fetch-ephemeris; ephemeral import-cities)${reset}"; return; fi
  if [ "$DRY_RUN" -eq 1 ]; then say "${dim}(dry run: would download them)${reset}"; return; fi
  local app="docker compose --project-directory $DIR exec -T app ephemeral"
  # Every Swiss Ephemeris file (about 120 MB: 13201 BC to 17191 AD). Files already there are
  # kept, so an install that has the 1800–2399 files only gets the rest.
  $app fetch-ephemeris || warn "Some ephemeris files didn't download. Try again later: ephemeral fetch-ephemeris"
  local stats loaded regions
  stats=$(curl -fsS --max-time 10 "https://$DOMAIN/api/v1/cache/stats" 2>/dev/null || true)
  loaded=$(printf '%s' "$stats" | grep -o '"cities_loaded": *[0-9]*' | grep -o '[0-9]*$' || echo 0)
  regions=$(printf '%s' "$stats" | grep -o '"cities_regions": *[a-z]*' | grep -o '[a-z]*$' || echo false)
  if [ "${loaded:-0}" -gt 0 ] && [ "$regions" = true ]; then
    ok "Cities list already imported ($loaded places)"
  else
    if [ "${loaded:-0}" -gt 0 ]; then
      say "Importing the cities list again, with the names of states, regions and countries (a minute or two)..."
    else
      say "Importing the cities list (a minute or two)..."
    fi
    $app import-cities >/dev/null || warn "Couldn't import the cities list. Try later: ephemeral import-cities"
    ok "Cities list imported"
  fi
}

finish() {
  printf '\n%sephemeralREST is installed.%s\n\n' "$bold" "$reset"
  if [ "$EXISTING" -eq 1 ]; then
    say "  Open ${bold}https://$DOMAIN${reset} and log in as usual."
  else
    say "  Open ${bold}https://${ADMIN_DOMAIN:-$DOMAIN}${reset} to create the administrator account. Do it now:"
    say "  the first person to open it becomes the administrator."
  fi
  say "  The API is at ${bold}https://$DOMAIN/api/v1${reset}."
  if [ -n "$ADMIN_DOMAIN" ]; then say "  Administration: ${bold}https://$ADMIN_DOMAIN${reset} (only there)."; fi
  say ""
  say "  Useful commands:  ephemeral status | logs | update | backup"
  say "  Settings:         $DIR/.env  (then: ephemeral restart)"
  if [ "$DB_TYPE" = mysql ]; then
    say "  Your data:        MySQL at $MYSQL_HOST, database $MYSQL_DATABASE (backed up by your provider)"
  else
    say "  Your data:        $DIR/data   (backed up daily to $DIR/backups)"
  fi
  say "  Shared Caddy:     studioapps sites   (Studio apps can share this server)"
  if [ "$MOVED_FROM_OWN_CADDY" -eq 1 ]; then
    say ""
    say "  The old Caddy's storage is kept until you're happy. To remove it:"
    say "    docker volume rm ephemeral_caddy_data ephemeral_caddy_config"
  fi
  say ""
}

main() {
  MOVED_FROM_OWN_CADDY=0
  parse_args "$@"
  load_existing
  printf '%sephemeralREST installer%s\n' "$bold" "$reset"
  check_system
  check_internet
  choose_domain
  choose_geocoding
  choose_database
  install_docker
  check_ports
  check_dns
  install_edge
  if [ "$OWN_CADDY" -eq 1 ]; then
    move_to_shared_caddy
  else
    if [ "$DRY_RUN" -eq 0 ]; then
      step "Starting the shared Caddy"
      ensure_edge || die "The shared Caddy didn't start. Its message is above."
    fi
    write_files
    start || die "Nothing else on the server was changed. Fix the problem above, then run the installer again."
  fi
  install_command
  wait_until_answering
  download_data
  finish
}

main "$@"
