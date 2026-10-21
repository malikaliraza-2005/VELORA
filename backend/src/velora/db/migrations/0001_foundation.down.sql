DROP SCHEMA velora CASCADE;

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE CONNECT ON DATABASE %I FROM app_rw', pg_catalog.current_database());
    EXECUTE pg_catalog.format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC', pg_catalog.current_database());
END
$$;
