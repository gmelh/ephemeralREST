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

   **MySQL** (for example a database shared with marketREST): don't copy a database file.
   To keep using the same MySQL, run the installer again with its `--mysql-...` options (it
   checks the connection first); to move to Vultr Managed MySQL, see
   [below](#moving-the-database-to-vultr-managed-mysql). If MySQL runs on the host, also add an `extra_hosts` entry for the app in
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

## Moving the database to Vultr Managed MySQL

To move an install that uses MySQL on the server itself onto Vultr Managed MySQL (the same
accounts, keys and settings), copy the database across first, then install against it.

1. **Make the managed database ready**, as in [Using MySQL](../install/README.md#using-mysql-vultr-managed-mysql):
   create a database (`ephemeral`), add the old server's and the new server's IP addresses to
   its *Trusted Sources*, and download its CA certificate (`ca-certificate.crt`).

2. **Check every table has a primary key** — Vultr refuses tables without one. ephemeralREST's
   own tables all have one; this lists any that don't (a table left by another service, say):

   ```sql
   SELECT t.table_name FROM information_schema.tables t
   LEFT JOIN information_schema.table_constraints c
     ON c.table_schema = t.table_schema AND c.table_name = t.table_name
    AND c.constraint_type = 'PRIMARY KEY'
   WHERE t.table_schema = 'OLD_DB' AND t.table_type = 'BASE TABLE' AND c.constraint_name IS NULL;
   ```

   Leave any it lists out of the copy (name the tables to copy after the database name in the
   `mysqldump` command below). Vultr also has only InnoDB: a table on another engine is made as
   InnoDB on import, or refused.

3. **Copy the data.** Stop ephemeralREST on the old server so nothing changes during the copy, then:

   ```sh
   mysqldump --single-transaction --set-gtid-purged=OFF --no-tablespaces \
     -h OLD_HOST -u OLD_USER -p OLD_DB > ephemeral.sql
   mysql --ssl-mode=VERIFY_CA --ssl-ca=ca-certificate.crt \
     -h vultr-prod-xxxx.vultrdb.com -P 16751 -u vultradmin -p ephemeral < ephemeral.sql
   ```

   `--set-gtid-purged=OFF` matters: without it the dump tries to set GTIDs, which a managed
   database doesn't allow.

4. **Give the new install the old `SECRET_KEY`** before it starts, so it can read the stored keys.
   The installer keeps a key it finds in `.env`:

   ```sh
   sudo install -d -m 755 /opt/studioapps/ephemeral
   echo 'SECRET_KEY=THE-OLD-KEY' | sudo tee /opt/studioapps/ephemeral/.env >/dev/null
   sudo chmod 600 /opt/studioapps/ephemeral/.env
   ```

5. **Install** with the `--mysql-...` options. The installer checks the connection, then
   ephemeralREST starts on the copied tables (and brings them up to date, as it does on any start):

   ```sh
   curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash -s -- \
     --domain api.example.com --admin-domain admin.example.com --no-data \
     --mysql-host vultr-prod-xxxx.vultrdb.com --mysql-port 16751 --mysql-user vultradmin \
     --mysql-password-file /root/mysql-password --mysql-database ephemeral \
     --mysql-ca /root/ca-certificate.crt
   ```

   Then the ephemeris files as in step 3 above, and log in as usual.

6. **Other services that read ephemeralREST's database** (marketREST and searchREST check keys
   against it) need its new address too: the Vultr host, port and user, with TLS, and their
   server in the database's *Trusted Sources*. Give them a read-only user of their own
   (`GRANT SELECT ON ephemeral.* TO …`) rather than ephemeralREST's.

An install that uses SQLite can move to MySQL too, but only by starting afresh: there's no
copy from SQLite to MySQL. The installer won't do it unasked (see `--switch-to-mysql`).

## What's gone

- The separate portal container, nginx, certbot and `setup.sh` (their files are kept in
  `deploy/two-service/` for anyone still running that setup).
- The domain-registration pages (`registrations.php`, `register-domain.php`,
  `register-key.php`, `portal-domain.php`). They called API endpoints that no longer existed.
- The marketing landing page (`landing.php`). Signed-out visitors see the login page, with a
  link to create an account (turn that off with `ALLOW_REGISTRATION=false`).
