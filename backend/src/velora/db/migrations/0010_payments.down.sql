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
                                         ('preparing', 'refund_pending'))
            AND NEW.payment_method = 'transfer')
        OR ((OLD.status, NEW.status) = ('preparing', 'out_for_delivery')
            AND NEW.fulfilment = 'delivery')
        OR ((OLD.status, NEW.status) = ('preparing', 'ready') AND NEW.fulfilment = 'takeaway'))
    THEN
        RAISE EXCEPTION 'order status % cannot change to %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER payment_evidence_no_truncate ON payment_evidence;
DROP TRIGGER payment_evidence_immutable ON payment_evidence;
DROP TABLE payment_evidence;

ALTER TABLE ordering_settings DROP COLUMN transfer_account_details;
ALTER TABLE customers
    DROP CONSTRAINT customers_cod_block_check,
    DROP COLUMN cod_block_reason,
    DROP COLUMN cod_blocked_at;

DROP TRIGGER payments_no_truncate ON payments;
DROP TRIGGER payments_immutable ON payments;
GRANT UPDATE ON payments TO app_rw;
DELETE FROM payments;
DROP INDEX payments_verified_at_idx;
DROP INDEX payments_reference_idx;
DROP INDEX payments_one_reversal;
ALTER TABLE payments
    DROP CONSTRAINT payments_recorded_by_fk,
    DROP CONSTRAINT payments_corrects_fk,
    DROP CONSTRAINT payments_tenant_id_key,
    DROP CONSTRAINT payments_reversal_check,
    DROP CONSTRAINT payments_reference_key_check,
    DROP CONSTRAINT payments_reference_check,
    DROP COLUMN reason,
    DROP COLUMN corrects_payment_id,
    DROP COLUMN late,
    DROP COLUMN recorded_by_staff_id,
    DROP COLUMN reference_key,
    DROP CONSTRAINT payments_status_check,
    ADD CONSTRAINT payments_status_check
        CHECK (status IN ('pending', 'customer_claimed', 'verified', 'rejected',
                          'refund_pending', 'refunded')),
    ALTER COLUMN status SET DEFAULT 'pending',
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
