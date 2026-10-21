-- P2-T1: cluster roles for VELORA. Run once per PostgreSQL cluster by an administrator
-- (`python -m velora.db setup-roles`). Idempotent: safe to re-run, and it re-asserts the
-- attributes of roles that already exist.
--
-- Passwords are never set here. Set them out of band (for example `\password app_rw`).
--
--   velora_owner     Migrator. Owns the schema and every table. Never used at runtime.
--   app_rw           Runtime role. NOBYPASSRLS, owns nothing, subject to FORCE RLS.
--   velora_resolver  NOLOGIN. Owns only the bootstrap resolver function (SECURITY DEFINER),
--                    and may read only the channel lookup columns.
--   velora_platform  Founder onboarding tools (`python -m velora.admin`, P3). NOBYPASSRLS,
--                    owns nothing, works one tenant at a time under the tenant expression.
--   velora_dispatcher Worker work discovery (P5). NOBYPASSRLS, owns nothing. May read only
--                    tenant ids and queue-timing columns across tenants, never a payload.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'velora_owner') THEN
        CREATE ROLE velora_owner;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_rw') THEN
        CREATE ROLE app_rw;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'velora_resolver') THEN
        CREATE ROLE velora_resolver;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'velora_platform') THEN
        CREATE ROLE velora_platform;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'velora_dispatcher') THEN
        CREATE ROLE velora_dispatcher;
    END IF;
END
$$;

ALTER ROLE velora_owner
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;
ALTER ROLE app_rw
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE velora_resolver
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE velora_platform
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE velora_dispatcher
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

ALTER ROLE velora_owner SET search_path = velora;
ALTER ROLE app_rw SET search_path = velora;
ALTER ROLE velora_platform SET search_path = velora;
ALTER ROLE velora_dispatcher SET search_path = velora;

-- The migrator must be able to hand the resolver function to velora_resolver, but must not
-- inherit its privileges (or its RLS policy).
GRANT velora_resolver TO velora_owner WITH INHERIT FALSE, SET TRUE;
