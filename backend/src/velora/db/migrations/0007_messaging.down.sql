DROP POLICY dispatch_lookup ON channels;
DROP POLICY dispatch_lookup ON outbox_messages;
DROP POLICY dispatch_lookup ON inbox_events;
REVOKE ALL ON channels, outbox_messages, inbox_events FROM velora_dispatcher;
REVOKE USAGE ON SCHEMA velora FROM velora_dispatcher;
DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE CONNECT ON DATABASE %I FROM velora_dispatcher',
                              pg_catalog.current_database());
END
$$;

REVOKE UPDATE (state, state_changed_at, updated_at) ON channels FROM app_rw;

DROP TABLE global_pause_events;

DROP INDEX outbox_messages_provider_id_idx;
DROP INDEX outbox_messages_ordering_idx;
ALTER TABLE outbox_messages
    DROP CONSTRAINT outbox_messages_lease_check,
    DROP COLUMN claim_token,
    DROP COLUMN critical,
    DROP COLUMN purpose,
    DROP COLUMN ordering_key,
    DROP COLUMN seq,
    DROP CONSTRAINT outbox_messages_kind_check,
    DROP CONSTRAINT outbox_messages_status_check;
UPDATE outbox_messages SET status = CASE status
    WHEN 'waiting_for_channel' THEN 'pending'
    WHEN 'delivered' THEN 'sent'
    WHEN 'read' THEN 'sent'
    WHEN 'unknown' THEN 'failed'
    ELSE status END;
ALTER TABLE outbox_messages ADD CONSTRAINT outbox_messages_status_check
    CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'stale', 'cancelled'));

DROP INDEX inbox_events_ordering_idx;
ALTER TABLE inbox_events
    DROP CONSTRAINT inbox_events_lease_check,
    DROP CONSTRAINT inbox_events_event_type_check,
    DROP COLUMN claim_token,
    DROP COLUMN ordering_key,
    DROP COLUMN seq;

ALTER TABLE channels DROP COLUMN state_changed_at;
ALTER TABLE channels DROP CONSTRAINT channels_state_check;
UPDATE channels SET state = CASE state
    WHEN 'reconnecting' THEN 'disconnected'
    WHEN 'needs_qr' THEN 'disconnected'
    WHEN 'logged_out' THEN 'disconnected'
    WHEN 'failed' THEN 'disconnected'
    WHEN 'suspected_restricted' THEN 'banned'
    ELSE state END;
ALTER TABLE channels ADD CONSTRAINT channels_state_check
    CHECK (state IN ('pending', 'connecting', 'connected', 'disconnected', 'paused', 'banned',
                     'retired'));
