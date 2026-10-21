ALTER TABLE ordering_settings DROP COLUMN ai_enabled;

DROP INDEX conversations_handoff_idx;
ALTER TABLE conversations
    DROP CONSTRAINT conversations_handoff_check,
    DROP COLUMN handoff_reason,
    DROP COLUMN handoff_at,
    DROP CONSTRAINT conversations_state_check;

ALTER TABLE customers DROP COLUMN opted_out_at;
