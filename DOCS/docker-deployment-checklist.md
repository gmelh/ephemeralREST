# Deploying ephemeralREST + ephemeralADMIN with Docker — Step-by-Step Checklist

This is a complete, ordered runbook for taking both projects from git to a
running, HTTPS-secured deployment on a VPS. Each phase ends with a
**Checkpoint** you can verify before moving on — if a checkpoint fails,
stop and fix it there rather than continuing.

This assumes: a fresh Ubuntu 22.04/24.04 (or Debian 12) VPS, root or sudo
access, and MySQL already installed directly on that same VPS. If your
MySQL is a separate managed database instead, skip the MySQL networking
steps in Phase 4 and just use its hostname directly.

---

## Prerequisites checklist

Before starting, have these ready:

- [ ] SSH access to the VPS
- [ ] Two domain names (or subdomains) you control DNS for — one for the
      API, one for the admin portal (e.g. `api.yourdomain.com`,
      `admin.yourdomain.com`)
- [ ] Access to both git repositories (ephemeralREST, ephemeralADMIN)
- [ ] A Google Maps API key, if you're using Google-based geocoding
      (optional — the API works without it)

---

## Phase 1 — Base VPS setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ufw
```

Install Docker Engine + Compose plugin (official Docker install script is
the simplest path):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in (or `newgrp docker`) so your user picks up the docker
group membership.

**Checkpoint:**
```bash
docker --version
docker compose version
```
Both should print version numbers without error.

---

## Phase 2 — Get the code

```bash
mkdir -p ~/ephemeral && cd ~/ephemeral
git clone <your-ephemeralREST-repo-url> ephemeralREST
git clone <your-ephemeralADMIN-repo-url> ephemeralADMIN
```

This layout matters — `ephemeralREST/docker-compose.yml` builds the portal
via a relative path to its sibling:

```
~/ephemeral/
  ephemeralREST/    ← docker-compose.yml lives here; you'll run docker compose from here
  ephemeralADMIN/
