# web/

The staff dashboard, an installable PWA built in phase P9. See
[`../docs/planning/phases/p09-staff-dashboard.md`](../docs/planning/phases/p09-staff-dashboard.md)
and decision AD-33.

Plain JavaScript modules with **no build step and no npm packages**. The API serves these
files from its own origin (`backend/src/velora/api/web.py`), so the session cookie stays
SameSite=Strict and HttpOnly.

| Path | What it is |
|---|---|
| `index.html` | The app shell. No inline script or style (CSP) |
| `manifest.webmanifest`, `assets/icons/` | Installability |
| `sw.js` | Service worker. It caches the app's own files only, never `/api/`, and never stores an action |
| `assets/js/main.js` | Login (token from the URL fragment), role-scoped navigation, banners, polling |
| `assets/js/api.js` | Fetch wrapper: CSRF header, an `Idempotency-Key` per action, refusal while offline |
| `assets/js/dom.js`, `ui.js`, `format.js` | DOM building (no HTML strings), action buttons and forms, display formats |
| `assets/js/views/` | One module per screen: board, payment panel, payments, chats, menu (availability, draft review, CSV import, publish, rollback), controls, summary, WhatsApp, staff, settings, me |

## Rules

- **Never** use `innerHTML`, `insertAdjacentHTML`, `eval`, inline handlers or browser storage.
  The CSP requires Trusted Types, and `backend/tests/api/test_dashboard.py` checks the code.
- Every action goes through `Action` / `actionButton` / `actionForm`, so it carries an
  idempotency key and is refused while the connection is lost.
- Each screen declares the permissions it needs. The API checks them as well.
- A new file must be added to the list in `sw.js`.

## Running it locally

Follow [`../docs/planning/local-testing-guide.md`](../docs/planning/local-testing-guide.md):
`python -m velora.dev setup`, then `python -m velora.dev api` serves the dashboard on
http://127.0.0.1:8000 and `python -m velora.dev customer` plays a WhatsApp customer.

The browser tests are in `backend/tests/web/` (Playwright, headless Chromium). Install the
browser once with `uv run playwright install chromium`.
