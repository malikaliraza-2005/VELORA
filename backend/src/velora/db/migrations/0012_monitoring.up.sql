-- P10 (provider-neutral track, AD-25): monitoring and alerting (AD-34).
--
-- * velora_monitor (roles.sql) is the founder's monitor. Like velora_dispatcher it reads
--   across tenants through SELECT-only policies, and only the columns it needs for counts,
--   ages and tenant names: statuses, timestamps and ids. Never a payload, a message, an
--   address, an amount or a customer.
-- * handoff_events: one row each time a chat is handed to staff, written by a trigger, so
--   the daily pilot metrics can count handoffs (conversations keep only the current one).
-- * ingress_rejections: webhooks refused at ingress (bad signature, bad payload, another
--   session's events, an error while storing). Per tenant; no body or header is kept.
-- * backup_runs and alert_events are deliberately global and append-only: the backup job
--   (P11) reports each run here, and the monitor records each alert it sends and clears.
-- No SECURITY DEFINER function is added; the resolver stays the only one.

-- Handoffs (daily pilot metrics) ----------------------------------------------------------

CREATE TABLE handoff_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    conversation_id uuid NOT NULL,
    reason          text NOT NULL CHECK (reason IN ('requested', 'not_understood')),
    created_at      timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES conversations (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX handoff_events_created_idx ON handoff_events (created_at);

SELECT enable_tenant_rls('handoff_events');
GRANT SELECT, INSERT ON handoff_events TO app_rw;

CREATE TRIGGER handoff_events_immutable BEFORE UPDATE OR DELETE ON handoff_events
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER handoff_events_no_truncate BEFORE TRUNCATE ON handoff_events
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Runs as the caller (app_rw), so the row passes the same tenant policy as the update.
CREATE FUNCTION record_handoff() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    INSERT INTO velora.handoff_events (tenant_id, conversation_id, reason, created_at)
    VALUES (NEW.tenant_id, NEW.id, NEW.handoff_reason, NEW.handoff_at);
    RETURN NULL;
END
$$;

REVOKE ALL ON FUNCTION record_handoff() FROM PUBLIC;

CREATE TRIGGER conversations_handoff_started AFTER UPDATE OF handoff_at ON conversations
    FOR EACH ROW WHEN (OLD.handoff_at IS NULL AND NEW.handoff_at IS NOT NULL)
    EXECUTE FUNCTION record_handoff();
CREATE TRIGGER conversations_handoff_created AFTER INSERT ON conversations
    FOR EACH ROW WHEN (NEW.handoff_at IS NOT NULL)
    EXECUTE FUNCTION record_handoff();

-- Ingress rejections (ingress errors and HMAC failures) -----------------------------------

CREATE TABLE ingress_rejections (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    channel_id uuid NOT NULL,
    reason     text NOT NULL
               CHECK (reason IN ('signature', 'payload', 'session_mismatch', 'error')),
    created_at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, channel_id) REFERENCES channels (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX ingress_rejections_created_idx ON ingress_rejections (created_at);

SELECT enable_tenant_rls('ingress_rejections');
GRANT SELECT, INSERT ON ingress_rejections TO app_rw;

CREATE TRIGGER ingress_rejections_immutable BEFORE UPDATE OR DELETE ON ingress_rejections
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER ingress_rejections_no_truncate BEFORE TRUNCATE ON ingress_rejections
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Backup runs (global, append-only) -------------------------------------------------------

CREATE TABLE backup_runs (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq        bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    ok         boolean NOT NULL,
    -- What was backed up, e.g. "hourly-dump". No path, URL or key.
    kind       text NOT NULL CHECK (kind ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
    detail     text CHECK (detail IS NULL OR length(detail) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER backup_runs_immutable BEFORE UPDATE OR DELETE ON backup_runs
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER backup_runs_no_truncate BEFORE TRUNCATE ON backup_runs
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Alerts sent to the founder (global, append-only) ----------------------------------------

CREATE TABLE alert_events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq        bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    -- The rule and its subject, e.g. "channel_down:<channel id>".
    alert_key  text NOT NULL CHECK (alert_key ~ '^[a-z_]+(:[A-Za-z0-9-]+)?$'),
    severity   text NOT NULL CHECK (severity IN ('warning', 'critical')),
    state      text NOT NULL CHECK (state IN ('firing', 'resolved')),
    -- Grows when the problem does (e.g. more dead letters); a rise is notified again.
    level      integer NOT NULL DEFAULT 0 CHECK (level >= 0),
    summary    text NOT NULL CHECK (length(summary) BETWEEN 1 AND 300),
    -- Whether at least one notifier accepted it.
    delivered  boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX alert_events_key_idx ON alert_events (alert_key, seq);

CREATE TRIGGER alert_events_immutable BEFORE UPDATE OR DELETE ON alert_events
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER alert_events_no_truncate BEFORE TRUNCATE ON alert_events
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Monitor role ----------------------------------------------------------------------------

DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO velora_monitor',
                              pg_catalog.current_database());
END
$$;

GRANT USAGE ON SCHEMA velora TO velora_monitor;

GRANT SELECT (id, slug, display_name, status) ON tenants TO velora_monitor;
GRANT SELECT (id, tenant_id, provider, state, state_changed_at) ON channels TO velora_monitor;
GRANT SELECT (tenant_id, channel_id, event_type, details, created_at) ON channel_events
    TO velora_monitor;
GRANT SELECT (tenant_id, event_type, status, available_at, received_at) ON inbox_events
    TO velora_monitor;
GRANT SELECT (tenant_id, status, purpose, created_at, next_attempt_at) ON outbox_messages
    TO velora_monitor;
GRANT SELECT (tenant_id, conversation_id, status, payment_method, created_at) ON orders
    TO velora_monitor;
GRANT SELECT (tenant_id, status, verified_at) ON payments TO velora_monitor;
GRANT SELECT (tenant_id, conversation_id, created_at) ON handoff_events TO velora_monitor;
GRANT SELECT (tenant_id, reason, created_at) ON ingress_rejections TO velora_monitor;
GRANT SELECT ON global_pause_events TO velora_monitor;
GRANT SELECT, INSERT ON backup_runs, alert_events TO velora_monitor;

CREATE POLICY monitor_read ON tenants AS PERMISSIVE FOR SELECT TO velora_monitor USING (true);
CREATE POLICY monitor_read ON channels AS PERMISSIVE FOR SELECT TO velora_monitor USING (true);
CREATE POLICY monitor_read ON channel_events AS PERMISSIVE FOR SELECT TO velora_monitor
    USING (true);
CREATE POLICY monitor_read ON inbox_events AS PERMISSIVE FOR SELECT TO velora_monitor
    USING (true);
CREATE POLICY monitor_read ON outbox_messages AS PERMISSIVE FOR SELECT TO velora_monitor
    USING (true);
CREATE POLICY monitor_read ON orders AS PERMISSIVE FOR SELECT TO velora_monitor USING (true);
CREATE POLICY monitor_read ON payments AS PERMISSIVE FOR SELECT TO velora_monitor USING (true);
CREATE POLICY monitor_read ON handoff_events AS PERMISSIVE FOR SELECT TO velora_monitor
    USING (true);
CREATE POLICY monitor_read ON ingress_rejections AS PERMISSIVE FOR SELECT TO velora_monitor
    USING (true);
