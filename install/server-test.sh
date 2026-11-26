#!/usr/bin/env bash
# ephemeralREST · install/server-test.sh
#
# Runs the real installer against a real Docker and the server's shared Caddy (studio-edge).
# It takes over ports 80 and 443, /opt/studioapps, /opt/ephemeral, the studioapps network and
# /etc/hosts entries, so run it ONLY on a throwaway VM or CI runner, as root:
#
#   sudo install/server-test.sh --edge-script ../studio-edge/edge.sh --i-understand
#
# What it checks:
#   - a fresh install, with an admin address: the shared Caddy serves both over HTTPS
#   - running the installer again changes nothing
#   - another app's site on the same Caddy, in either order, and both keep answering
#   - an install with its own Caddy (in /opt/ephemeral) moves across with the same certificate
#     and its data, and a failed move puts the old setup back
#   - something else on port 80 stops the installer before it changes anything
#
# Settings (environment):
#   EPH_IMAGE    the ephemeralREST image (default ghcr.io/gmelh/ephemeralrest:1)
#   STUDIOAPPS_EDGE_IMAGE   the Caddy image for the shared Caddy (default: edge.sh's, caddy:2.10)
#   OLD_REF      the git ref of the installer that ran its own Caddy (default d6ed606)
#   KEEP=1       leave the server as the last check left it, to look around (no clean-up)
#
# Certificates come from Caddy's own local authority (`tls internal`, through --caddy-extra), so
# no real domain or Let's Encrypt is needed; the test domains are put in /etc/hosts.

# Checks below are written "A && B || bad": both must hold, so SC2015 is what we mean.
# shellcheck disable=SC2015
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
INSTALLER="$HERE/install.sh"
EDGE_SCRIPT=""
SURE=0
while [ $# -gt 0 ]; do
  case $1 in
    --edge-script) EDGE_SCRIPT=$(cd "$(dirname "$2")" && printf '%s/%s' "$PWD" "$(basename "$2")"); shift 2 ;;
    --i-understand) SURE=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$SURE" -eq 1 ] || { sed -n '4,9p' "$0" | sed 's/^# \{0,1\}//'; echo "Add --i-understand to run it." >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "Run it as root." >&2; exit 2; }
[ -n "$EDGE_SCRIPT" ] && [ -f "$EDGE_SCRIPT" ] || { echo "Give studio-edge's edge.sh with --edge-script." >&2; exit 2; }

EPH_IMAGE="${EPH_IMAGE:-ghcr.io/gmelh/ephemeralrest:1}"
EDGE_IMAGE="${STUDIOAPPS_EDGE_IMAGE:-caddy:2.10}"
export STUDIOAPPS_EDGE_IMAGE="$EDGE_IMAGE"
OLD_REF="${OLD_REF:-d6ed606}"
DOMAIN="api.example.test"
ADMIN="admin.example.test"
OTHER="other.example.test"
NEW_DIR=/opt/studioapps/ephemeral
OLD_DIR=/opt/ephemeral
WORK="$(mktemp -d)"
PASSED=0
# The test addresses are on this machine: never send them through an outbound proxy.
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$DOMAIN,$ADMIN,$OTHER,127.0.0.1"
export no_proxy="${no_proxy:+$no_proxy,}$DOMAIN,$ADMIN,$OTHER,127.0.0.1"

ok() { PASSED=$((PASSED + 1)); printf '  \033[32mok\033[0m %s\n' "$*"; }
bad() { printf '  \033[31mFAILED\033[0m %s\n' "$*" >&2; exit 1; }

# Test certificates come from Caddy's local authority, which curl doesn't know: the installer's
# own check reads this curlrc (through HOME), and so do the checks below.
mkdir -p "$WORK/home"
printf 'insecure\n' > "$WORK/home/.curlrc"
printf 'tls internal\n' > "$WORK/tls-internal.caddy"

