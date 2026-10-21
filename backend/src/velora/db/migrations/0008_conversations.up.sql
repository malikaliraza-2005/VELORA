-- P6 (provider-neutral track, AD-25): conversation workflows.
--
-- The conversation state machine lives in velora.conversation.engine; the CHECK below is the
-- final guard on its states. The cart and checkout details are in `context` (jsonb), never
-- prices: every amount is re-read from the published menu and settings when shown (AD-20).

-- STOP opt-out (AD-24). While set, the assistant answers nothing but START.
ALTER TABLE customers ADD COLUMN opted_out_at timestamptz;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_state_check
        CHECK (state IN ('idle', 'browsing', 'category', 'choosing', 'proposal', 'fulfilment',
                         'address', 'delivery_note', 'pickup_time', 'payment', 'summary',
                         'handoff')),
    -- Human handoff (P6-T6): set while staff own the chat; cleared by Resume bot.
    ADD COLUMN handoff_at timestamptz,
    ADD COLUMN handoff_reason text
        CHECK (handoff_reason IS NULL OR handoff_reason IN ('requested', 'not_understood')),
    ADD CONSTRAINT conversations_handoff_check
        CHECK ((state = 'handoff') = (handoff_at IS NOT NULL)
               AND (handoff_at IS NULL) = (handoff_reason IS NULL));

CREATE INDEX conversations_handoff_idx ON conversations (tenant_id, handoff_at)
    WHERE state = 'handoff';

-- AI proposals can be switched off per tenant (P6-T4); the numbered menu always works.
ALTER TABLE ordering_settings ADD COLUMN ai_enabled boolean NOT NULL DEFAULT true;
