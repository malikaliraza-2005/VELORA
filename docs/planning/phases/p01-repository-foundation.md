# P1: Repository and engineering foundation

| Field | Value |
|---|---|
| **Authorisation** | **AUTHORISED** by the owner, 2026-10-21. |
| **Depends on** | none. Can run in parallel with P0. |
| **Blocks** | P2 |
| **Effort (estimate)** | 3–5 days |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-15: Python 3.12 + FastAPI | ADOPTED (delegated) |
| AD-24: security baseline (no secrets in logs or repository) | ADOPTED (delegated) |
| AD-09: own database; Stock Flow excluded | APPROVED |

## Objective and scope
- **Objective:** a clean, secure, testable codebase skeleton.
- **Scope:**
  - Initialise git.
  - Layout: `backend/` (FastAPI ingress + workers), `web/` (PWA), `infra/` (compose, deploy), `docs/`.
  - Tooling: Python 3.12, dependency locking, formatter and linter, type checker, test runner, CI pipeline.
  - Configuration via environment variables, with only a `.env.example` committed.
  - Secret scanning in pre-commit and in CI.

## Phase security and reliability requirements
- No real credentials committed.
- `.gitignore` covers env files, session data and dumps.
- Lockfiles are committed.

## Tasks

### P1-T1: Initialise the repository and layout
- **Prerequisites:** P1 authorised.
- **Deliverables:** git repository; `backend/`, `web/`, `infra/`, `docs/`; a repository README.
- **Security:** `.gitignore` covers env files, session data and dumps.
- **Tests:** `git check-ignore` confirms `.env`, a session-data path and a dump file are ignored.
- **Acceptance:** a fresh clone has the layout, and the ignore checks pass.
- **Rollback:** reversible (delete or re-initialise).

### P1-T2: Toolchain
- **Prerequisites:** T1.
- **Deliverables:** Python 3.12 project with dependency locking, formatter and linter, type checker, test runner. **Lockfiles are committed.**
- **Tests:** format, lint, type check and the test runner all exit cleanly on the empty skeleton.
- **Acceptance:** all four tools run with zero errors.
- **Rollback:** reversible.

### P1-T3: Configuration
- **Prerequisites:** T1.
- **Deliverables:** configuration via environment variables, with only a `.env.example` committed (placeholder values).
- **Security:** **no real credentials committed.**
- **Tests:** a check that `.env.example` contains only placeholders.
- **Acceptance:** the check passes, and no real `.env` is in history.

### P1-T4: Secret scanning
- **Prerequisites:** T1.
- **Deliverables:** secret scanning in pre-commit and in CI.
- **Tests:** a test commit containing a fake key.
- **Acceptance:** **the secret scanner blocks a test commit that contains a fake key.**

### P1-T5: CI pipeline and branch protection guidance
- **Prerequisites:** T2, T4.
- **Deliverables:** CI that runs lint, type checks, tests and a secret scan on every push; branch protection guidance.
- **Acceptance:** **CI passes on an empty skeleton.**

## Phase deliverables
- Repository with README.
- CI that runs lint, type checks, tests and a secret scan on every push.
- Branch protection guidance.

## Phase acceptance criteria (exit)
- CI passes on an empty skeleton.
- The secret scanner blocks a test commit that contains a fake key.

## Risks and rollback
- **Risks and rollback:** low. Everything is reversible.
