-- P2-T4: bootstrap resolver: provider session reference -> tenant_id.
--
-- This is the only SECURITY DEFINER function in the database (a catalog test enforces it).
-- It runs as velora_resolver, a NOLOGIN role that can read only the channel lookup columns,
-- through a SELECT-only policy that applies to no other role. It returns nothing but the
-- tenant_id, and only app_rw may execute it.

GRANT SELECT (tenant_id, provider, session_ref, state) ON channels TO velora_resolver;
CREATE POLICY resolver_lookup ON channels AS PERMISSIVE FOR SELECT TO velora_resolver
    USING (true);

CREATE FUNCTION resolve_channel_tenant(p_provider text, p_session_ref text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT c.tenant_id
      FROM velora.channels c
     WHERE c.provider = p_provider
       AND c.session_ref = p_session_ref
       AND c.state <> 'retired'
$$;

REVOKE ALL ON FUNCTION resolve_channel_tenant(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_channel_tenant(text, text) TO app_rw;

-- Hand the function to velora_resolver last: after this the migrator can no longer grant on
-- it. Changing a function's owner requires the new owner to hold CREATE on the schema, so
-- that is granted only for the hand-over.
GRANT CREATE ON SCHEMA velora TO velora_resolver;
ALTER FUNCTION resolve_channel_tenant(text, text) OWNER TO velora_resolver;
REVOKE CREATE ON SCHEMA velora FROM velora_resolver;
