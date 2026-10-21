-- P4: menu catalogue with draft / publish / version history, availability, ordering settings,
-- pause and the per-slot capacity counter.
--
--   * A menu version is a complete catalogue: categories, items, modifier groups and options,
--     all carrying integer paisa prices. Each tenant has at most one draft version.
--   * Publishing freezes a version. A frozen version and its rows can never change again
--     (triggers below), so a live menu can't be edited in place and an old one can be
--     re-published exactly as it was (P4-T3).
--   * menu_publications is the append-only publication history. The newest row is the live
--     menu. Only a frozen version can be published, and only the runtime role may publish:
--     the founder's onboarding tool can fill a draft but not put it live (AD-28).
--   * Availability is per SKU, outside the versions, so a toggle applies at once (P4-T4).
--   * ordering_settings holds one row per tenant; the services create it on first use.
--   * order_capacity_slots counts orders per 15-minute slot; P7 claims a place atomically.

-- Menu versions ---------------------------------------------------------------------------

CREATE TABLE menu_versions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    version_no          integer NOT NULL CHECK (version_no > 0),
    -- Bumped on every draft save; publishing must name the revision that was reviewed.
    revision            integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by_kind     text NOT NULL CHECK (created_by_kind IN ('staff', 'platform')),
    created_by_staff_id uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- NULL while the version is the draft; set once, when it is first published.
    frozen_at           timestamptz,
    CHECK ((created_by_kind = 'staff') = (created_by_staff_id IS NOT NULL)),
    UNIQUE (tenant_id, version_no),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX menu_versions_one_draft ON menu_versions (tenant_id) WHERE frozen_at IS NULL;

