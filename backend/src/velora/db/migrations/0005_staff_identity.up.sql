-- P3: onboarding records, staff identity and the platform (founder) role.
--
--   * Staff roles become owner / manager / kitchen / kitchen_device (P3-T4).
--   * magic_links: single-use login links, 15 minutes or less, bound to one staff member.
--   * Payment PIN lockout counters on staff_members (P3-T5).
--   * Consents, owner verifications and the audit log can't be changed once saved; a
--     correction is a new record that supersedes the old one (P3-T1, P3-T6).
--   * A tenant can't go live without an effective verified owner verification and an
--     effective ban-risk consent (P3-T1, AD-12, AD-18). Enforced here, not only in code.
--   * velora_platform: the founder's onboarding role. NOBYPASSRLS; it works one tenant at a
--     time under the same tenant expression, and may only list tenants across the platform.

-- Staff roles and PIN lockout -------------------------------------------------------------

UPDATE staff_members SET role = 'kitchen' WHERE role = 'staff';
ALTER TABLE staff_members DROP CONSTRAINT staff_members_role_check;
ALTER TABLE staff_members ADD CONSTRAINT staff_members_role_check
    CHECK (role IN ('owner', 'manager', 'kitchen', 'kitchen_device'));

ALTER TABLE staff_members
    ADD COLUMN pin_set_at          timestamptz,
    ADD COLUMN pin_failed_attempts integer NOT NULL DEFAULT 0 CHECK (pin_failed_attempts >= 0),
    ADD COLUMN pin_locked_until    timestamptz,
    -- A shared kitchen tablet is not a person, so it never holds a personal payment PIN.
    ADD CONSTRAINT staff_members_device_has_no_pin
        CHECK (role <> 'kitchen_device' OR pin_hash IS NULL),
    ADD CONSTRAINT staff_members_pin_set_at_check
        CHECK ((pin_hash IS NULL) = (pin_set_at IS NULL));

-- Magic links -----------------------------------------------------------------------------

