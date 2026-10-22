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
| `assets/js/views/` | One module per screen: board, payment panel, payments, chats, menu, controls, summary, WhatsApp, staff, settings, me |

## Rules

- **Never** use `innerHTML`, `insertAdjacentHTML`, `eval`, inline handlers or browser storage.
  The CSP requires Trusted Types, and `backend/tests/api/test_dashboard.py` checks the code.
- Every action goes through `Action` / `actionButton` / `actionForm`, so it carries an
  idempotency key and is refused while the connection is lost.
- Each screen declares the permissions it needs. The API checks them as well.
- A new file must be added to the list in `sw.js`.

## Running it locally

```bash
cd backend
VELORA_ENV=development VELORA_DATABASE_URL=postgresql://app_rw:...@127.0.0.1:55432/velora \
VELORA_PUBLIC_BASE_URL=http://127.0.0.1:8000 uv run uvicorn velora.asgi:app --port 8000
```

Sign in with an owner link from the founder's tool (`python -m velora.admin`), which points at
`VELORA_PUBLIC_BASE_URL/login#…`.

The browser tests are in `backend/tests/web/` (Playwright, headless Chromium). Install the
browser once with `uv run playwright install chromium`.
