REVOKE ALL ON FUNCTION anonymise_customer(uuid) FROM app_rw;
REVOKE ALL ON FUNCTION next_order_number() FROM app_rw;

DO $$
DECLARE
    t regclass;
BEGIN
    FOR t IN
        SELECT c.oid::regclass
          FROM pg_catalog.pg_class c
         WHERE c.relnamespace = 'velora'::regnamespace AND c.relkind = 'r'
    LOOP
        EXECUTE pg_catalog.format('REVOKE ALL ON %s FROM app_rw', t);
        EXECUTE pg_catalog.format('DROP POLICY IF EXISTS tenant_isolation ON %s', t);
        EXECUTE pg_catalog.format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', t);
        EXECUTE pg_catalog.format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', t);
    END LOOP;
END
$$;
