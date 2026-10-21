-- P5 (provider-neutral track, AD-25): channel state machine, inbox and outbox queue columns,
-- cross-tenant work discovery and the global pause.
--
-- Work discovery (AD-26 finding): under FORCE RLS, app_rw only ever sees one tenant, so it
-- can't find which tenants have queued work. velora_dispatcher (roles.sql) is a login role
-- that may read only tenant_id and the queue-timing columns, never a payload, through
-- SELECT-only policies. The worker then does the work as app_rw inside one tenant's scope.
-- No SECURITY DEFINER function is added; the resolver stays the only one.

-- Channel state machine (P5-T5) -----------------------------------------------------------

ALTER TABLE channels DROP CONSTRAINT channels_state_check;
UPDATE channels SET state = CASE state
    WHEN 'connecting' THEN 'reconnecting'
    WHEN 'disconnected' THEN 'reconnecting'
    WHEN 'paused' THEN 'failed'
    WHEN 'banned' THEN 'suspected_restricted'
    ELSE state END;
ALTER TABLE channels ADD CONSTRAINT channels_state_check
    CHECK (state IN ('pending', 'connected', 'reconnecting', 'needs_qr', 'logged_out', 'failed',
                     'suspected_restricted', 'retired'));
-- When the provider last reported the current state. Older reports are ignored.
ALTER TABLE channels ADD COLUMN state_changed_at timestamptz;

-- Inbox (P5-T3) ---------------------------------------------------------------------------

ALTER TABLE inbox_events
    ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY,
    -- Events with the same key (one customer on one channel) are processed one at a time, in
    -- arrival order (AD-20). NULL means no ordering constraint.
    ADD COLUMN ordering_key text,
    -- Identifies the current lease. A worker whose lease expired can't complete the event.
    ADD COLUMN claim_token uuid,
    ADD CONSTRAINT inbox_events_event_type_check
        CHECK (event_type IN ('message', 'delivery_status')),
    ADD CONSTRAINT inbox_events_lease_check
        CHECK (status <> 'processing' OR (claim_token IS NOT NULL AND locked_until IS NOT NULL));

CREATE INDEX inbox_events_ordering_idx ON inbox_events (tenant_id, channel_id, ordering_key, seq)
    WHERE status IN ('pending', 'processing');

-- Outbox (P5-T4) --------------------------------------------------------------------------

ALTER TABLE outbox_messages DROP CONSTRAINT outbox_messages_status_check;
ALTER TABLE outbox_messages
    ADD CONSTRAINT outbox_messages_status_check
        CHECK (status IN ('pending', 'waiting_for_channel', 'sending', 'sent', 'delivered',
                          'read', 'failed', 'unknown', 'stale', 'cancelled')),
    ADD CONSTRAINT outbox_messages_kind_check CHECK (kind IN ('text', 'media')),
    ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY,
    -- The recipient: messages to one recipient on one channel are sent in order.
    ADD COLUMN ordering_key text,
    -- Conversational replies go stale (replaced by one "we're back" message); status
    -- updates are always sent, in order.
    ADD COLUMN purpose text NOT NULL DEFAULT 'conversational'
        CHECK (purpose IN ('conversational', 'status_update')),
    -- A critical message whose send outcome is unknown is never re-sent automatically.
    ADD COLUMN critical boolean NOT NULL DEFAULT false,
    ADD COLUMN claim_token uuid,
    ADD CONSTRAINT outbox_messages_lease_check
        CHECK (status <> 'sending' OR (claim_token IS NOT NULL AND locked_until IS NOT NULL));

CREATE INDEX outbox_messages_ordering_idx ON outbox_messages (tenant_id, channel_id, seq)
    WHERE status IN ('pending', 'waiting_for_channel', 'sending');
CREATE INDEX outbox_messages_provider_id_idx
    ON outbox_messages (tenant_id, channel_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

-- Global pause (P5-T5, AD-19) -------------------------------------------------------------

-- Deliberately global (no tenant): one switch that stops new orders for every tenant. The
-- newest row is the current state. Append-only, like the other records.
CREATE TABLE global_pause_events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq        bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    paused     boolean NOT NULL,
    reason     text CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 200),
    operator   text NOT NULL CHECK (length(operator) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT paused OR reason IS NOT NULL)
);

CREATE TRIGGER global_pause_events_immutable BEFORE UPDATE OR DELETE ON global_pause_events
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER global_pause_events_no_truncate BEFORE TRUNCATE ON global_pause_events
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

GRANT SELECT ON global_pause_events TO app_rw;
GRANT SELECT, INSERT ON global_pause_events TO velora_platform;

-- Runtime grants --------------------------------------------------------------------------

GRANT UPDATE (state, state_changed_at, updated_at) ON channels TO app_rw;

-- Work discovery role ---------------------------------------------------------------------

DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO velora_dispatcher',
                              pg_catalog.current_database());
END
$$;

GRANT USAGE ON SCHEMA velora TO velora_dispatcher;
GRANT SELECT (tenant_id, event_type, status, available_at, locked_until) ON inbox_events
    TO velora_dispatcher;
GRANT SELECT (tenant_id, status, next_attempt_at, locked_until) ON outbox_messages
    TO velora_dispatcher;
GRANT SELECT (tenant_id, state) ON channels TO velora_dispatcher;
CREATE POLICY dispatch_lookup ON inbox_events AS PERMISSIVE FOR SELECT TO velora_dispatcher
    USING (true);
CREATE POLICY dispatch_lookup ON outbox_messages AS PERMISSIVE FOR SELECT TO velora_dispatcher
    USING (true);
CREATE POLICY dispatch_lookup ON channels AS PERMISSIVE FOR SELECT TO velora_dispatcher
    USING (true);