```

**Checkpoint:**
```bash
ls ~/ephemeral/ephemeralREST/docker-compose.yml
ls ~/ephemeral/ephemeralADMIN/Dockerfile
```
Both should exist.

---

## Phase 3 — Swiss Ephemeris data files

The API needs the `.se1` ephemeris data files present before chart
calculations will work. **The container will start fine and pass its
health check without them** — only actual calculations fail, silently
returning `null` for every planet. Don't mistake a healthy container for
a working one; this step is easy to skip without noticing.

```bash
cd ~/ephemeral/ephemeralREST/sweph
curl -L -O https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/sepl_18.se1
curl -L -O https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/semo_18.se1
curl -L -O https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/seas_18.se1
```
(The original `astro.com/ftp/sweph/ephe/` path no longer serves these
directly — this GitHub mirror is the current, verified-working source.)

**Checkpoint:**
```bash
ls -la sweph/*.se1
```
Should list all three files, each a real size (hundreds of KB to a
couple MB — not a few bytes, which would mean the download silently
saved an error page instead of the actual file). Then actually verify
against a real calculation, not just file presence:
```bash
docker compose exec ephemeral-rest python3 -c "
import swisseph as swe
swe.set_ephe_path('/app/sweph')
print(swe.calc_ut(2440587.5, swe.SUN, swe.FLG_SWIEPH))
"
```
Should print real coordinates, not raise an exception.

---

## Phase 4 — MySQL setup (same-VPS installation)

Skip this whole phase if you're pointing at a separate managed database —
just note its hostname for Phase 7.

A Docker container's "localhost" is itself, not the host machine, so
MySQL needs to be reachable a different way, and that requires opening it
up more than its default configuration allows. Do these steps in order —
step 3 (the firewall rule) is not optional once you do step 2.

**1. Point MySQL at all interfaces**, not just loopback:

```bash
sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf
```

Find `bind-address = 127.0.0.1` and change it to:

```
bind-address = 0.0.0.0
```

```bash
sudo systemctl restart mysql
```

**2. Firewall port 3306 — but scoped, not blanket.** MySQL is now
listening on the VPS's public interface too, and nothing outside this VPS
should ever reach it directly — but a bare `ufw deny 3306` would *also*
block the one connection that's actually supposed to get through: the
`ephemeral-rest` container talking to MySQL via `host.docker.internal`.
That connection arrives at the host on the Docker bridge network, which a
blanket deny doesn't distinguish from the public internet.

`docker-compose.yml` pins the Compose network to a fixed subnet
(`172.28.0.0/16`) specifically so this rule can be written now, correctly
scoped, even though the network itself doesn't exist yet until Phase 10's
first `docker compose up`. Add the allow rule *before* the deny rule —
`ufw` evaluates top-down, first match wins:

```bash
sudo ufw allow from 172.28.0.0/16 to any port 3306
sudo ufw deny 3306
```

(If your VPS provider uses a separate cloud firewall/security group
instead of, or in addition to, `ufw`, block port 3306 there as well — that
layer only needs to block the public internet too, not the Docker bridge,
which never leaves the VPS.)

**3. Create the database and a user Docker can actually connect as.**
`'ephemeral'@'localhost'` won't match a connection arriving from the
Docker bridge network — it needs `'%'` (any host), which is safe here
specifically because you just firewalled 3306 from the public internet:

```bash
sudo mysql -u root -p
```
```sql
CREATE DATABASE ephemeral CHARACTER SET utf8mb4;
CREATE USER 'ephemeral'@'%' IDENTIFIED BY 'choose-a-strong-password-here';
GRANT ALL PRIVILEGES ON ephemeral.* TO 'ephemeral'@'%';
FLUSH PRIVILEGES;
EXIT;
```

**Checkpoint:**
```bash
sudo ss -ltnp | grep 3306
```
Should show MySQL listening on `0.0.0.0:3306` (not `127.0.0.1:3306`).
```bash
sudo ufw status numbered | grep 3306
```
Should show two rules — `ALLOW` for `172.28.0.0/16` listed *above* the
`DENY` rule (order matters; if `DENY` appears first, delete both with
`sudo ufw delete <number>` and re-add in the order shown above).

---

## Phase 5 — Firewall for incoming traffic

Set this up now, before DNS and certbot — not after. Certbot's
`--standalone` mode (used in Phase 8) needs port 80 reachable from the
internet to complete Let's Encrypt's challenge; if the firewall isn't
already open by then, that step times out. This also isn't just your own
VPS's OS-level firewall — most VPS providers additionally enforce a
*separate*, network-level firewall (Vultr's "Firewall Groups,"
DigitalOcean's "Cloud Firewalls," AWS "Security Groups," etc.) that's
invisible from inside the machine entirely; check your provider's control
panel too, not just the commands below.

```bash
sudo ufw allow 22/tcp      # don't lock yourself out of SSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Port 8080 (the no-domain quick-start portal port) doesn't need to be
opened externally if you're only accessing the portal via its real domain
on 443 — leave it closed unless you specifically want that fallback
reachable from outside the VPS too.

**Checkpoint:**
```bash
sudo ufw status
```
Should show 22, 80, and 443 as `ALLOW`, plus the two 3306 rules from
Phase 4 (`ALLOW` for `172.28.0.0/16`, `DENY` for everything else).

Then test reachability from *outside* the VPS — testing against
`localhost` on the box itself will falsely succeed even if external
traffic is actually blocked:
```bash
# from your own laptop, not the VPS:
curl -v http://<VPS public IP>
```
A connection timeout here (as opposed to a fast "connection refused," or
an HTTP response of any kind) usually means the provider's separate
network-level firewall, not `ufw`, is still blocking it — nothing's
listening on 80 yet at this point in the checklist anyway, so what you're
really testing is just "can traffic reach this box on this port at all."

---

## Phase 6 — DNS

Point A records for both domains at this VPS's public IP address, in
whatever DNS provider you use for the domain:

| Type | Name | Value |
|---|---|---|
| A | `api.yourdomain.com` | `<VPS public IP>` |
| A | `admin.yourdomain.com` | `<VPS public IP>` |

**Checkpoint:** wait for propagation, then confirm both resolve to your
VPS from the VPS itself:

```bash
dig +short api.yourdomain.com
dig +short admin.yourdomain.com
```
Both should print your VPS's IP. Don't move on to Phase 8 (certbot) until
this checkpoint passes — certbot's domain validation will fail otherwise.

---

## Phase 7 — Environment configuration

```bash
cd ~/ephemeral/ephemeralREST
```

Generate a secret key:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Create `.env`:

```bash
nano .env
```

```bash
SECRET_KEY=<paste the generated value>
USE_GOOGLE=false                        # or true, with GOOGLE_MAPS_API_KEY set below
GOOGLE_MAPS_API_KEY=

DB_TYPE=mysql
MYSQL_HOST=host.docker.internal
MYSQL_PORT=3306
MYSQL_USER=ephemeral
MYSQL_PASSWORD=<the password you set in Phase 4>
MYSQL_DATABASE=ephemeral

PORTAL_URL=https://admin.yourdomain.com
CORS_ORIGINS=https://admin.yourdomain.com

FLASK_DEBUG=False
```

(If MySQL is a separate managed server instead, set `MYSQL_HOST` to its
real hostname rather than `host.docker.internal`.)

**Checkpoint:**
```bash
cat .env | grep SECRET_KEY
```
Should show a 64-character hex string, not blank.

---

## Phase 8 — Certificates (Let's Encrypt / certbot)

nginx isn't running yet, so `--standalone` (which briefly binds port 80
itself) is the simplest way to get the first certificate — one
certificate covering both domains as SANs:

```bash
sudo apt install -y certbot
sudo mkdir -p /srv/ephemeral/certbot-webroot

sudo certbot certonly --standalone \
    -d api.yourdomain.com \
    -d admin.yourdomain.com
```

Follow the prompts (email address, terms agreement). Certbot stores the
result under the *first* domain listed:
`/etc/letsencrypt/live/api.yourdomain.com/`.

**Checkpoint:**
```bash
sudo ls /etc/letsencrypt/live/api.yourdomain.com/
```
Should list `fullchain.pem` and `privkey.pem`.

Set up automatic renewal now, so you don't forget later — renewals use
the webroot method (via the ACME-challenge location already configured in
`nginx.conf`), which doesn't require any downtime:

```bash
sudo certbot certonly --webroot -w /srv/ephemeral/certbot-webroot \
    -d api.yourdomain.com -d admin.yourdomain.com \
    --deploy-hook "docker compose -f $HOME/ephemeral/ephemeralREST/docker-compose.yml restart nginx"
```

This both switches the saved renewal config to the webroot method and
registers the deploy hook, so certbot's own systemd timer (installed
automatically with the package) picks it up going forward without
further action from you.

---

## Phase 9 — Point the config at your real domains

`nginx.conf` ships with placeholder domains. Replace them with your real
ones (each appears twice — once in the HTTP→HTTPS redirect block, once in
the HTTPS server block):

```bash
cd ~/ephemeral/ephemeralREST
sed -i 's/api\.yourdomain\.com/api.YOURACTUALDOMAIN.com/g; s/admin\.yourdomain\.com/admin.YOURACTUALDOMAIN.com/g' nginx.conf
```

(Replace `YOURACTUALDOMAIN.com` with your real domain, or just edit
`nginx.conf` directly in an editor if you'd rather see the changes as you
make them.)

**Checkpoint:**
```bash
grep -c "yourdomain.com" nginx.conf
```
Should print `0` — if it doesn't, the placeholder text is still in there
somewhere.

---

## Phase 10 — Build and start

```bash
cd ~/ephemeral/ephemeralREST
docker compose up -d --build
```

This builds the `ephemeral-rest` image, builds the `ephemeral-admin`
image (from `../ephemeralADMIN`), pulls `nginx:alpine`, and starts all
three.

**Checkpoint:**
```bash
docker compose ps
```
All three services (`ephemeral-rest`, `ephemeral-admin`, `ephemeral-nginx`)
should show `running` / `healthy`.

```bash
docker compose logs ephemeral-rest --tail 30
```
Look for `Database initialized successfully (mysql)` and no tracebacks.
If you see a MySQL connection error here, go back to Phase 4's checkpoint.

---

## Phase 11 — First admin key

```bash
docker compose exec ephemeral-rest python3 key_manager.py create
```

Follow the interactive prompts — choose `admin` where asked, and set a
password when prompted (this lets you log in immediately without needing
email verification).

**Checkpoint:** the command prints the generated API key — save it
somewhere safe, it's shown only once.

---

## Phase 12 — Verify the whole stack

```bash
curl https://api.yourdomain.com/health
```
Expect a JSON response, not a connection error or certificate warning.

```bash
curl -I https://admin.yourdomain.com/login.php
```
Expect `HTTP/2 200`.

Then in a browser:

1. Go to `https://admin.yourdomain.com` — should load without a
   certificate warning.
2. Log in with the admin key's identifier + password from Phase 11.
3. Create a test API key from the portal.
4. Confirm that key works against the API directly:
   ```bash
   curl -H "X-API-Key: <the test key>" https://api.yourdomain.com/health
   ```

If all four of those succeed, the deployment is fully working end to end.

---

## Phase 13 — Build once, tag it, push it to a registry

This is the step that lets production run the *exact* image you just
verified, rather than an independently-rebuilt copy that merely uses the
same source code. Skip this (and Phase 14) if this test box is simply
going to become production in place — in that case you're already done.

You'll need a container registry — Docker Hub, GitHub Container Registry,
or a private one all work identically for what follows; only the hostname
changes. This example uses `$REGISTRY` as a stand-in for whichever you
pick (e.g. `docker.io/yourusername` or `ghcr.io/yourusername`).

Tag with something immutable, not just `latest` — `latest` gets
overwritten by definition, so it can't be what you point production at if
you want a durable guarantee that it's running what you tested. The short
git commit hash is a convenient, traceable choice:

```bash
cd ~/ephemeral/ephemeralREST

export REGISTRY=docker.io/yourusername    # substitute your actual registry
export VERSION=$(git rev-parse --short HEAD)

docker login $REGISTRY

docker build -t $REGISTRY/ephemeral-rest:$VERSION \
             -t $REGISTRY/ephemeral-rest:latest .

docker build -t $REGISTRY/ephemeral-admin:$VERSION \
             -t $REGISTRY/ephemeral-admin:latest \
             ../ephemeralADMIN

docker push $REGISTRY/ephemeral-rest:$VERSION
docker push $REGISTRY/ephemeral-rest:latest
docker push $REGISTRY/ephemeral-admin:$VERSION
docker push $REGISTRY/ephemeral-admin:latest
```

**Checkpoint:**
```bash
echo $VERSION
```
Note this value down — you'll use it explicitly in Phase 14 rather than
relying on `latest`, precisely so a later re-push from further testing
can't silently change what production is running.

---

## Phase 14 — Deploy the same image to production

Production does **not** need the application source checkout at all for
building — no `build:` context is used — but it does still need
`docker-compose.prod.yml`, `nginx.conf`, its own `.env`, and its own
`sweph/` data files, since none of those are baked into the image. The
simplest way to get all of that onto the box is still a `git clone`; you
just won't run a build from it.

On the **production** VPS, repeat Phases 1, 3, 4, 5, 7, 8, and 9 from this
checklist exactly as before — same steps, but this is a genuinely separate
server with its own MySQL (or managed database), its own domains, and its
own firewall. **Do not point production at the same database you used for
testing** — every key, chart, and admin account created while testing
would become live production data.

Then, instead of Phase 7's plain `.env`, add the registry/version
variables so `docker-compose.prod.yml` knows what to pull:

```bash
cd ~/ephemeral/ephemeralREST
nano .env
```
```bash
REGISTRY=docker.io/yourusername
IMAGE_TAG=<the $VERSION value from Phase 13's checkpoint>
# ...plus everything else from Phase 7: SECRET_KEY, DB_TYPE, MYSQL_*,
# PORTAL_URL, CORS_ORIGINS — all production-specific values, not copied
# from the test box's .env.
```

```bash
docker login $REGISTRY
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Then run Phase 11 (create an admin key — production's database starts
empty, it doesn't inherit the test box's keys) and Phase 12
(verification) again, against production's own domains.

**Checkpoint:**
```bash
docker compose -f docker-compose.prod.yml images
```
Confirm the `ephemeral-rest` and `ephemeral-admin` rows show the
`$VERSION` tag from Phase 13, not `latest` and not a locally-built image
ID with no registry prefix — that would mean it built rather than pulled.

**Redeploying later**, once you've tested a new version: repeat Phase 13
with a new commit (new `$VERSION`), update `IMAGE_TAG` in production's
`.env`, then just `docker compose -f docker-compose.prod.yml pull && up -d`
again — no rebuild on the production box, ever.

---

## Troubleshooting

**"Port 5000/80/443 already in use" on startup.** Something else on the
VPS is already bound to that port — often a bare `python3 app.py` process
left running from earlier testing, or a previous docker-compose stack
that wasn't fully torn down.
```bash
sudo ss -ltnp | grep -E ':(5000|80|443)'
```
Identify the PID and `kill` it (or `docker compose down` if it's a
leftover container), then retry `docker compose up -d`.

**`ephemeral-rest` container keeps restarting / unhealthy.**
```bash
docker compose logs ephemeral-rest --tail 50
```
Most common causes: MySQL connection refused (check Phase 4's checkpoint
again — did `mysqld.cnf` actually reload? did the firewall rule land?),
or missing `sweph/*.se1` files (Phase 3).

**"Can't connect to host.docker.internal:3306" specifically.** Two
likely causes, in order of likelihood:

1. The `ufw allow from 172.28.0.0/16` rule from Phase 4 is missing or
   landed *after* the `deny 3306` rule rather than before it (`ufw`
   matches top-down, first rule wins):
   ```bash
   sudo ufw status numbered
   ```
2. `extra_hosts` only takes effect when a container is *created*, not on
   one already running — if `ephemeral-rest` started before this setting
   existed in `docker-compose.yml`, or hasn't been recreated since:
   ```bash
   docker compose exec ephemeral-rest getent hosts host.docker.internal
   ```
   Should print an IP. If it doesn't: `docker compose up -d --force-recreate ephemeral-rest`.

**Certbot fails domain validation.** Almost always DNS hasn't propagated
yet, or the A record points at the wrong IP — re-run the Phase 6
checkpoint (`dig +short`) before retrying certbot.

**Portal loads but can't reach the API (login always fails).** Check
`ephemeral-admin` can actually resolve and reach the API container. The
portal image only has the PHP curl *extension* installed, not a
standalone `curl` CLI binary, so check via PHP itself rather than shelling
out to `curl`:
```bash
docker compose exec ephemeral-admin php -r "
\$ch = curl_init('http://ephemeral-rest:5000/health');
curl_setopt(\$ch, CURLOPT_RETURNTRANSFER, true);
echo curl_exec(\$ch), PHP_EOL;
"
```
Should print JSON. If this fails but the API itself is healthy from
Phase 12, the issue is Docker networking between the two containers —
confirm both are on the same bridge network:
```bash
docker network ls | grep ephemeral
docker network inspect <the network name from above>
```
(both containers should be listed under it — the exact network name
depends on your directory name, since Compose prefixes it with the
project name derived from the folder `docker-compose.yml` lives in).

---

## Ongoing maintenance

**Deploying a code update (test box, builds from source):**
```bash
cd ~/ephemeral/ephemeralREST && git pull
cd ~/ephemeral/ephemeralADMIN && git pull
cd ~/ephemeral/ephemeralREST
docker compose up -d --build
```

**Deploying a code update (production, pulls a pre-built image):** repeat
Phase 13 on the test box against the new commit, then on production:
```bash
cd ~/ephemeral/ephemeralREST
nano .env    # update IMAGE_TAG to the new $VERSION
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```
Production's `git pull` here is only to keep `nginx.conf` and
`docker-compose.prod.yml` themselves in sync if you've changed
infrastructure config — it doesn't affect the app images either way.

**Viewing logs (add `-f docker-compose.prod.yml` on production):**
```bash
docker compose logs -f ephemeral-rest
docker compose logs -f ephemeral-admin
docker compose logs -f nginx
```

**Stopping everything:**
```bash
docker compose down
```
(Data persists in the `ephemeral-data`/`ephemeral-logs`/`admin-app` named
volumes across this — use `docker compose down -v` only if you actually
want to wipe them, e.g. `ephemeral-data` if you're on SQLite rather than
MySQL.)