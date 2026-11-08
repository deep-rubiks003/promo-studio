# Setup, configuration and troubleshooting

## Install and start

Use Python 3.11+ and a local virtual environment. On Windows, `Start Promo Studio.cmd` creates `.venv`, installs `requirements.txt` and opens the browser. On macOS/Linux, use the commands in the root README.

The app listens at `http://127.0.0.1:8787`. Keep its process running. Closing the process stops the app; the local database survives. Only one instance can use that port.

## Gemini connection

Click Connect Gemini in the UI, enter a key privately, select a model and set a positive limit up to $2. Connection validation checks whether the model is available to the key; it does not guarantee that a later generation request has capacity or quota.

The registry currently includes `gemini-3.5-flash-lite` (default), `gemini-3.1-flash-lite` and `gemini-3.8-flash`. These are configured project identifiers, not a claim that every account can use them. A provider can reject an identifier, thinking setting, request or account entitlement. There is no silent model substitution. To add a supported identifier, update `models.py`, confirm request settings in `provider.py`, and update the mocked model tests.

The key stays in server memory. Reconnect after restarting. Do not add the key to `.env`, the README, a fixture or a screenshot. The app does not require an environment key to import files or run its offline tests.

## Data location and backups

By default the app creates `data/studio.sqlite3` relative to `server.py`. For a different local directory:

```powershell
$env:PROMO_STUDIO_DATA = 'C:\PromoStudioData'
.\.venv\Scripts\python.exe server.py
```

```bash
PROMO_STUDIO_DATA="$HOME/.local/share/promo-studio" .venv/bin/python server.py
```

Use a directory you control. The database contains imported text, project output, raw responses, job history and accounting records. To back it up, stop the application and copy the database and any SQLite sidecars if present, or use SQLite's backup API for a consistent snapshot. Never upload the database to a public issue or repository.

The clean repository does not contain your previous projects. Continue using the original installation's data directory or import source again into the clean copy. Do not overwrite a database casually to merge projects.

## PDF fonts

PDF export uses local TrueType fonts. Windows commonly supplies Arial; Linux commonly supplies DejaVu Sans. On a minimal Ubuntu environment, install the `fonts-dejavu-core` system package. Alternatively place an openly licensed `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` pair in a `fonts/` directory beside `pdf_export.py`, respecting its licence.

Some scripts/languages need additional supported fonts. Footage and audio timing remain unverified regardless of PDF rendering.

## Common problems

| Symptom | What to check |
|---|---|
| Python command not found | Install Python, enable PATH on Windows, or use `python3` on macOS/Linux. |
| Dependency import error | Run pip through the same virtual-environment Python used to start the server. |
| Address/port already in use | Stop the earlier studio instance; do not start a second one on 8787. |
| Forbidden request after restart | Refresh the browser to receive the new local session token. |
| Key no longer connected | Reconnect it after server restart; it is intentionally not persisted. |
| Gemini 400 / 404 | Check exact configured model ID, request settings and availability to your account. |
| Gemini 401 / 403 | Check key permissions, API access and billing/account eligibility. |
| Gemini 429 | Quota or rate limit; stop retrying blindly and check the account. |
| Gemini busy / 503 | Inspect sanitized diagnostics and resume when appropriate. Bounded retries cannot create provider capacity. |
| Budget used/held looks higher than expected | Ambiguous failed requests retain conservative reservations; compare with provider billing rather than assuming the hold is an invoice. |
| Draft exceeds 60 seconds | It is a genuine failed output. Inspect it and request a source-based repair; do not remove the duration rule. |
| Virality Review button disabled | Fix local cut checks, finish pending generation, and return from a read-only saved snapshot if applicable. |
| No score shown | Review is optional. Generation and refinement do not trigger it automatically. |
| Nothing happens on a static hosting service | The frontend depends on the Python `/api` routes; GitHub Pages or a static upload alone cannot run the app. |
| Malformed source rejected | Correct missing/reversed timestamps, encoding or unsupported block format; do not omit the offending dialogue. |

`GET /api/status` and `GET /api/diagnostics` inspect local state. The status includes a local session token; do not paste full responses publicly. Explicit diagnostic probe actions can call the model, so do not run them as free health checks.

## Stop and recover

Use Stop generation to cancel a job. Saved completed work is retained. If the server crashes or restarts, unfinished jobs are marked interrupted and do not automatically resume. Open the saved project and explicitly resume supported unfinished work. A provider request that was already sent may still be billed.

## Run tests safely

```bash
python -m pytest -q
```

Use the virtual-environment interpreter where appropriate. Tests use temporary databases and mocked providers. A provider key is not needed. Set `PROMO_STUDIO_DATA` to a scratch directory for an additional separation from your working database when developing.

CI runs offline on clean runners. A newly pushed workflow can fail due to platform differences even when local tests pass; inspect the run before advertising a green build.