CREATE TABLE menu_categories (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    menu_version_id uuid NOT NULL,
    position        integer NOT NULL CHECK (position >= 0),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    UNIQUE (tenant_id, menu_version_id, name),
    UNIQUE (tenant_id, menu_version_id, id),
    FOREIGN KEY (tenant_id, menu_version_id)
        REFERENCES menu_versions (tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE menu_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    menu_version_id uuid NOT NULL,
    category_id     uuid NOT NULL,
    position        integer NOT NULL CHECK (position >= 0),
    -- The stable identity of an item across versions; availability and order snapshots use it.
    sku             text NOT NULL CHECK (sku ~ '^[A-Z0-9][A-Z0-9_-]{0,39}$'),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description     text CHECK (length(description) <= 500),
    price_paisa     bigint NOT NULL CHECK (price_paisa >= 0),
    UNIQUE (tenant_id, menu_version_id, sku),
    UNIQUE (tenant_id, menu_version_id, id),
    FOREIGN KEY (tenant_id, menu_version_id, category_id)
        REFERENCES menu_categories (tenant_id, menu_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE modifier_groups (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    menu_version_id uuid NOT NULL,
    item_id         uuid NOT NULL,
    position        integer NOT NULL CHECK (position >= 0),
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    min_select      integer NOT NULL CHECK (min_select >= 0),
    max_select      integer NOT NULL CHECK (max_select >= 1),
    CHECK (min_select <= max_select),
    UNIQUE (tenant_id, menu_version_id, item_id, name),
    UNIQUE (tenant_id, menu_version_id, id),
    FOREIGN KEY (tenant_id, menu_version_id, item_id)
        REFERENCES menu_items (tenant_id, menu_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE modifier_options (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL,
    menu_version_id   uuid NOT NULL,
    group_id          uuid NOT NULL,
    position          integer NOT NULL CHECK (position >= 0),
    name              text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    price_delta_paisa bigint NOT NULL CHECK (price_delta_paisa >= 0),
    UNIQUE (tenant_id, menu_version_id, group_id, name),
    FOREIGN KEY (tenant_id, menu_version_id, group_id)
        REFERENCES modifier_groups (tenant_id, menu_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE menu_publications (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL,
    publication_no        integer NOT NULL CHECK (publication_no > 0),
    menu_version_id       uuid NOT NULL,
    published_by_staff_id uuid NOT NULL,
    published_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, publication_no),
    FOREIGN KEY (tenant_id, menu_version_id)
        REFERENCES menu_versions (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, published_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

-- Availability ----------------------------------------------------------------------------

CREATE TABLE menu_item_availability (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    sku                 text NOT NULL CHECK (sku ~ '^[A-Z0-9][A-Z0-9_-]{0,39}$'),
    is_available        boolean NOT NULL,
    updated_by_staff_id uuid NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, sku),
    FOREIGN KEY (tenant_id, updated_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

-- Ordering settings, pause and capacity ---------------------------------------------------

CREATE TABLE ordering_settings (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   uuid NOT NULL UNIQUE REFERENCES tenants (id) ON DELETE RESTRICT,
    delivery_enabled            boolean NOT NULL DEFAULT true,
    takeaway_enabled            boolean NOT NULL DEFAULT true,
    delivery_fee_paisa          bigint NOT NULL DEFAULT 0
                                CHECK (delivery_fee_paisa BETWEEN 0 AND 1000000),
    delivery_min_order_paisa    bigint NOT NULL DEFAULT 0
                                CHECK (delivery_min_order_paisa BETWEEN 0 AND 10000000),
    pickup_min_lead_minutes     integer NOT NULL DEFAULT 20
                                CHECK (pickup_min_lead_minutes BETWEEN 0 AND 240),
    pickup_max_ahead_minutes    integer NOT NULL DEFAULT 240
                                CHECK (pickup_max_ahead_minutes BETWEEN 1 AND 1440),
    -- Tax in basis points of the item subtotal, by payment method. NULL means not set, and
    -- pricing then refuses to quote that method. Values come from the accountant (B3).
    tax_cod_bps                 integer CHECK (tax_cod_bps BETWEEN 0 AND 5000),
    tax_transfer_bps            integer CHECK (tax_transfer_bps BETWEEN 0 AND 5000),
    cod_enabled                 boolean NOT NULL DEFAULT true,
    transfer_enabled            boolean NOT NULL DEFAULT true,
    -- AD-03: the most a customer's first COD order may total. NULL means no limit.
    cod_first_order_limit_paisa bigint CHECK (cod_first_order_limit_paisa BETWEEN 1 AND 100000000),
    transfer_window_minutes     integer NOT NULL DEFAULT 30
                                CHECK (transfer_window_minutes BETWEEN 5 AND 1440),
    -- Orders per 15-minute slot. NULL means no cap.
    max_orders_per_slot         integer CHECK (max_orders_per_slot BETWEEN 1 AND 500),
    paused_until                timestamptz,
    pause_reason                text CHECK (length(pause_reason) <= 200),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    CHECK (delivery_enabled OR takeaway_enabled),
    CHECK (cod_enabled OR transfer_enabled),
    CHECK (pickup_max_ahead_minutes >= pickup_min_lead_minutes)
);

CREATE TABLE order_capacity_slots (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    slot_start   timestamptz NOT NULL
                 CHECK (extract(epoch FROM slot_start)::bigint % 900 = 0),
    orders_count integer NOT NULL CHECK (orders_count >= 0),
    UNIQUE (tenant_id, slot_start)
);

-- Frozen versions can't change ------------------------------------------------------------

-- Runs as the caller, so the lookup sees only the caller's tenant: the row being written.
CREATE FUNCTION forbid_frozen_menu_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NEW;
    END IF;
    IF (TG_OP <> 'INSERT' AND EXISTS (
            SELECT 1 FROM velora.menu_versions v
             WHERE v.id = OLD.menu_version_id AND v.frozen_at IS NOT NULL))
       OR (TG_OP <> 'DELETE' AND EXISTS (
            SELECT 1 FROM velora.menu_versions v
             WHERE v.id = NEW.menu_version_id AND v.frozen_at IS NOT NULL)) THEN
        RAISE EXCEPTION 'a published menu version cannot be changed; edit the draft instead'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$$;

CREATE FUNCTION forbid_frozen_version_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL AND (TG_OP = 'DELETE' OR NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'a published menu version cannot be changed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$$;

CREATE FUNCTION require_frozen_menu_version() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM velora.menu_versions v
                    WHERE v.tenant_id = NEW.tenant_id AND v.id = NEW.menu_version_id
                      AND v.frozen_at IS NOT NULL) THEN
        RAISE EXCEPTION 'only a frozen menu version can be published'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION forbid_frozen_menu_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION forbid_frozen_version_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION require_frozen_menu_version() FROM PUBLIC;

CREATE TRIGGER menu_versions_frozen BEFORE UPDATE OR DELETE ON menu_versions
    FOR EACH ROW EXECUTE FUNCTION forbid_frozen_version_change();
CREATE TRIGGER menu_categories_frozen BEFORE INSERT OR UPDATE OR DELETE ON menu_categories
    FOR EACH ROW EXECUTE FUNCTION forbid_frozen_menu_change();
CREATE TRIGGER menu_items_frozen BEFORE INSERT OR UPDATE OR DELETE ON menu_items
    FOR EACH ROW EXECUTE FUNCTION forbid_frozen_menu_change();
CREATE TRIGGER modifier_groups_frozen BEFORE INSERT OR UPDATE OR DELETE ON modifier_groups
    FOR EACH ROW EXECUTE FUNCTION forbid_frozen_menu_change();
CREATE TRIGGER modifier_options_frozen BEFORE INSERT OR UPDATE OR DELETE ON modifier_options
    FOR EACH ROW EXECUTE FUNCTION forbid_frozen_menu_change();

CREATE TRIGGER menu_publications_frozen_only BEFORE INSERT ON menu_publications
    FOR EACH ROW EXECUTE FUNCTION require_frozen_menu_version();
CREATE TRIGGER menu_publications_immutable BEFORE UPDATE OR DELETE ON menu_publications
    FOR EACH ROW EXECUTE FUNCTION forbid_record_change();
CREATE TRIGGER menu_publications_no_truncate BEFORE TRUNCATE ON menu_publications
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_record_change();

-- RLS and runtime grants ------------------------------------------------------------------

SELECT enable_tenant_rls(t)
  FROM unnest(ARRAY[
      'menu_versions', 'menu_categories', 'menu_items', 'modifier_groups', 'modifier_options',
      'menu_publications', 'menu_item_availability', 'ordering_settings', 'order_capacity_slots'
  ]::regclass[]) AS t;

GRANT SELECT, INSERT, UPDATE (revision, updated_at, frozen_at) ON menu_versions TO app_rw;
-- A draft is saved as a whole (delete and re-insert), never updated row by row.
GRANT SELECT, INSERT, DELETE ON menu_categories, menu_items, modifier_groups, modifier_options
    TO app_rw;
GRANT SELECT, INSERT ON menu_publications TO app_rw;
GRANT SELECT, INSERT, UPDATE (is_available, updated_by_staff_id, updated_at)
    ON menu_item_availability TO app_rw;
GRANT SELECT, INSERT, UPDATE ON ordering_settings TO app_rw;
GRANT SELECT, INSERT, UPDATE (orders_count) ON order_capacity_slots TO app_rw;

-- Platform role: the founder may fill a draft (CSV import) but never freeze or publish it.
-- Reading the publication history lets an import report its changes against the live menu.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['menu_versions', 'menu_categories', 'menu_items',
                             'modifier_groups', 'modifier_options', 'menu_publications']
    LOOP
        EXECUTE pg_catalog.format(
            'CREATE POLICY platform_scope ON velora.%I AS PERMISSIVE FOR ALL '
            'TO velora_platform USING (tenant_id = velora.current_tenant_id()) '
            'WITH CHECK (tenant_id = velora.current_tenant_id())', t);
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE (revision, updated_at) ON menu_versions TO velora_platform;
GRANT SELECT, INSERT, DELETE ON menu_categories, menu_items, modifier_groups, modifier_options
    TO velora_platform;
GRANT SELECT ON menu_publications TO velora_platform;
