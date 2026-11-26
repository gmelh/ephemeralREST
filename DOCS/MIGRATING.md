# Moving a two-service server to the one-container install

Before 1.0, ephemeralREST ran as two containers — the API and the ephemeralADMIN portal —
behind nginx, usually on two addresses (`api.example.com`, `admin.example.com`). From 1.0 the
portal is built in, and one container serves everything. It can keep the same two addresses:
the API (and the web app for account holders) on the first, administration on the second
(`--admin-domain`). Nothing about the data changes: the same database and the same
`SECRET_KEY` carry straight over.

The install runs behind the server's **shared Caddy**, which holds ports 80 and 443 for every
app on the server, so Studio apps such as AstrologyStudio can be installed beside it.

## What stays the same

- **API clients.** Every endpoint still answers at the root (`/calculate`) as well as under
  `/api/v1`, with the same keys. Keep serving the old API address (step 4) and nothing needs
  to change in MindForce, cosmobiology.online or anything else.
- **Accounts, keys, passwords, output settings, email settings and templates, federated
  services and grants** — all in the database.
- **Links in emails already sent.** `/verify.php?t=…` and `/set-password.php?t=…` redirect to
  the web app's pages, so they keep working on the old portal address too.

## Steps

1. **Back up.** Copy the database (for SQLite, the `ephemeral.db` file on the
   `ephemeral-data` volume; for MySQL, a `mysqldump`) and note the `SECRET_KEY` from the old
   `.env`. Without that exact key, stored API keys can't be decrypted.

2. **Install** on the new server (or the same one, once the old containers and nginx are
   stopped: the shared Caddy needs ports 80 and 443), with the same two addresses:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh \
     | sudo bash -s -- --domain api.example.com --admin-domain admin.example.com --no-data
   ```

   It installs in `/opt/studioapps/ephemeral`; the paths below assume that.

3. **Bring the data across**, then restart:

   ```sh
   ephemeral stop
   # The secret key: replace the generated one
   sudo sed -i 's/^SECRET_KEY=.*/SECRET_KEY=THE-OLD-KEY/' /opt/studioapps/ephemeral/.env
   # SQLite: the database and the ephemeris files
   sudo cp ephemeral.db /opt/studioapps/ephemeral/data/ephemeral.db
   sudo cp -r sweph/*.se1 /opt/studioapps/ephemeral/data/sweph/
   # Federation test files, if you had any (they were in ./federation-config)
   sudo cp federation-config/*.json /opt/studioapps/ephemeral/data/federation/
   sudo chown -R 10001:10001 /opt/studioapps/ephemeral/data
   ephemeral start
   ```

   **MySQL** (for example a database shared with marketREST): don't copy a database file —
   add `DB_TYPE=mysql` and the `MYSQL_*` settings from the old `.env` to `/opt/studioapps/ephemeral/.env`
   instead. If MySQL runs on the host, also add an `extra_hosts` entry for the app in
   `compose.override.yml` (as the old compose file had) and use `MYSQL_HOST=host.docker.internal`.

   Copy over any other settings you'd changed (`SITE_NAME`, `CORS_ORIGINS`, rate limits,
   `ALLOW_ADMIN_PROMOTION`, `TRUSTED_DEVICE_DAYS`, `GOOGLE_MAPS_API_KEY` with
   `USE_GOOGLE=true`, …). `PORTAL_URL` and `API_PUBLIC_URL` are no longer needed.

4. **Other old addresses**, if you had any besides those two, can keep answering: add them to
   `/opt/studioapps/ephemeral/.env` and run the installer again (it keeps your settings):

   ```sh
   echo 'EXTRA_DOMAINS=old-api.example.com' | sudo tee -a /opt/studioapps/ephemeral/.env
   curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash -s -- --yes
   ```

   Point those DNS records at the server; the shared Caddy gets their certificates too.

5. **Other federated services** on the same server (marketREST, searchREST) that joined the
   old `ephemeral-network`: add the app to that network in `compose.override.yml` — see
   "Joining other services" in [`install/README.md`](../install/README.md). They reach the API
   at `http://app:8080` (or by the container name, `ephemeral-app-1`) instead of
   `http://ephemeral-rest:5000`. Studio apps on the shared Caddy's network reach it at
   `http://ephemeral-app:8080`.

6. **Check**: `ephemeral status`, log in at the new address, and call
   `https://api.example.com/health` from a client. Then retire the old containers and the
   ephemeralADMIN checkout.

## What's gone

- The separate portal container, nginx, certbot and `setup.sh` (their files are kept in
  `deploy/two-service/` for anyone still running that setup).
- The domain-registration pages (`registrations.php`, `register-domain.php`,
  `register-key.php`, `portal-domain.php`). They called API endpoints that no longer existed.
- The marketing landing page (`landing.php`). Signed-out visitors see the login page, with a
  link to create an account (turn that off with `ALLOW_REGISTRATION=false`).