install_eph() { # install_eph ARGS...: run the installer as people do, piped into bash
  HOME="$WORK/home" bash "$INSTALLER" --yes --skip-dns-check --no-data --image "$EPH_IMAGE" \
    --edge-script "$EDGE_SCRIPT" --caddy-extra "$WORK/tls-internal.caddy" "$@" < /dev/null
}
ping_at() { curl -sk --max-time 5 --resolve "$1:443:127.0.0.1" "https://$1/api/v1/ping"; }
answers() { grep -q '"status": *"ok"' <<<"$(ping_at "$1")"; }
fingerprint() {
  openssl s_client -connect 127.0.0.1:443 -servername "$1" </dev/null 2>/dev/null |
    openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2
}
other_app() { # a tiny second "Studio app" on the shared Caddy
  docker rm -f t-other-app >/dev/null 2>&1 || true
  docker run -d --name t-other-app --network studioapps --network-alias t-other-app "$EDGE_IMAGE" \
    caddy respond --listen :8080 "other app" >/dev/null
  printf 'http://%s {\n\timport studio other\n\treverse_proxy t-other-app:8080\n}\n' "$OTHER" > "$WORK/other.caddy"
  studioapps site add other --file "$WORK/other.caddy" >/dev/null
}
answers_soon() { # answers_soon DOMAIN: up to 30 seconds for it to answer over HTTPS
  for _ in $(seq 1 30); do answers "$1" && return 0; sleep 1; done
  return 1
}
other_answers() { [ "$(curl -s --max-time 5 -H "Host: $OTHER" http://127.0.0.1/)" = "other app" ]; }

reset_server() {
  for dir in "$NEW_DIR" "$OLD_DIR"; do
    [ -f "$dir/compose.yml" ] && docker compose --project-directory "$dir" down -v --remove-orphans >/dev/null 2>&1 || true
  done
  docker rm -f studioapps-edge t-other-app t-squatter >/dev/null 2>&1 || true
  docker volume rm ephemeral_caddy_data ephemeral_caddy_config >/dev/null 2>&1 || true
  docker network rm studioapps >/dev/null 2>&1 || true
  rm -rf /opt/studioapps "$OLD_DIR"
  rm -f /usr/local/bin/studioapps /usr/local/bin/ephemeral /etc/cron.daily/ephemeral-backup
}
cleanup() {
  reset_server
  sed -i "/ $DOMAIN\$/d; / $ADMIN\$/d; / $OTHER\$/d" /etc/hosts
  rm -rf "${WORK:?}"
}
[ "${KEEP:-0}" = 1 ] || trap cleanup EXIT

reset_server
sed -i "/ $DOMAIN\$/d; / $ADMIN\$/d; / $OTHER\$/d" /etc/hosts
printf '127.0.0.1 %s\n127.0.0.1 %s\n127.0.0.1 %s\n' "$DOMAIN" "$ADMIN" "$OTHER" >> /etc/hosts

echo "fresh install"
install_eph --domain "$DOMAIN" --admin-domain "$ADMIN" --email ops@example.com > "$WORK/fresh.log" 2>&1 ||
  { cat "$WORK/fresh.log"; bad "a fresh install should work"; }
answers "$DOMAIN" || bad "ephemeralREST should answer over HTTPS"
answers "$ADMIN" || bad "the admin address should answer too"
[ "$(studioapps version)" = "$(sed -n 's/^EDGE_VERSION="\(.*\)"/\1/p' "$EDGE_SCRIPT")" ] || bad "studioapps should be installed"
[ -f "$NEW_DIR/compose.yml" ] && ! grep -q '^  caddy:' "$NEW_DIR/compose.yml" || bad "a new install goes in $NEW_DIR, without a Caddy of its own"
[ ! -e "$NEW_DIR/Caddyfile" ] || bad "there's no Caddyfile any more"
grep -q "email ops@example.com" /opt/studioapps/edge/Caddyfile || bad "the email should reach the shared Caddy"
grep -q "^ADMIN_DOMAIN=$ADMIN\$" "$NEW_DIR/.env" || bad "the admin address should be kept in .env"
[ "$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' ephemeral-app-1 | tr ' ' '\n' | grep -c .)" = 2 ] ||
  bad "the app should be on its own network and studioapps"
