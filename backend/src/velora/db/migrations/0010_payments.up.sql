-- P8 (provider-neutral track, AD-25): payment verification (AD-03, AD-32).
--
-- * payments becomes an append-only ledger. An entry is either `verified` (staff confirmed,
--   with their PIN, that the money is in the restaurant's account) or `reversed` (the owner
--   withdrew a verification). A correction is a new row; nothing is updated or deleted.
-- * An order can become paid_staff_verified only while a verified, unreversed ledger entry
--   exists for it (trigger), so even a direct UPDATE can't mark an order paid.
-- * payment_evidence keeps what a customer sent about a payment (a screenshot, a document, an
--   "I have paid" message) for staff to look at. It never changes any status.
-- * COD blocklist (customers), and the restaurant's transfer account details (settings).
-- * The transition table gains the payment moves: a late payment after expiry (accept, or
--   refund) and the owner's reversal back to awaiting_transfer.

-- Payments ledger (P8-T1) -----------------------------------------------------------------

-- No code wrote payments before P8; only test seeds did, and they are recreated.
DELETE FROM payments;

ALTER TABLE payments
    DROP CONSTRAINT payments_status_check,
    DROP COLUMN updated_at,
    ALTER COLUMN status DROP DEFAULT,
    ADD CONSTRAINT payments_status_check CHECK (status IN ('verified', 'reversed')),
    -- The reference as typed (trimmed), and its comparison key (upper case, letters and
    -- digits only) for the per-tenant duplicate check.
    ADD COLUMN reference_key        text,
    -- The session that recorded the entry: the confirmer, or the shared kitchen device they
    -- confirmed on.
    ADD COLUMN recorded_by_staff_id uuid,
    -- A verification after the transfer window had passed (staff review, P8-T5).
    ADD COLUMN late                 boolean NOT NULL DEFAULT false,
    -- A reversal names the entry it withdraws, and why.
    ADD COLUMN corrects_payment_id  uuid,
    ADD COLUMN reason               text CHECK (reason IS NULL OR length(reason) <= 200),
    ADD CONSTRAINT payments_reference_check
        CHECK (reference IS NULL OR length(reference) BETWEEN 1 AND 64),
    ADD CONSTRAINT payments_reference_key_check
        CHECK ((reference IS NULL) = (reference_key IS NULL)
               AND (reference_key IS NULL OR reference_key ~ '^[A-Z0-9]{4,64}$')),
    ADD CONSTRAINT payments_reversal_check
        CHECK (CASE status
                   WHEN 'verified' THEN corrects_payment_id IS NULL
                   ELSE corrects_payment_id IS NOT NULL AND reason IS NOT NULL
                        AND verified_by_staff_id IS NOT NULL AND verified_at IS NOT NULL
               END),
    ADD CONSTRAINT payments_tenant_id_key UNIQUE (tenant_id, id),
    ADD CONSTRAINT payments_corrects_fk FOREIGN KEY (tenant_id, corrects_payment_id)
        REFERENCES payments (tenant_id, id) ON DELETE RESTRICT,
    ADD CONSTRAINT payments_recorded_by_fk FOREIGN KEY (tenant_id, recorded_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT;

-- An entry is reversed at most once.
CREATE UNIQUE INDEX payments_one_reversal ON payments (tenant_id, corrects_payment_id)
    WHERE status = 'reversed';
CREATE INDEX payments_reference_idx ON payments (tenant_id, reference_key)
    WHERE reference_key IS NOT NULL;
CREATE INDEX payments_verified_at_idx ON payments (tenant_id, verified_at);

REVOKE UPDATE ON payments FROM app_rw;
CREATE TRIGGER payments_immutable BEFORE UPDATE OR DELETE ON payments
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER payments_no_truncate BEFORE TRUNCATE ON payments
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Evidence from the customer (P8-T3) ------------------------------------------------------

CREATE TABLE payment_evidence (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    order_id         uuid NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('image', 'document', 'text_claim')),
    -- The provider's handle for the file; fetching it is the provider adapter's job (P5-T2).
    provider_media_id text CHECK (provider_media_id IS NULL OR length(provider_media_id) <= 256),
    mime_type        text CHECK (mime_type IS NULL OR length(mime_type) <= 100),
    -- The inbox event it came in on; one piece of evidence per message.
    inbox_event_id   uuid NOT NULL,
    received_at      timestamptz NOT NULL,
    CHECK ((kind = 'text_claim') = (provider_media_id IS NULL)),
    UNIQUE (tenant_id, inbox_event_id),
    FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX payment_evidence_order_idx ON payment_evidence (tenant_id, order_id);

SELECT enable_tenant_rls('payment_evidence');
GRANT SELECT, INSERT ON payment_evidence TO app_rw;
CREATE TRIGGER payment_evidence_immutable BEFORE UPDATE OR DELETE ON payment_evidence
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER payment_evidence_no_truncate BEFORE TRUNCATE ON payment_evidence
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- COD blocklist and transfer details (P8-T2, T3) ------------------------------------------

ALTER TABLE customers
    ADD COLUMN cod_blocked_at    timestamptz,
    ADD COLUMN cod_block_reason  text CHECK (cod_block_reason IS NULL
                                            OR length(cod_block_reason) BETWEEN 1 AND 200),
    ADD CONSTRAINT customers_cod_block_check
        CHECK ((cod_blocked_at IS NULL) = (cod_block_reason IS NULL));

ALTER TABLE ordering_settings
    -- Shown to the customer after a transfer order (bank or wallet, title, number). NULL:
    -- not set, so transfer can't be offered.
    ADD COLUMN transfer_account_details text
        CHECK (transfer_account_details IS NULL
               OR length(btrim(transfer_account_details)) BETWEEN 1 AND 500);

-- Transition guard, with the payment moves and the paid check (P8-T4, T5, T6) --------------

CREATE OR REPLACE FUNCTION guard_order_update() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    -- tenant_id is left to RLS, whose WITH CHECK already refuses moving a row to a tenant.
    IF (NEW.order_number, NEW.customer_id, NEW.pending_action_id,
        NEW.fulfilment, NEW.payment_method, NEW.subtotal_paisa, NEW.delivery_fee_paisa,
        NEW.tax_paisa, NEW.total_paisa, NEW.menu_version_id, NEW.tax_bps, NEW.cart_hash,
        NEW.capacity_slot, NEW.created_at)
       IS DISTINCT FROM
       (OLD.order_number, OLD.customer_id, OLD.pending_action_id,
        OLD.fulfilment, OLD.payment_method, OLD.subtotal_paisa, OLD.delivery_fee_paisa,
        OLD.tax_paisa, OLD.total_paisa, OLD.menu_version_id, OLD.tax_bps, OLD.cart_hash,
        OLD.capacity_slot, OLD.created_at)
    THEN
        RAISE EXCEPTION 'an order''s amounts and snapshot cannot change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status, NEW.status) IN (
            ('cod_confirmed', 'accepted'), ('cod_confirmed', 'rejected'),
            ('cod_confirmed', 'cancelled'),
            ('awaiting_transfer', 'paid_staff_verified'), ('awaiting_transfer', 'expired'),
            ('awaiting_transfer', 'cancelled'),
            ('paid_staff_verified', 'accepted'), ('paid_staff_verified', 'refund_pending'),
            ('accepted', 'preparing'),
            ('out_for_delivery', 'completed'), ('ready', 'completed'),
            ('refund_pending', 'cancelled'))
        OR ((OLD.status, NEW.status) IN (('accepted', 'cancelled'), ('preparing', 'cancelled'))
            AND NEW.payment_method = 'cod')
        OR ((OLD.status, NEW.status) IN (('accepted', 'refund_pending'),
                                         ('preparing', 'refund_pending'),
                                         ('paid_staff_verified', 'awaiting_transfer'),
                                         ('expired', 'paid_staff_verified'),
                                         ('expired', 'refund_pending'))
            AND NEW.payment_method = 'transfer')
        OR ((OLD.status, NEW.status) = ('preparing', 'out_for_delivery')
            AND NEW.fulfilment = 'delivery')
        OR ((OLD.status, NEW.status) = ('preparing', 'ready') AND NEW.fulfilment = 'takeaway'))
    THEN
        RAISE EXCEPTION 'order status % cannot change to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    -- AD-03: an order is paid only on a staff verification recorded in the ledger. A late
    -- payment that is refunded was still received, so it needs one too.
    IF NEW.status IS DISTINCT FROM OLD.status
       AND (NEW.status = 'paid_staff_verified'
            OR (OLD.status, NEW.status) = ('expired', 'refund_pending'))
       AND NOT EXISTS (
           SELECT 1 FROM velora.payments p
            WHERE p.order_id = NEW.id AND p.status = 'verified'
              AND NOT EXISTS (SELECT 1 FROM velora.payments r
                               WHERE r.corrects_payment_id = p.id AND r.status = 'reversed'))
    THEN
        RAISE EXCEPTION 'order % has no staff-verified payment', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
