# VELORA

Pakistan-first, WhatsApp-based ordering SaaS for restaurants and cloud kitchens (hotels later).

> **Project status:** early build. Phase **P1 (repository foundation)** is in progress.
> No product features exist yet, and nothing connects to WhatsApp.
> See [`docs/planning/implementation-status.md`](docs/planning/implementation-status.md).

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Python 3.12 + FastAPI: webhook ingress and background workers |
| `web/` | Staff dashboard (PWA). Built in P9 |
| `infra/` | Deployment and containers. Built in P12 |
| `docs/` | Research inputs, architecture decisions and planning. Start at [`docs/README.md`](docs/README.md) |
| `.github/workflows/ci.yml` | CI: format, lint, type check, tests, secret scan |

## Development quick start

Requires [uv](https://docs.astral.sh/uv/) and git.

```bash
cd backend
uv sync          # installs the locked dependencies into backend/.venv
uv run pytest    # run the tests
```

The full list of checks is in [`backend/README.md`](backend/README.md).

## Configuration and secrets

- Configuration comes from environment variables. The only committed file is [`.env.example`](.env.example), and it holds **placeholders only**; a test enforces this.
- Real values go in an uncommitted `.env`. `.gitignore` excludes env files, WhatsApp session data and database dumps (also tested).
- **Never commit** real credentials, merchant data, session files, QR codes or database dumps.

### Secret scanning
- **CI:** every push and pull request runs gitleaks (`secret-scan` job).
- **Locally (recommended):** install the git hooks once per clone with `uvx pre-commit install`. This runs gitleaks and ruff before each commit.
  - *On the founder's current Windows machine, Windows Application Control blocked the `pre-commit` launcher, so the local hook is **not active there**. CI scanning still applies.*

## Branch protection guidance (GitHub)

Apply these once the repository is pushed. Settings → Branches → Add rule for `main`:

1. **Require a pull request before merging.** At least 1 approval once there is more than one contributor.
2. **Require status checks to pass:** `Backend checks` and `Secret scan (gitleaks)`.
3. **Require branches to be up to date before merging.**
4. **Block force pushes and deletions** on `main`.
5. **Do not allow bypassing** these settings, including for administrators.
6. Turn on GitHub **secret scanning and push protection** (Settings → Code security) where available.

## Ground rules (from the architecture decisions)

- An order is never confirmed before availability, pricing and the database transaction are validated.
- A payment is never marked verified from AI output or a screenshot alone.
- Each business's data is isolated in the application **and** by database row-level security.
- OpenWA (the planned WhatsApp gateway) is **unvalidated** until its tests pass, and is not claimed to be compliant. The official Meta Cloud API remains the alternative.

Details: [`docs/architecture/architecture-decisions.md`](docs/architecture/architecture-decisions.md).
