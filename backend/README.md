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
python -m velora.admin menu-import --tenant karachi-grill --file menu.csv   # draft only; the owner publishes
python -m velora.admin go-live --tenant karachi-grill   # refused until both records exist
python -m velora.admin tenants                          # status, and what each tenant still needs
```

- **Corrections:** a verification or consent record can't be edited. To correct one, record a new one with `--supersedes <id>`.
- **Login links in the pilot (B7):** VELORA doesn't send links itself. You, or the owner for their staff, pass each link on privately.

## Menu, availability and ordering settings (P4)

- `src/velora/menu/`: the menu structure and its rules (`model.py`), CSV import (`csv_import.py`), versions and the customer-facing read (`store.py`), and staff operations (`service.py`).
- `src/velora/ordering/`: settings (`settings.py`), pause and capacity (`gate.py`), and server-side pricing (`pricing.py`).
- **Draft, review, publish.** The draft menu is edited as a whole, by CSV import or by form (`PUT /api/menu/draft`), and both go through the same validator. Publishing (`POST /api/menu/versions/{id}/publish`) needs the revision the reviewer saw. A published version is frozen by the database. Re-publishing an older version is the rollback. Customers only ever see the newest publication.
- **Founder import.** `menu-import` fills the draft through `velora_platform`, which cannot publish. The owner reviews `GET /api/menu/draft` (which lists the price changes against the live menu) and publishes.
- **Availability** is per SKU (`PUT /api/menu/availability/{sku}`) and applies immediately, across versions.
- **Controls:** `POST /api/ordering/pause` and `/resume`, `GET /api/ordering/status`, and `PATCH /api/settings/ordering` (delivery fee and minimum, pickup window, tax per payment method, COD first-order limit, transfer window, orders per 15-minute slot).
- **Tax (B3):** a payment method with no tax rate set can't be quoted. Don't enter real rates until the accountant confirms them.

### Menu CSV format

UTF-8, header on line 1, one row per item or per modifier option:

```csv
kind,category,sku,name,price,description,group,min,max
item,Burgers,ZINGER,Zinger Burger,650,Crispy chicken fillet,,,
option,,ZINGER,Regular,0,,Size,1,1
option,,ZINGER,Large,150,,Size,,
item,Drinks,COKE,Coca-Cola 345ml,120,,,,
```

Prices are in rupees (`650` or `650.50`). An option's price is its extra charge. Put the item row before its options. `min` and `max` (how many options a customer picks from the group) go on the group's first row. Every problem in the file is reported with its line number, and nothing is saved until the file is clean.
