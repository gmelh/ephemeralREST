ephemeralREST
-------------

ephemeralREST is a REST API that enables developers of astronomy and astrology applications to
perform the sometimes complex calculations with simple calls to a REST service — with a
built-in web app for accounts, API keys and administration.

**Install** on a fresh Ubuntu or Debian server, at an address you've pointed at it:

```sh
curl -fsSL https://raw.githubusercontent.com/gmelh/ephemeralREST/main/install/install.sh | sudo bash
```

Then open the address to create the administrator account. The API is at `/api/v1` on the
same address. See [install/README.md](install/README.md) for the day-to-day commands, and
[DOCS/API_REFERENCE.md](DOCS/API_REFERENCE.md) for the API. Moving from the older
two-container setup (with ephemeralADMIN)? See [DOCS/MIGRATING.md](DOCS/MIGRATING.md).

**Develop:** `docker compose up --build` and open http://localhost:8080 — or run
`python app.py` with `npm run dev` in `web/` alongside. Tests: `python -m pytest tests`.

This service relies on Swiss Ephemeris and its data files, which are licensed under the AGPL.
You may also obtain a commercial license to use Swiss Ephemeris from Astrodienst. The web app
in `web/` shares its shell with MailStudio.
