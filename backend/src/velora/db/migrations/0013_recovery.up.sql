-- P11: backups, restore and recovery mode on startup (AD-21, AD-36).
--
-- * velora_backup (roles.sql) may connect. It reads everything through pg_read_all_data and
--   BYPASSRLS, so it needs no grant here, and it can write nothing.
-- * worker_heartbeats is global: the worker's last sign of life. On restart, recovery mode
--   takes the outage as starting then. It holds no tenant data.
-- * recovery_runs records, per tenant, each recovery after an outage: what it did, and when
--   the backlog of messages held through the outage was cleared. Orders placed in between
--   are highlighted on the dashboard. One run per tenant and outage, so a recovery that is
--   interrupted and started again never extends a transfer window twice.

DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO velora_backup',
                              pg_catalog.current_database());
END
$$;

-- Worker heartbeat (global) ---------------------------------------------------------------

CREATE TABLE worker_heartbeats (
    name         text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_-]{0,39}$'),
    started_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL
);

GRANT SELECT, INSERT, UPDATE (started_at, last_seen_at) ON worker_heartbeats TO app_rw;

-- Recovery runs (per tenant) --------------------------------------------------------------

CREATE TABLE recovery_runs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    -- The worker's last heartbeat before the restart; NULL on a first start.
    outage_started_at  timestamptz,
    started_at         timestamptz NOT NULL,
    leases_reclaimed   integer NOT NULL CHECK (leases_reclaimed >= 0),
    -- Inbound reconciliation waits for V-04; until then it is recorded as skipped.
    inbound            text NOT NULL CHECK (inbound IN ('skipped', 'reconciled')),
    inbound_recovered  integer NOT NULL CHECK (inbound_recovered >= 0),
    nonces_voided      integer NOT NULL CHECK (nonces_voided >= 0),
    windows_extended   integer NOT NULL CHECK (windows_extended >= 0),
    messages_unknown   integer NOT NULL CHECK (messages_unknown >= 0),
    replies_stale      integer NOT NULL CHECK (replies_stale >= 0),
    -- When the last customer message received before recovery was processed.
    backlog_cleared_at timestamptz CHECK (backlog_cleared_at >= started_at),
    UNIQUE NULLS NOT DISTINCT (tenant_id, outage_started_at)
);

CREATE INDEX recovery_runs_open_idx ON recovery_runs (tenant_id) WHERE backlog_cleared_at IS NULL;

SELECT enable_tenant_rls('recovery_runs');
GRANT SELECT, INSERT, UPDATE (backlog_cleared_at) ON recovery_runs TO app_rw;
