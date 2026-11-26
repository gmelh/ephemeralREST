# Installing ephemeralREST

On a fresh Ubuntu or Debian server (1 GB of memory and 3 GB of disk are plenty):

```sh
curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash
```

Before you run it, point an address at the server: in your domain's DNS settings add an
**A record** for, say, `api.example.com` with the server's IP address, and another for the
admin address if you want one (`admin.example.com`). The installer checks them, installs
Docker if needed, sets up the server's **shared Caddy** and starts ephemeralREST behind it,
downloads the Swiss Ephemeris files and the GeoNames cities list, and tells you where to create
the administrator account.

Then open the address (the admin address, if you gave one). The first person to do so creates
the administrator account.

- The web app (accounts, keys) is at `https://api.example.com`.
- The API is at `https://api.example.com/api/v1` — and, for clients written before 1.0,
  at the root too (`https://api.example.com/calculate`).
- With an admin address, administration (keys, sites, email, settings) works **only** at
  `https://admin.example.com`; the main address answers everything else. Without one, it's on
  the main address, as before.

## Sharing the server with Studio apps

ephemeralREST doesn't hold ports 80 and 443 itself. The **shared Caddy**
([studio-edge](https://github.com/studioappsorg/studio-edge), the `studioapps` command) does,
for every app on the server: AstrologyStudio, MailStudio and the rest install beside
ephemeralREST, in any order, and each gets its own address and certificate. `studioapps sites`
lists them.

A Studio app on the same server can reach ephemeralREST without going out to the internet, at
`http://ephemeral-app:8080` (both are on the `studioapps` network).

An install that ran its own Caddy (`/opt/ephemeral`, before this version) is moved onto the
shared Caddy when you run the installer again: same folder, data and secret key, and its
certificate is carried over so Let's Encrypt isn't asked again. The old setup is kept in
`backups/before-shared-caddy-…`, and if anything fails part-way it's put back as it was.

## Day to day

| Command | What it does |
| --- | --- |
| `ephemeral status` | Is it running? |
| `ephemeral update` | Back up, get the latest version, restart. The database is updated automatically. |
| `ephemeral backup` | Back up the database to `backups/` (also runs daily; two weeks are kept) |
| `ephemeral restore FILE` | Put a backup back (asks first, and backs up the current data before replacing it) |
| `ephemeral logs` | Follow ephemeralREST's log |
| `ephemeral logs caddy` | Follow the shared Caddy's log (certificates, errors) |
| `ephemeral reset-password EMAIL` | Prints a link to set a new password — works even before email is set up |
| `ephemeral keys ...` | Manage keys from the command line (`ephemeral keys --help`) |
| `ephemeral fetch-ephemeris` | Download the Swiss Ephemeris files again |
| `ephemeral import-cities` | Download and import the cities list again |
| `ephemeral restart` | Restart, picking up changes to `.env` |

Copy the `backups` folder off the server now and then (or turn on your hosting
provider's snapshots): a backup on the same server doesn't help if the server is lost.
The ephemeris files and cities list aren't in backups — they can be downloaded again.

## What's where

```
/opt/studioapps/ephemeral/   (/opt/ephemeral for an install from before the shared Caddy)
  .env                  settings: the addresses, the secret key, and anything you add
  compose.yml           the ephemeralREST container (rewritten by the installer)
  compose.override.yml  your own additions, if any (never touched by the installer)
  ephemeral.caddy       its sites in the shared Caddy (rewritten by the installer)
  site-extra.caddy      extra lines for those sites, from --caddy-extra (kept)
  data/                 ephemeral.db, sweph/, cities/, logs/, federation/
  backups/              daily backups
/opt/studioapps/edge/        the shared Caddy (certificates, every app's site file)
```

**Keep `SECRET_KEY` in `.env` safe and never change it.** Every stored API key is
encrypted with it; a backup is useless without it. The installer keeps it when re-run.

Any setting ephemeralREST understands can go in `.env` (see the examples at the end of the
file), followed by `ephemeral restart`. Email (SMTP) is set up in the web app, under Email.

## Options

```sh
curl -fsSL .../install.sh | sudo bash -s -- --domain astro.example.com --email you@example.com --yes
```

`--domain`, `--admin-domain` (administration only at this address), `--email` (for
certificate notices), `--google-key` (look places up with Google Maps instead of offline),
`--dir`, `--image`, `--caddy-extra FILE` (extra lines for its sites, such as a `tls` block),
`--edge-script FILE` (a local copy of the shared Caddy's script; it's still checked against its
SHA-256), `--no-data` (don't download the ephemeris files or cities list), `--yes` (no
questions), `--skip-dns-check`, `--dry-run` (write the files only). Running the installer again
keeps your data and settings. To add or change the admin address later, run it again with
`--admin-domain`.

Old addresses can keep working: put them in `.env` as `EXTRA_DOMAINS=old.example.com …` and run
the installer again.

## Joining other services (federation)

Other containers on the same server — marketREST, say — can reach ephemeralREST at
`http://app:8080` once they share its network. Put that in `compose.override.yml`, which the
installer never replaces, for example (create the network once with
`docker network create ephemeral-network` if nothing else has):

```yaml
services:
  app:
    networks: [default, federation]
networks:
  federation:
    name: ephemeral-network
    external: true
```

To share a MySQL database with those services instead of SQLite, set `DB_TYPE=mysql` and the
`MYSQL_*` settings in `.env` (and use `mysqldump` for backups). See
[`../deploy/two-service/`](../deploy/two-service/) for the older two-container setup.

## For maintainers

### Releasing

```sh
# 1. Set the version in version.py and web/package.json
git commit -am "Version 1.0.0" && git push
# 2. Tag it; pushing the tag starts .github/workflows/release.yml
git tag -a v1.0.0 -m "ephemeralREST 1.0.0"
git push origin v1.0.0
```

The release workflow runs the checks (`ci.yml`), builds the image for amd64 and arm64,
pushes it to `ghcr.io/gmelh/ephemeralrest` (tags `1.0.0`, `1.0`, `1`, `latest`), then creates a
GitHub release with `install.sh` and its SHA-256 checksum attached. The first time, make the
package public: GitHub → Packages → ephemeralrest → Package settings → Change visibility.

The installer runs `:1`, so a future 2.0 never arrives through `ephemeral update` by surprise.
Existing installs keep the image named in their `/opt/ephemeral/.env`.

A shorter install address (like MailStudio's `get.mailstudio.email`) can serve
`install/install.sh` the same way — see MailStudio's `install/hosting/` for the `.htaccess`
files.

### Testing the installer on a real server

`install/server-test.sh` runs the real installer against a real Docker and the shared Caddy: a
fresh install with an admin address, running it again, another app on the same Caddy in either
order, moving an install that ran its own Caddy (with the same certificate, and putting it back
when that fails), and something else on port 80. It takes over ports 80 and 443, `/opt/studioapps`,
`/opt/ephemeral` and `/etc/hosts` entries, so run it only on a throwaway VM:

```sh
sudo install/server-test.sh --edge-script ../studio-edge/edge.sh --i-understand
```

Certificates come from Caddy's local authority, so no real domain is needed.

### The shared Caddy's version

The installer pins `EDGE_VERSION` and `EDGE_SHA256` (the shared Caddy's script it was tested
with, from `https://getstudioapps.com/edge/`). Change both when moving to a newer studio-edge.
