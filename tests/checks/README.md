# Checks

Scripted checks that run ephemeralREST in-process (Flask's test client) against a throwaway
SQLite database, with one test city and no network or email:

| Script | Checks |
|---|---|
| `route_check.py` | Every calculation route carries the four ayanamsas |
| `check_progressions.py` | Secondary progressions and solar arc use a day for a year; an old saved progression is recalculated when read |
| `check_signup.py` | App links: link origins for domain keys, links on `/register` and `/password/forgot`, `/register/complete`, the portal flow without links |
| `check_delivery.py` | Email delivery by an app: only when the admin allows it, only for the app's own accounts, every `deliver` kind, and switching it off |

Run each from this folder, with the repository's root as the argument, in an environment with
`requirements.txt` installed:

```bash
cd tests/checks
python route_check.py ../..
python check_progressions.py ../..
python check_signup.py ../..
python check_delivery.py ../..
```

Each prints a line per check and stops at the first failure.
