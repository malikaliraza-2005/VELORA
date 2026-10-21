# backend/

Python 3.12 + FastAPI service: webhook ingress and background workers (decision AD-15).

P1 added the skeleton: an app factory with a health endpoint, settings loaded from
environment variables, and an empty `workers` package. Product features arrive in later
phases — see [`../docs/planning/implementation-plan.md`](../docs/planning/implementation-plan.md).

## Local development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
cd backend
uv sync                      # create .venv from the committed lockfile
uv run ruff format --check . # formatting
uv run ruff check .          # lint
uv run mypy                  # type check (strict)
uv run pytest                # tests
```

## Database (P2)

`src/velora/db/` holds the PostgreSQL layer:

- `sql/roles.sql`: cluster roles. `velora_owner` is the migrator and owns everything. `app_rw` is the runtime role (NOBYPASSRLS, owns nothing). `velora_resolver` is NOLOGIN and owns only the bootstrap resolver.
- `migrations/`: plain SQL, `NNNN_name.up.sql` + `.down.sql`. Every tenant table gets FORCE RLS and the shared `tenant_isolation` policy via `velora.enable_tenant_rls()`.
- `access.py`: `Database.tenant(TenantId)`, the only way the runtime runs queries. It refuses to start without a `TenantId` and scopes one transaction to that tenant.
- `velora_platform` (P3) is the founder's onboarding role: NOBYPASSRLS, owns nothing, scoped to one tenant at a time. Its only cross-tenant right is listing tenants.

```bash
python -m velora.db setup-roles      # VELORA_ADMIN_DATABASE_URL (administrator)
python -m velora.db migrate up       # VELORA_MIGRATION_DATABASE_URL (velora_owner)
python -m velora.db migrate status
python -m velora.db migrate down 2   # development/test only
```

### Running the database tests locally

The database tests need a disposable PostgreSQL 17. Without one they are skipped (CI sets
`VELORA_REQUIRE_DB_TESTS=1`, so there they fail instead).

```bash
docker run -d --name velora-pg -e POSTGRES_PASSWORD=<random> -p 127.0.0.1:55432:5432 postgres:17
export VELORA_TEST_ADMIN_URL=postgresql://postgres:<random>@127.0.0.1:55432/postgres
export VELORA_TEST_PG_TOOLS="docker exec -i velora-pg"   # pg_dump/pg_restore for the restore test
uv run pytest
```

Each session creates the roles with random passwords and migrates a uniquely named database,
which is then dropped. Use a throwaway server: the roles are cluster-wide.

## Staff identity (P3)

- `src/velora/identity/`: magic links and device sessions (`sessions.py`), staff management and payment PINs (`staff.py`), the role-permission matrix (`permissions.py`) and audit writes (`audit.py`).
- `src/velora/api/`: the staff API under `/api`. The session cookie is `__Host-velora_session` (HttpOnly, Secure, SameSite=Strict). State-changing requests need the `X-CSRF-Token` header that `/api/auth/login` and `/api/me` return.
- Rules: magic links last 15 minutes and work once. Sessions end after 12 hours idle or 30 days. A role change or removal revokes the person's sessions. A PIN locks for 15 minutes after 5 wrong tries. The tenant always comes from the session.

### Founder onboarding tools

These connect as `velora_platform` through `VELORA_PLATFORM_DATABASE_URL`. Every action is audited under your operator name.

```bash
export VELORA_OPERATOR="<your name>"
python -m velora.admin tenant-create --slug karachi-grill --name "Karachi Grill" --prefix KG
python -m velora.admin verification-record --tenant karachi-grill --method in_person --status verified --evidence-ref "<where the evidence is kept>"
python -m velora.admin consent-record --tenant karachi-grill --kind separate_number_ban_risk --version 2026-10 --document signed-consent.pdf --accepted-by "<owner name>"
python -m velora.admin owner-invite --tenant karachi-grill --name "<owner name>"   # prints a 15-minute link
python -m velora.admin go-live --tenant karachi-grill   # refused until both records exist
python -m velora.admin tenants                          # status, and what each tenant still needs
```

- **Corrections:** a verification or consent record can't be edited. To correct one, record a new one with `--supersedes <id>`.
- **Login links in the pilot (B7):** VELORA doesn't send links itself. You, or the owner for their staff, pass each link on privately.
