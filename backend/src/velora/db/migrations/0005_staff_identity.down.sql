REVOKE ALL ON tenants, channels, consents, owner_verifications, audit_log, magic_links,
    staff_members, staff_sessions FROM velora_platform;
DROP POLICY platform_directory ON tenants;
DROP POLICY platform_scope ON tenants;
DROP POLICY platform_scope ON channels;
DROP POLICY platform_scope ON staff_members;
DROP POLICY platform_scope ON staff_sessions;
DROP POLICY platform_scope ON magic_links;
DROP POLICY platform_scope ON consents;
DROP POLICY platform_scope ON owner_verifications;
DROP POLICY platform_scope ON audit_log;
REVOKE ALL ON FUNCTION current_tenant_id() FROM velora_platform;
REVOKE ALL ON SCHEMA velora FROM velora_platform;

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE ALL ON DATABASE %I FROM velora_platform',
                              pg_catalog.current_database());
END
$$;

DROP TRIGGER tenants_go_live ON tenants;
DROP FUNCTION enforce_tenant_go_live();
DROP FUNCTION tenant_go_live_gaps(uuid);

DROP TRIGGER audit_log_no_truncate ON audit_log;
DROP TRIGGER audit_log_immutable ON audit_log;
DROP TRIGGER owner_verifications_no_truncate ON owner_verifications;
DROP TRIGGER owner_verifications_immutable ON owner_verifications;
DROP TRIGGER consents_no_truncate ON consents;
DROP TRIGGER consents_immutable ON consents;
DROP FUNCTION forbid_record_change();

GRANT UPDATE ON owner_verifications TO app_rw;

ALTER TABLE owner_verifications
    DROP CONSTRAINT owner_verifications_supersedes_once,
    DROP CONSTRAINT owner_verifications_supersedes_fkey,
    DROP CONSTRAINT owner_verifications_tenant_id_id_key,
    DROP COLUMN supersedes_id;

ALTER TABLE consents
    DROP CONSTRAINT consents_supersedes_once,
    DROP CONSTRAINT consents_supersedes_fkey,
    DROP CONSTRAINT consents_tenant_id_id_key,
    DROP COLUMN supersedes_id;

DROP INDEX staff_sessions_staff_idx;
DROP TABLE magic_links;

ALTER TABLE staff_members
    DROP CONSTRAINT staff_members_pin_set_at_check,
    DROP CONSTRAINT staff_members_device_has_no_pin,
    DROP COLUMN pin_locked_until,
    DROP COLUMN pin_failed_attempts,
    DROP COLUMN pin_set_at;

UPDATE staff_members SET role = 'staff' WHERE role = 'kitchen';
ALTER TABLE staff_members DROP CONSTRAINT staff_members_role_check;
ALTER TABLE staff_members ADD CONSTRAINT staff_members_role_check
    CHECK (role IN ('owner', 'manager', 'staff', 'kitchen_device'));
