-- P7 (provider-neutral track, AD-25): the order lifecycle.
--
-- * pending_actions become the YES confirmation nonce (AD-20): bound to the tenant (RLS), the
--   conversation, the customer and a hash of the exact checkout, single-use and expiring. At
--   most one is open per conversation; a new summary voids the previous one.
-- * orders keep a snapshot of what was confirmed: the menu version, the tax rate, the cart
--   hash (for the possible-duplicate flag), the capacity slot it took and, for transfers, when
--   payment is due. Money and snapshot columns can't change after the order is created.
-- * order_items keep the chosen options and are append-only: no UPDATE grant, and a trigger.
-- * The status transition table is enforced here too (defence in depth): velora.orders.
--   lifecycle.TRANSITIONS is the source, and a test checks the two agree.
-- * velora_dispatcher may see which tenants have a transfer window due, and nothing else.

-- Confirmation nonces (P7-T1) -------------------------------------------------------------

-- P6 never wrote a pending action, so there are no rows to backfill.
ALTER TABLE pending_actions
    ADD COLUMN customer_id uuid NOT NULL,
    ADD COLUMN cart_hash   bytea NOT NULL CHECK (octet_length(cart_hash) = 32),
    ADD COLUMN voided_at   timestamptz,
    ADD COLUMN void_reason text CHECK (void_reason IN ('superseded', 'recovery')),
    ADD CONSTRAINT pending_actions_void_check
        CHECK ((voided_at IS NULL) = (void_reason IS NULL)),
    ADD CONSTRAINT pending_actions_single_use_check
        CHECK (consumed_at IS NULL OR voided_at IS NULL),
    ADD CONSTRAINT pending_actions_customer_fk FOREIGN KEY (tenant_id, customer_id)
        REFERENCES customers (tenant_id, id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX pending_actions_one_open ON pending_actions (tenant_id, conversation_id)
    WHERE consumed_at IS NULL AND voided_at IS NULL;

-- Order snapshots (P7-T2) -----------------------------------------------------------------

ALTER TABLE orders
    ADD COLUMN menu_version_id uuid,
    ADD COLUMN tax_bps         integer CHECK (tax_bps BETWEEN 0 AND 5000),
    ADD COLUMN cart_hash       bytea CHECK (octet_length(cart_hash) = 32),
    ADD COLUMN capacity_slot   timestamptz,
    -- Transfer orders only: when an unpaid order expires (P7-T4). P11 recovery may extend it.
    ADD COLUMN payment_due_at  timestamptz,
    ADD CONSTRAINT orders_payment_due_check
        CHECK (status <> 'awaiting_transfer' OR payment_due_at IS NOT NULL);

CREATE INDEX orders_payment_due_idx ON orders (payment_due_at) WHERE status = 'awaiting_transfer';
CREATE INDEX orders_duplicate_idx ON orders (tenant_id, customer_id, cart_hash, created_at);

ALTER TABLE order_items ADD COLUMN choices jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(choices) = 'array');

REVOKE UPDATE ON order_items FROM app_rw;
CREATE TRIGGER order_items_immutable BEFORE UPDATE OR DELETE ON order_items
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER order_items_no_truncate BEFORE TRUNCATE ON order_items
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Transition guard (P7-T3) ----------------------------------------------------------------

-- The order of an order's history, even when two moves share a timestamp.
ALTER TABLE order_transitions ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY;

CREATE FUNCTION guard_order_update() RETURNS trigger
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

REVOKE ALL ON FUNCTION guard_order_update() FROM PUBLIC;

CREATE TRIGGER orders_guard BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION guard_order_update();

-- Timer discovery (P7-T4) -----------------------------------------------------------------

GRANT SELECT (tenant_id, status, payment_due_at) ON orders TO velora_dispatcher;
CREATE POLICY dispatch_lookup ON orders AS PERMISSIVE FOR SELECT TO velora_dispatcher
    USING (true);
