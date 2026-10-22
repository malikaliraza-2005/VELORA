# VELORA

Pakistan-first, WhatsApp-based ordering SaaS for restaurants and cloud kitchens (hotels later).

> **Project status:** early build. P1–P4 are complete (repository, database and tenant isolation, staff identity, menu and ordering settings). The provider-neutral parts of P5–P9 are built against simulated WhatsApp: messaging core, conversations, order lifecycle, payment verification and the staff dashboard. The OpenWA adapter is blocked until the live validation Go.
> Nothing connects to WhatsApp yet.
> See [`docs/planning/implementation-status.md`](docs/planning/implementation-status.md).

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Python 3.12 + FastAPI: webhook ingress and background workers |
| `web/` | Staff dashboard (installable PWA, plain JavaScript, no build step). Built in P9 |
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

## Branch workflow

All work happens on short-lived branches. `main` only changes through pull requests.

| Branch prefix | Use for | Example |
|---|---|---|
| `feature/` | New phase work | `feature/p2-database` |
| `fix/` | Fixing something broken | `fix/ci-secret-scan` |
| `docs/` | Documentation only | `docs/p2-decisions` |

1. Start from an up-to-date `main`: `git switch main` then `git pull`.
2. Create a branch: `git switch -c feature/p2-database`.
3. Commit the work on that branch.
4. Push the branch: `git push -u origin feature/p2-database`.
5. On GitHub, open a **pull request** into `main`, and wait for CI to go green.
6. Merge the pull request, then delete the branch.

## Branch protection guidance (GitHub)

These are not applied yet; the owner applies them. Settings → Branches → Add rule for `main`:

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
