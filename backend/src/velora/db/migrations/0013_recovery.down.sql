DROP TABLE recovery_runs;
DROP TABLE worker_heartbeats;

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE CONNECT ON DATABASE %I FROM velora_backup',
                              pg_catalog.current_database());
END
$$;
