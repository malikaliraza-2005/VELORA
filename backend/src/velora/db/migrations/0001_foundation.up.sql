-- P2-T1/T3: schema, the shared tenant expression and the RLS policy module.

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE ALL ON DATABASE %I FROM PUBLIC', pg_catalog.current_database());
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO app_rw', pg_catalog.current_database());
END
$$;

CREATE SCHEMA velora;
REVOKE ALL ON SCHEMA velora FROM PUBLIC;
GRANT USAGE ON SCHEMA velora TO app_rw, velora_resolver;

-- The single shared tenant expression. An unset or empty `app.tenant_id` yields NULL, so every
-- policy comparison is false: no rows are visible and no rows can be written (fail closed).
CREATE FUNCTION velora.current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT NULLIF(pg_catalog.current_setting('app.tenant_id', true), '')::uuid $$;

REVOKE ALL ON FUNCTION velora.current_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION velora.current_tenant_id() TO app_rw;

-- RLS policy module: every tenant table is enabled through this one function, so the policy
-- text is identical everywhere. Migration-time only; the runtime role cannot call it.
CREATE FUNCTION velora.enable_tenant_rls(p_table regclass, p_key text DEFAULT 'tenant_id')
    RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', p_table);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', p_table);
    EXECUTE format(
        'CREATE POLICY tenant_isolation ON %s AS PERMISSIVE FOR ALL TO app_rw '
        'USING (%I = velora.current_tenant_id()) '
        'WITH CHECK (%I = velora.current_tenant_id())',
        p_table, p_key, p_key
    );
END
$$;

REVOKE ALL ON FUNCTION velora.enable_tenant_rls(regclass, text) FROM PUBLIC;
