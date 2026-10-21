# P2: Database and tenant isolation

| Field | Value |
|---|---|
| **Authorisation** | **AUTHORISED** by the owner, 2026-10-21 ("Start P2"). **Complete** (PR #3; implementation choices in AD-26). |
| **Depends on** | P1 |
| **Blocks** | P3, P4, P5, P11 |
| **Effort (estimate)** | 1–1.5 weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-04: managed PostgreSQL with backups | APPROVED |
| AD-09: own database; Stock Flow excluded | APPROVED |
| AD-16: PostgreSQL queue with inbox and outbox | ADOPTED (delegated) |
| AD-17: tenant isolation | ADOPTED (delegated) |
| AD-21: backups (restore must preserve roles and policies) | ADOPTED (delegated) |
| Database vendor and region | PENDING EXTERNAL (price sheet). PC-9 (same region as the VPS) is PROPOSED. |

## Objective and scope
- **Objective:** one canonical schema with enforced isolation between businesses. This replaces the conflicting schemas in research docs 07, 08, 09 and 17.
- **Scope:**
  - **Migration tooling.**
  - **Roles:**
    - owner/migrator;
    - `app_rw` (NOBYPASSRLS, not the table owner);
    - a read-only reporting role (optional).
  - **Core tables:**
    - Tenants and channels: `tenants`, `channels` (provider, session ref, state), `staff_members`, `staff_sessions`, `consents`, `owner_verifications`.
    - Customers and conversations: `customers`, `conversations`.
    - Messaging: `inbox_events`, `outbox_messages`.
    - Confirmations: `pending_actions`.
    - Orders and payments: `orders`, `order_items`, `order_transitions`, `payments`.
    - Audit and history: `audit_log`, `channel_events`.
    - The menu and settings tables are added in P4.
  - **FORCE RLS** on every tenant table, using a single shared policy expression that handles an unset tenant setting safely (`NULLIF(current_setting(...,true),'')`).
  - **Bootstrap resolver:** session reference → tenant, narrowly granted.
  - **Keys and deletes:**
    - Composite unique keys (`tenant_id, sku`), (`tenant_id, order_number`).
    - Per-tenant order sequences.
    - `RESTRICT` on financial foreign keys.
    - Soft delete plus anonymisation for customers.
  - **Money** stored as integer paisa or `NUMERIC(12,2)`; never floats.

## Phase security and reliability requirements
- The runtime never uses the owner role or `service_role`.
- SECURITY DEFINER is used only for the resolver, with `SET search_path`, and EXECUTE is revoked from PUBLIC.
- No tenant ID ever comes from the request body.

## Tasks

### P2-T1: Migration tooling and roles
- **Prerequisites:** P1; a development PostgreSQL. **Free and local; no purchase in P2.**
- **Deliverables:** migration tooling; a role setup script for owner/migrator, `app_rw` (NOBYPASSRLS, not the table owner) and an optional read-only reporting role.
- **Security:** **the runtime never uses the owner role or `service_role`.**
- **Tests:** a role-attribute check asserts that `app_rw` lacks BYPASSRLS and owns no tables.
- **Acceptance:** the check passes in CI.
- **Rollback:** migrations are reversible during development.

### P2-T2: Canonical core schema
- **Prerequisites:** T1.
- **Deliverables:** the core tables listed in Scope. Composite unique keys (`tenant_id, sku`), (`tenant_id, order_number`). Per-tenant order sequences. `RESTRICT` on financial foreign keys. Soft delete plus anonymisation for customers. Money stored as integer paisa or `NUMERIC(12,2)`; never floats.
- **Tests:**
  - a schema lint rejects float money columns;
  - inserting the same SKU in two tenants succeeds, and a duplicate within one tenant fails;
  - deleting a customer with orders is blocked, and anonymisation works.
- **Acceptance:** all schema tests pass.
- **Rollback:** reversible in development. **Once data exists, use forward-only fixes.**

### P2-T3: RLS policy module
- **Prerequisites:** T2.
- **Deliverables:** **FORCE RLS** on every tenant table, using a single shared policy expression that handles an unset tenant setting safely (`NULLIF(current_setting(...,true),'')`).
- **Tests:** the CI enumeration test, cross-tenant tests and fail-closed test (T6).
- **Acceptance:** see T6.

### P2-T4: Bootstrap resolver
- **Prerequisites:** T2.
- **Deliverables:** session reference → tenant, narrowly granted.
- **Security:** **SECURITY DEFINER is used only for the resolver, with `SET search_path`, and EXECUTE is revoked from PUBLIC.**
- **Tests:**
  - the resolver returns only `tenant_id`;
  - PUBLIC cannot execute it;
  - no other SECURITY DEFINER function exists (catalog check).
- **Acceptance:** all three checks pass.

### P2-T5: Database-access helper
- **Prerequisites:** T3.
- **Deliverables:** a database-access helper that refuses to run a query without a tenant context.
- **Security:** **no tenant ID ever comes from the request body.**
- **Tests:** a call without a tenant context raises an error before any SQL runs.
- **Acceptance:** the test passes, and there's a code-review checklist item.

### P2-T6: Isolation test suite
- **Prerequisites:** T3–T5.
- **Deliverables, all run in CI:**
  - **CI enumeration test:** every table either has `tenant_id` + RLS + FORCE + a policy, or is on an explicit allowlist of global tables.
  - **Cross-tenant tests, run as `app_rw`:** reading or updating tenant B's rows from a tenant A context affects **0 rows** on every table.
  - **Fail-closed test:** a query without a tenant context returns 0 rows or an error, never all rows.
  - **Restore-compatibility test:** a dump and restore keeps roles, grants and policies (also used in P11).
- **Acceptance:** all four pass on every CI run.

## Phase deliverables
- **Deliverables:** migrations, a role setup script, an RLS policy module, and a database-access helper that refuses to run a query without a tenant context.

## Phase acceptance criteria (exit)
- CI enumeration test passes (every table covered, or allowlisted as global).
- Cross-tenant tests run as `app_rw` affect 0 rows on every table.
- Fail-closed test passes.
- Restore-compatibility test passes.

## Risks and rollback
- **Risk:** policy mistakes. Mitigated by the enumeration test.
- **Rollback:** migrations are reversible during development. Once data exists, use forward-only fixes.

## Open items
- Database vendor and region are not chosen ([price sheet](../../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing); PC-9 proposed).
