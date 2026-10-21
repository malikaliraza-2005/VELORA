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
