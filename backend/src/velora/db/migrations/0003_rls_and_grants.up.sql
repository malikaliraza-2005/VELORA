-- P2-T3: FORCE RLS on every tenant table, through the shared policy module (0001), and the
-- runtime role's grants. The CI enumeration test fails if a table is missed.

SELECT enable_tenant_rls('tenants', 'id');
SELECT enable_tenant_rls(t)
  FROM unnest(ARRAY[
      'channels', 'staff_members', 'staff_sessions', 'consents', 'owner_verifications',
      'customers', 'conversations', 'inbox_events', 'outbox_messages', 'pending_actions',
      'orders', 'order_items', 'order_transitions', 'payments', 'audit_log', 'channel_events'
  ]::regclass[]) AS t;

-- Tenants are created by onboarding (P3); at runtime a tenant may only read itself and
-- allocate order numbers.
GRANT SELECT, UPDATE (display_name, next_order_number, updated_at) ON tenants TO app_rw;

GRANT SELECT, INSERT, UPDATE ON
    channels, staff_members, owner_verifications, conversations, orders, order_items,
    payments
TO app_rw;

-- DELETE only where removing a row is a normal operation: customers without history,
-- expired sessions and confirmations, and queue retention.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    customers, staff_sessions, pending_actions, inbox_events, outbox_messages
TO app_rw;

-- Append-only history.
GRANT SELECT, INSERT ON consents, order_transitions, audit_log, channel_events TO app_rw;

REVOKE ALL ON FUNCTION next_order_number() FROM PUBLIC;
REVOKE ALL ON FUNCTION anonymise_customer(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION next_order_number() TO app_rw;
GRANT EXECUTE ON FUNCTION anonymise_customer(uuid) TO app_rw;
