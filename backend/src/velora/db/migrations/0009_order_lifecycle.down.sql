DROP POLICY dispatch_lookup ON orders;
REVOKE SELECT (tenant_id, status, payment_due_at) ON orders FROM velora_dispatcher;

DROP TRIGGER orders_guard ON orders;
ALTER TABLE order_transitions DROP COLUMN seq;
DROP FUNCTION guard_order_update();

DROP TRIGGER order_items_no_truncate ON order_items;
DROP TRIGGER order_items_immutable ON order_items;
GRANT UPDATE ON order_items TO app_rw;
ALTER TABLE order_items DROP COLUMN choices;

DROP INDEX orders_duplicate_idx;
DROP INDEX orders_payment_due_idx;
ALTER TABLE orders
    DROP CONSTRAINT orders_payment_due_check,
    DROP COLUMN payment_due_at,
    DROP COLUMN capacity_slot,
    DROP COLUMN cart_hash,
    DROP COLUMN tax_bps,
    DROP COLUMN menu_version_id;

DROP INDEX pending_actions_one_open;
ALTER TABLE pending_actions
    DROP CONSTRAINT pending_actions_customer_fk,
    DROP CONSTRAINT pending_actions_single_use_check,
    DROP CONSTRAINT pending_actions_void_check,
    DROP COLUMN void_reason,
    DROP COLUMN voided_at,
    DROP COLUMN cart_hash,
    DROP COLUMN customer_id;