CREATE TABLE magic_links (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL,
    staff_member_id    uuid NOT NULL,
    token_hash         bytea NOT NULL UNIQUE,
    purpose            text NOT NULL CHECK (purpose IN ('invite', 'login')),
    issued_by_kind     text NOT NULL CHECK (issued_by_kind IN ('platform', 'staff')),
    issued_by_staff_id uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    consumed_at        timestamptz,
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '15 minutes'),
    CHECK ((issued_by_kind = 'staff') = (issued_by_staff_id IS NOT NULL)),
    FOREIGN KEY (tenant_id, staff_member_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, issued_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX magic_links_staff_idx ON magic_links (tenant_id, staff_member_id);

SELECT enable_tenant_rls('magic_links');

CREATE INDEX staff_sessions_staff_idx ON staff_sessions (tenant_id, staff_member_id)
    WHERE revoked_at IS NULL;

-- Immutable onboarding records and audit log ----------------------------------------------

ALTER TABLE consents
    ADD COLUMN supersedes_id uuid,
    ADD CONSTRAINT consents_tenant_id_id_key UNIQUE (tenant_id, id),
    ADD CONSTRAINT consents_supersedes_fkey FOREIGN KEY (tenant_id, supersedes_id)
        REFERENCES consents (tenant_id, id) ON DELETE RESTRICT,
    ADD CONSTRAINT consents_supersedes_once UNIQUE (tenant_id, supersedes_id);

ALTER TABLE owner_verifications
    ADD COLUMN supersedes_id uuid,
    ADD CONSTRAINT owner_verifications_tenant_id_id_key UNIQUE (tenant_id, id),
    ADD CONSTRAINT owner_verifications_supersedes_fkey FOREIGN KEY (tenant_id, supersedes_id)
        REFERENCES owner_verifications (tenant_id, id) ON DELETE RESTRICT,
    ADD CONSTRAINT owner_verifications_supersedes_once UNIQUE (tenant_id, supersedes_id);

-- A verification decision is a new record now, never an update.
REVOKE UPDATE ON owner_verifications FROM app_rw;

CREATE FUNCTION forbid_record_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION '% rows cannot be changed or deleted; record a correction instead',
        TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END
$$;

REVOKE ALL ON FUNCTION forbid_record_change() FROM PUBLIC;

CREATE TRIGGER consents_immutable BEFORE UPDATE OR DELETE ON consents
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER consents_no_truncate BEFORE TRUNCATE ON consents
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER owner_verifications_immutable BEFORE UPDATE OR DELETE ON owner_verifications
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER owner_verifications_no_truncate BEFORE TRUNCATE ON owner_verifications
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- Go-live readiness -----------------------------------------------------------------------

-- What a tenant still lacks before it may go live. A record counts only while no later
-- record supersedes it. Runs as the caller, so RLS limits it to the current tenant.
CREATE FUNCTION tenant_go_live_gaps(p_tenant uuid) RETURNS text[]
    LANGUAGE sql STABLE
    SET search_path = pg_catalog
AS $$
    SELECT array_remove(ARRAY[
        CASE WHEN EXISTS (
            SELECT 1 FROM velora.owner_verifications v
             WHERE v.tenant_id = p_tenant AND v.status = 'verified'
               AND NOT EXISTS (SELECT 1 FROM velora.owner_verifications s
                                WHERE s.tenant_id = v.tenant_id AND s.supersedes_id = v.id)
        ) THEN NULL ELSE 'owner_verification' END,
        CASE WHEN EXISTS (
            SELECT 1 FROM velora.consents c
             WHERE c.tenant_id = p_tenant AND c.kind = 'separate_number_ban_risk'
               AND NOT EXISTS (SELECT 1 FROM velora.consents s
                                WHERE s.tenant_id = c.tenant_id AND s.supersedes_id = c.id)
        ) THEN NULL ELSE 'consent_separate_number_ban_risk' END
    ], NULL)
$$;

CREATE FUNCTION enforce_tenant_go_live() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
DECLARE
    gaps text[];
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status = 'active' THEN
        RETURN NEW;
    END IF;
    gaps := velora.tenant_go_live_gaps(NEW.id);
    IF cardinality(gaps) > 0 THEN
        RAISE EXCEPTION 'tenant % cannot go live; missing: %', NEW.slug,
            array_to_string(gaps, ', ') USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION tenant_go_live_gaps(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION enforce_tenant_go_live() FROM PUBLIC;

CREATE TRIGGER tenants_go_live BEFORE INSERT OR UPDATE OF status ON tenants
    FOR EACH ROW WHEN (NEW.status = 'active') EXECUTE FUNCTION enforce_tenant_go_live();

-- Runtime role grants ---------------------------------------------------------------------

GRANT SELECT, INSERT, DELETE, UPDATE (consumed_at) ON magic_links TO app_rw;
GRANT EXECUTE ON FUNCTION tenant_go_live_gaps(uuid) TO app_rw;

-- Platform (founder onboarding) role ------------------------------------------------------

DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO velora_platform',
                              pg_catalog.current_database());
END
$$;

GRANT USAGE ON SCHEMA velora TO velora_platform;
GRANT EXECUTE ON FUNCTION current_tenant_id() TO velora_platform;
GRANT EXECUTE ON FUNCTION tenant_go_live_gaps(uuid) TO velora_platform;

-- The same tenant expression as tenant_isolation, for the platform role.
DO $$
DECLARE
    t text;
BEGIN
    EXECUTE 'CREATE POLICY platform_scope ON velora.tenants AS PERMISSIVE FOR ALL '
            'TO velora_platform USING (id = velora.current_tenant_id()) '
            'WITH CHECK (id = velora.current_tenant_id())';
    FOREACH t IN ARRAY ARRAY['channels', 'staff_members', 'staff_sessions', 'magic_links',
                             'consents', 'owner_verifications', 'audit_log']
    LOOP
        EXECUTE pg_catalog.format(
            'CREATE POLICY platform_scope ON velora.%I AS PERMISSIVE FOR ALL '
            'TO velora_platform USING (tenant_id = velora.current_tenant_id()) '
            'WITH CHECK (tenant_id = velora.current_tenant_id())', t);
    END LOOP;
END
$$;

-- The founder may list every tenant (slug, name, status) to pick one to work on.
CREATE POLICY platform_directory ON tenants AS PERMISSIVE FOR SELECT TO velora_platform
    USING (true);

GRANT SELECT, INSERT, UPDATE (display_name, status, updated_at) ON tenants TO velora_platform;
GRANT SELECT, INSERT ON channels, consents, owner_verifications, audit_log, magic_links
    TO velora_platform;
GRANT SELECT, INSERT, UPDATE (active, updated_at) ON staff_members TO velora_platform;
GRANT SELECT, UPDATE (revoked_at, revoked_reason) ON staff_sessions TO velora_platform;