[ "$(docker exec ephemeral-app-1 env | grep -c '^TRUSTED_PROXIES=172.31.250.2$')" = 1 ] ||
  bad "the app should believe forwarded addresses from the shared Caddy only"
main_admin=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/v1/admin/sites")
admin_admin=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$ADMIN:443:127.0.0.1" "https://$ADMIN/api/v1/admin/sites")
[ "$main_admin" = 404 ] && [ "$admin_admin" = 401 ] ||
  bad "administration should answer only at the admin address (main $main_admin, admin $admin_admin)"
ok "a fresh install comes up behind the shared Caddy, over HTTPS, at both addresses"
ok "administration answers only at the admin address"
grep -q "Running at https://$DOMAIN" "$WORK/fresh.log" || bad "the installer should have seen it answering"
ok "the installer's own check saw it answering"

first=$(docker inspect -f '{{.Id}}' ephemeral-app-1)
secret=$(grep '^SECRET_KEY=' "$NEW_DIR/.env")
install_eph > "$WORK/again.log" 2>&1 || { cat "$WORK/again.log"; bad "running it again should work"; }
[ "$(docker inspect -f '{{.Id}}' ephemeral-app-1)" = "$first" ] || bad "running it again shouldn't recreate the app"
[ "$(grep '^SECRET_KEY=' "$NEW_DIR/.env")" = "$secret" ] || bad "the secret key must never change"
answers "$DOMAIN" && answers "$ADMIN" || bad "it should still answer"
ok "running the installer again keeps the addresses and secret key, and changes nothing"

echo "sharing the server"
other_app
other_answers && answers "$DOMAIN" || bad "both apps should answer"
ok "another app's site added afterwards: both answer"
install_eph > "$WORK/third.log" 2>&1 || { cat "$WORK/third.log"; bad "re-running beside another app should work"; }
other_answers || bad "re-running ephemeralREST's installer shouldn't touch the other app"
ok "re-running ephemeralREST's installer leaves the other app alone"

echo "another app first"
reset_server
install -m 0755 "$EDGE_SCRIPT" /usr/local/bin/studioapps
studioapps edge ensure >/dev/null
other_app
install_eph --domain "$DOMAIN" > "$WORK/second.log" 2>&1 || { cat "$WORK/second.log"; bad "installing beside another app should work"; }
other_answers && answers "$DOMAIN" || bad "both apps should answer"
ok "installed after another app: both answer"

echo "moving an install that ran its own Caddy"
reset_server
old_install() { # old_install: an install made by the installer that ran its own Caddy
  git -C "$REPO" show "$OLD_REF:install/install.sh" > "$WORK/old-install.sh"
  HOME="$WORK/home" bash "$WORK/old-install.sh" --dry-run --yes --dir "$OLD_DIR" --domain "$DOMAIN" \
    --image "$EPH_IMAGE" < /dev/null > "$WORK/old.log" 2>&1 || { cat "$WORK/old.log"; bad "the old installer's files"; }
  sed -i "s|image: caddy:2|image: $EDGE_IMAGE|" "$OLD_DIR/compose.yml"
  sed -i 's|^\treverse_proxy app:8080|\ttls internal\n\treverse_proxy app:8080|' "$OLD_DIR/Caddyfile"
  install -m 0755 "$OLD_DIR/ephemeral" /usr/local/bin/ephemeral
  chown -R 10001:10001 "$OLD_DIR/data"
  docker compose --project-directory "$OLD_DIR" up -d >/dev/null 2>&1
  answers_soon "$DOMAIN" || bad "the old-style install should answer"
}
old_install
old_cert=$(fingerprint "$DOMAIN")
[ -n "$old_cert" ] || bad "the old install should serve a certificate"
old_secret=$(grep '^SECRET_KEY=' "$OLD_DIR/.env")
touch "$OLD_DIR/data/kept-by-the-move"
chown 10001:10001 "$OLD_DIR/data/kept-by-the-move"
ok "an install made by the $OLD_REF installer, with its own Caddy"

