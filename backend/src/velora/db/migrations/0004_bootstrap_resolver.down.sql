-- The migrator does not inherit velora_resolver's privileges, so it must switch role to drop
-- the function that role owns.
SET ROLE velora_resolver;
DROP FUNCTION resolve_channel_tenant(text, text);
RESET ROLE;

DROP POLICY resolver_lookup ON channels;
REVOKE ALL ON channels FROM velora_resolver;
