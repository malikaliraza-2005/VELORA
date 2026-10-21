REVOKE ALL ON menu_versions, menu_categories, menu_items, modifier_groups, modifier_options,
    menu_publications FROM velora_platform;
DROP POLICY platform_scope ON menu_publications;
DROP POLICY platform_scope ON menu_versions;
DROP POLICY platform_scope ON menu_categories;
DROP POLICY platform_scope ON menu_items;
DROP POLICY platform_scope ON modifier_groups;
DROP POLICY platform_scope ON modifier_options;

DROP TABLE order_capacity_slots;
DROP TABLE ordering_settings;
DROP TABLE menu_item_availability;
DROP TABLE menu_publications;
DROP TABLE modifier_options;
DROP TABLE modifier_groups;
DROP TABLE menu_items;
DROP TABLE menu_categories;
DROP TABLE menu_versions;

DROP FUNCTION require_frozen_menu_version();
DROP FUNCTION forbid_frozen_version_change();
DROP FUNCTION forbid_frozen_menu_change();
