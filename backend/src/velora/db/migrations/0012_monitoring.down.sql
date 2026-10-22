DROP POLICY monitor_read ON ingress_rejections;
DROP POLICY monitor_read ON handoff_events;
DROP POLICY monitor_read ON payments;
DROP POLICY monitor_read ON orders;
DROP POLICY monitor_read ON outbox_messages;
DROP POLICY monitor_read ON inbox_events;
DROP POLICY monitor_read ON channel_events;
DROP POLICY monitor_read ON channels;
DROP POLICY monitor_read ON tenants;

REVOKE ALL ON tenants, channels, channel_events, inbox_events, outbox_messages, orders,
    payments, global_pause_events FROM velora_monitor;
REVOKE USAGE ON SCHEMA velora FROM velora_monitor;
DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE CONNECT ON DATABASE %I FROM velora_monitor',
                              pg_catalog.current_database());
END
$$;

DROP TABLE alert_events;
DROP TABLE backup_runs;
DROP TABLE ingress_rejections;
DROP TRIGGER conversations_handoff_created ON conversations;
DROP TRIGGER conversations_handoff_started ON conversations;
DROP FUNCTION record_handoff();
DROP TABLE handoff_events;
