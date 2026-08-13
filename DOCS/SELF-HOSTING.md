# Self-Hosting ephemeralREST + ephemeralADMIN

This is a guide for running your own independent copy of ephemeralREST
(the API) and ephemeralADMIN (its admin portal) — whether you're
evaluating it, developing against it, or running it long-term as your own
service. It doesn't assume you're doing anything beyond that; setting up
a public domain and HTTPS is covered too, but as an optional later step,
not a prerequisite.

## Licensing, briefly

ephemeralREST is **GNU Affero General Public License v3 (AGPL v3)** —
chosen because the Swiss Ephemeris library it's built on is itself AGPL
v3. Under the AGPL, running this as a network service means anyone who
interacts with it over the network is entitled to receive its source
code. Publishing your fork or modifications on a public repository
satisfies this.

ephemeralADMIN (the portal) is **MIT licensed** — no equivalent
obligation there.

## What you'll need

- A machine with [Docker](https://docs.docker.com/get-docker/) and the
  Docker Compose plugin installed — your own computer for local
  evaluation, or any VPS if you want it reachable over the network
- `git`
- Swiss Ephemeris data files (see Step 2 — not bundled with either
  project; you download these yourself, directly from their publisher)
- About 10 minutes for the minimal path below

---

## Step 1 — Get the code

```bash
mkdir -p ~/ephemeral && cd ~/ephemeral
git clone <ephemeralREST-repo-url> ephemeralREST
git clone <ephemeralADMIN-repo-url> ephemeralADMIN
```

The layout matters: `ephemeralREST/docker-compose.yml` builds the portal
image from a relative path to its sibling, so the two need to sit next to
each other on disk:

```
~/ephemeral/
  ephemeralREST/
  ephemeralADMIN/
```

## Step 2 — Swiss Ephemeris data files

Neither project bundles or redistributes these — they're the
publisher's (Astrodienst's) own data, and you download your own copy
directly from them:

- Official download area: **https://www.astro.com/ftp/swisseph/ephe/**
  (also reachable via FTP at `ftp://ftp.astro.com/pub/swisseph/ephe/`)
- Background and file-naming reference: https://www.astro.com/swisseph-download/

At minimum, grab the planetary, lunar, and main-asteroid files covering
1800–2399 AD (`sepl_18.se1`, `semo_18.se1`, `seas_18.se1`) — that range
covers general-purpose astrological calculation. Download a wider range
if you specifically need dates outside it.

```bash
cd ~/ephemeral/ephemeralREST
mkdir -p sweph
# place the .se1 files you downloaded into ./sweph/
```

**Check:** `ls sweph/*.se1` should list what you just downloaded. If this
directory is empty when you start the API, it'll run but chart
calculations will fail.

## Step 3 — Minimal configuration

The database needs no setup at all — it defaults to SQLite, which the
app creates automatically on first start. The one thing you do need to
set is a secret key:

```bash
cd ~/ephemeral/ephemeralREST
python3 -c "import secrets; print(secrets.token_hex(32))"
```

```bash
nano .env
```
```bash
SECRET_KEY=<paste the value you just generated>
USE_GOOGLE=false
```

(`USE_GOOGLE=false` skips requiring a Google Maps API key — location
lookups fall back to the bundled offline cities database instead. Set it
to `true` and add `GOOGLE_MAPS_API_KEY=...` if you'd rather use Google's
geocoding.)

## Step 4 — Start it

```bash
docker compose up -d --build
```

This builds both images locally and starts three containers: the API,
the portal, and an nginx front end proxying both. First build takes a few
minutes; subsequent starts are fast.

**Check:**
```bash
docker compose ps
```
All three services should show `running`.

## Step 5 — Create your first account

```bash
docker compose exec ephemeral-rest python3 key_manager.py create
```

Answer the prompts — choose `admin` when asked, and set a password when
offered one, so you can log into the portal immediately rather than
needing email verification (which isn't configured yet at this point
anyway). The command prints your API key once at the end — save it, it
isn't shown again.

## Step 6 — Verify

```bash
curl http://localhost/health
```
Should return JSON, not a connection error.

Then open `http://localhost:8080` in a browser and log in with the
identifier and password from Step 5.

If both of those work, you have a fully running, independent instance.

---

## Optional: a real domain and HTTPS

Everything above runs over plain HTTP on your own machine or a VPS's IP
address — fine for evaluation, development, or anything not exposed
publicly. If you want it reachable at a real domain with a trusted
certificate, here's the condensed path (this assumes a VPS with a public
IP, not a machine behind home NAT):

1. **Point DNS** — A records for two domains (or subdomains), one for the
   API and one for the portal, at your VPS's IP address. Wait for
   propagation (`dig +short yourdomain.com` should return that IP).

2. **Get a certificate.** nginx isn't listening on port 80 for real
   domains yet, so `--standalone` (which briefly binds port 80 itself) is
   simplest for this first certificate:
   ```bash
   sudo apt install -y certbot
   sudo mkdir -p /srv/ephemeral/certbot-webroot
   sudo certbot certonly --standalone -d api.yourdomain.com -d admin.yourdomain.com
   ```

3. **Point `nginx.conf` at your domains** — it ships with
   `api.yourdomain.com` / `admin.yourdomain.com` as placeholders in the
   HTTPS server blocks near the bottom of the file; replace both with
   your real ones.

4. **Set up renewal**, so it doesn't lapse in 90 days:
   ```bash
   sudo certbot certonly --webroot -w /srv/ephemeral/certbot-webroot \
       -d api.yourdomain.com -d admin.yourdomain.com \
       --deploy-hook "docker compose -f $HOME/ephemeral/ephemeralREST/docker-compose.yml restart nginx"
   ```

5. **Open the firewall and restart:**
   ```bash
   sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
   docker compose up -d
   ```

Your API and portal are now reachable at `https://api.yourdomain.com` and
`https://admin.yourdomain.com`.

## Optional: MySQL instead of SQLite

SQLite is genuinely sufficient for most self-hosted use — it's the
default for a reason. You'd reach for MySQL specifically if you're
planning to run other services of your own that share this instance's
authentication data (see `DOCS/ARCHITECTURE.md`, "Federated service
access" — this instance can act as a shared identity provider for
companion services you build yourself, reading the same database
directly rather than calling back to the API per request). Set
`DB_TYPE=mysql` and the corresponding `MYSQL_*` values in `.env`; see
`DOCS/SETUP.md` for the full connection setup.

---

## Stopping / removing it

```bash
docker compose stop      # stop, keep everything for next time
docker compose down      # stop and remove containers (data persists in volumes)
docker compose down -v   # also delete the data — irreversible
```

## Where to go from here

- `DOCS/ARCHITECTURE.md` — how the pieces fit together, including the
  federated-service-access pattern mentioned above
- `DOCS/SETUP.md` — bare-metal (non-Docker) installation, and more detail
  on MySQL configuration
- `DOCS/DOCKER_DEPLOYMENT_CHECKLIST.md` — a more exhaustive, checkpointed
  version of this guide, written for a specific production rollout
  (build/test/promote workflow, registry-based deploys) — more than most
  self-hosters need, but useful if you're planning to run this at any
  real scale or across multiple environments