install_eph > "$WORK/move.log" 2>&1 || { cat "$WORK/move.log"; bad "the move should work"; }
answers "$DOMAIN" || bad "it should answer after the move"
[ "$(fingerprint "$DOMAIN")" = "$old_cert" ] || bad "the shared Caddy should serve the same certificate"
ok "moved onto the shared Caddy with the same certificate"
[ -f "$OLD_DIR/data/kept-by-the-move" ] && [ ! -e "$NEW_DIR" ] || bad "it should stay in $OLD_DIR with its data"
[ "$(grep '^SECRET_KEY=' "$OLD_DIR/.env")" = "$old_secret" ] || bad "the secret key must be kept"
[ ! -e "$OLD_DIR/Caddyfile" ] && ! grep -q '^  caddy:' "$OLD_DIR/compose.yml" || bad "its own Caddy should be gone"
[ -z "$(docker ps -aq --filter label=com.docker.compose.project=ephemeral --filter label=com.docker.compose.service=caddy)" ] ||
  bad "the old Caddy container should be removed"
docker volume inspect ephemeral_caddy_data >/dev/null 2>&1 || bad "the old Caddy's storage is kept until the owner removes it"
ls "$OLD_DIR"/backups/before-shared-caddy-*/Caddyfile >/dev/null 2>&1 || bad "the old setup should be kept in backups/"
ls "$OLD_DIR"/backups/ephemeral-*.tar.gz >/dev/null 2>&1 || bad "the data should be backed up first"
grep -q "docker volume rm ephemeral_caddy_data" "$WORK/move.log" || bad "the installer should say how to remove the old storage"
ok "it stays in $OLD_DIR with its data and secret key, backed up first, and the old setup kept"
other_app
other_answers && answers "$DOMAIN" || bad "another app should be able to join"
ok "after the move, another app can share the server"

echo "a move that fails is put back"
reset_server
old_install
old_cert=$(fingerprint "$DOMAIN")
if install_eph --image local-test/does-not-exist:0 > "$WORK/fail.log" 2>&1; then bad "a move with a missing image should stop"; fi
grep -q "running as it was before, with its own Caddy" "$WORK/fail.log" || { cat "$WORK/fail.log"; bad "it should say it put things back"; }
answers_soon "$DOMAIN" && [ "$(fingerprint "$DOMAIN")" = "$old_cert" ] || bad "the old install should answer again, as before"
grep -q '^  caddy:' "$OLD_DIR/compose.yml" && [ -f "$OLD_DIR/Caddyfile" ] || bad "its old files should be back"
[ "$(docker inspect -f '{{.State.Running}}' studioapps-edge 2>/dev/null || echo false)" = false ] ||
  bad "the shared Caddy, started for this move alone, should be stopped again"
ok "a failed move puts the old setup back, answering with its own Caddy"

echo "something else on port 80"
reset_server
docker run -d --name t-squatter -p 80:80 "$EDGE_IMAGE" caddy respond --listen :80 squatter >/dev/null
if install_eph --domain "$DOMAIN" > "$WORK/squat.log" 2>&1; then bad "it should stop"; fi
grep -q "t-squatter" "$WORK/squat.log" && grep -q "Nothing has been changed" "$WORK/squat.log" || { cat "$WORK/squat.log"; bad "it should name what holds the port"; }
[ ! -e "$NEW_DIR" ] && [ ! -e /usr/local/bin/studioapps ] || bad "nothing should be installed"
ok "another container on port 80 stops it before anything is installed"
docker rm -f t-squatter >/dev/null

echo
echo "All $PASSED installer checks passed."
