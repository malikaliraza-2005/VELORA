-- P2-T2: canonical core schema. Replaces the conflicting schemas in research docs 07, 08, 09
-- and 17. Menu and settings tables arrive in P4.
--
-- Conventions:
--   * Every tenant row carries tenant_id. Child rows reference parents through composite
--     (tenant_id, id) foreign keys, so a row can never point at another tenant's row.
--   * Money is integer paisa (bigint). Floating-point types are never used.
--   * Foreign keys are ON DELETE RESTRICT: financial history cannot be deleted from under
--     an order, and customers are soft-deleted and anonymised instead.
--   * RLS, grants and policies are applied in 0003.

-- Tenants and channels -------------------------------------------------------------------

CREATE TABLE tenants (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    display_name        text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    status              text NOT NULL DEFAULT 'onboarding'
                        CHECK (status IN ('onboarding', 'active', 'paused', 'closed')),
    order_number_prefix text NOT NULL CHECK (order_number_prefix ~ '^[A-Z]{2,4}$'),
    -- Per-tenant order sequence; allocated by next_order_number().
    next_order_number   integer NOT NULL DEFAULT 1 CHECK (next_order_number > 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE channels (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    provider    text NOT NULL CHECK (provider IN ('fake', 'openwa', 'meta_cloud')),
    -- Provider session reference; the bootstrap resolver maps it to the tenant.
    session_ref text NOT NULL CHECK (length(session_ref) BETWEEN 1 AND 200),
    state       text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'connecting', 'connected', 'disconnected',
                                 'paused', 'banned', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, session_ref),
    UNIQUE (tenant_id, id)
);

-- Staff -----------------------------------------------------------------------------------

CREATE TABLE staff_members (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    role         text NOT NULL CHECK (role IN ('owner', 'manager', 'staff', 'kitchen_device')),
    -- Contact for magic links; the delivery channel is decided in P3 (blocker B7).
    email        text,
    phone_e164   text CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
    pin_hash     text,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);

CREATE TABLE staff_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL,
    staff_member_id     uuid NOT NULL,
    token_hash          bytea NOT NULL UNIQUE,
    device_label        text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    absolute_expires_at timestamptz NOT NULL,
    revoked_at          timestamptz,
    revoked_reason      text,
    CHECK (absolute_expires_at > created_at),
    FOREIGN KEY (tenant_id, staff_member_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

-- Onboarding records ----------------------------------------------------------------------

CREATE TABLE consents (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    kind                 text NOT NULL
                         CHECK (kind IN ('separate_number_ban_risk', 'data_processing', 'terms')),
    document_version     text NOT NULL,
    document_sha256      text NOT NULL CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    accepted_by_name     text NOT NULL,
    accepted_by_staff_id uuid,
    accepted_at          timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, accepted_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE owner_verifications (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    method       text NOT NULL
                 CHECK (method IN ('cnic', 'ntn', 'business_document', 'in_person', 'other')),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'verified', 'rejected')),
    -- A pointer to where the evidence is held; never the document itself.
    evidence_ref text,
    reviewed_by  text,
    decided_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'pending') = (decided_at IS NULL))
);

-- Customers and conversations -------------------------------------------------------------

CREATE TABLE customers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    phone_e164    text CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    anonymised_at timestamptz,
    CHECK (anonymised_at IS NOT NULL OR phone_e164 IS NOT NULL),
    CHECK (anonymised_at IS NULL OR (phone_e164 IS NULL AND display_name IS NULL
                                     AND deleted_at IS NOT NULL)),
    UNIQUE (tenant_id, phone_e164),
    UNIQUE (tenant_id, id)
);

CREATE TABLE conversations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    channel_id       uuid NOT NULL,
    customer_id      uuid NOT NULL,
    state            text NOT NULL DEFAULT 'idle',
    state_version    integer NOT NULL DEFAULT 1,
    context          jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_inbound_at  timestamptz,
    last_outbound_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, channel_id, customer_id),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, channel_id) REFERENCES channels (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id) ON DELETE RESTRICT
);

-- Messaging: durable inbox and outbox (AD-16) ---------------------------------------------

CREATE TABLE inbox_events (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL,
    channel_id        uuid NOT NULL,
    provider_event_id text NOT NULL,
    event_type        text NOT NULL,
    payload           jsonb NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'processing', 'done', 'failed', 'ignored')),
    attempts          integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at      timestamptz NOT NULL DEFAULT now(),
    locked_until      timestamptz,
    processed_at      timestamptz,
    last_error        text,
    received_at       timestamptz NOT NULL DEFAULT now(),
    -- Deduplicates provider redeliveries.
    UNIQUE (tenant_id, channel_id, provider_event_id),
    FOREIGN KEY (tenant_id, channel_id) REFERENCES channels (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX inbox_events_claim_idx ON inbox_events (available_at) WHERE status = 'pending';

CREATE TABLE outbox_messages (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL,
    channel_id          uuid NOT NULL,
    conversation_id     uuid,
    idempotency_key     text NOT NULL,
    kind                text NOT NULL,
    payload             jsonb NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'stale',
                                          'cancelled')),
    attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at     timestamptz NOT NULL DEFAULT now(),
    -- Past this time the message is no longer worth sending.
    stale_after         timestamptz,
    locked_until        timestamptz,
    sent_at             timestamptz,
    provider_message_id text,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, channel_id) REFERENCES channels (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES conversations (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX outbox_messages_claim_idx ON outbox_messages (next_attempt_at)
    WHERE status = 'pending';

-- Confirmations (AD-20): the customer's YES maps to a single-use, expiring nonce -----------

CREATE TABLE pending_actions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    conversation_id uuid NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('confirm_order')),
    nonce_hash      bytea NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    consumed_at     timestamptz,
    CHECK (expires_at > created_at),
    UNIQUE (tenant_id, nonce_hash),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES conversations (tenant_id, id) ON DELETE RESTRICT
);

-- Orders and payments ---------------------------------------------------------------------

CREATE TABLE orders (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL,
    order_number       integer NOT NULL CHECK (order_number > 0),
    customer_id        uuid NOT NULL,
    conversation_id    uuid,
    -- The confirmation nonce that created this order; one order per nonce.
    pending_action_id  uuid,
    status             text NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'awaiting_confirmation', 'cod_confirmed',
                                         'awaiting_transfer', 'paid_staff_verified', 'accepted',
                                         'rejected', 'preparing', 'out_for_delivery', 'ready',
                                         'completed', 'cancelled', 'expired', 'refund_pending')),
    fulfilment         text NOT NULL CHECK (fulfilment IN ('delivery', 'takeaway')),
    payment_method     text CHECK (payment_method IN ('cod', 'transfer')),
    subtotal_paisa     bigint NOT NULL DEFAULT 0 CHECK (subtotal_paisa >= 0),
    delivery_fee_paisa bigint NOT NULL DEFAULT 0 CHECK (delivery_fee_paisa >= 0),
    tax_paisa          bigint NOT NULL DEFAULT 0 CHECK (tax_paisa >= 0),
    total_paisa        bigint NOT NULL DEFAULT 0
                       CHECK (total_paisa = subtotal_paisa + delivery_fee_paisa + tax_paisa),
    delivery_address   text,
    delivery_note      text,
    pickup_at          timestamptz,
    possible_duplicate boolean NOT NULL DEFAULT false,
    version            integer NOT NULL DEFAULT 1,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, order_number),
    UNIQUE (tenant_id, pending_action_id),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, conversation_id)
        REFERENCES conversations (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, pending_action_id)
        REFERENCES pending_actions (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX orders_status_idx ON orders (tenant_id, status, created_at);
CREATE INDEX orders_customer_idx ON orders (tenant_id, customer_id);

CREATE TABLE order_items (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    order_id         uuid NOT NULL,
    line_no          smallint NOT NULL CHECK (line_no > 0),
    -- Snapshots of the menu item at the time of ordering (AD-20).
    sku              text NOT NULL,
    name_snapshot    text NOT NULL,
    unit_price_paisa bigint NOT NULL CHECK (unit_price_paisa >= 0),
    quantity         integer NOT NULL CHECK (quantity > 0),
    line_total_paisa bigint NOT NULL CHECK (line_total_paisa = unit_price_paisa * quantity),
    notes            text,
    UNIQUE (tenant_id, order_id, line_no),
    FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE order_transitions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL,
    order_id       uuid NOT NULL,
    from_status    text,
    to_status      text NOT NULL,
    actor_kind     text NOT NULL CHECK (actor_kind IN ('customer', 'staff', 'system')),
    actor_staff_id uuid,
    reason         text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (actor_kind <> 'staff' OR actor_staff_id IS NOT NULL),
    FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, actor_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX order_transitions_order_idx ON order_transitions (tenant_id, order_id);

CREATE TABLE payments (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL,
    order_id             uuid NOT NULL,
    method               text NOT NULL CHECK (method IN ('cod', 'transfer')),
    amount_paisa         bigint NOT NULL CHECK (amount_paisa > 0),
    status               text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'customer_claimed', 'verified', 'rejected',
                                           'refund_pending', 'refunded')),
    reference            text,
    verified_by_staff_id uuid,
    verified_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- AD-03: only an identified staff member can verify a payment, and a transfer needs a
    -- recorded reference.
    CHECK (status <> 'verified' OR (verified_by_staff_id IS NOT NULL AND verified_at IS NOT NULL
                                    AND (method <> 'transfer' OR reference IS NOT NULL))),
    FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, verified_by_staff_id)
        REFERENCES staff_members (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX payments_order_idx ON payments (tenant_id, order_id);

-- Audit and history (append-only for the runtime role) ------------------------------------

CREATE TABLE audit_log (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    actor_kind  text NOT NULL CHECK (actor_kind IN ('customer', 'staff', 'system', 'platform')),
    actor_id    uuid,
    action      text NOT NULL,
    target_type text,
    target_id   uuid,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_created_idx ON audit_log (tenant_id, created_at);

CREATE TABLE channel_events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    channel_id uuid NOT NULL,
    event_type text NOT NULL,
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, channel_id) REFERENCES channels (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX channel_events_channel_idx ON channel_events (tenant_id, channel_id, created_at);

-- Functions -------------------------------------------------------------------------------

-- Allocates the next order number for the current tenant. Row-locks the tenant, so numbers
-- are gap-free per committed transaction order and never shared between tenants. Returns
-- NULL without a tenant context, which then fails the NOT NULL on orders.order_number.
CREATE FUNCTION next_order_number() RETURNS integer
    LANGUAGE sql VOLATILE
AS $$
    UPDATE velora.tenants
       SET next_order_number = next_order_number + 1
     WHERE id = velora.current_tenant_id()
    RETURNING next_order_number - 1
$$;

-- Soft-deletes and anonymises a customer of the current tenant. Orders are kept (financial
-- history) but lose their address fields. Returns false if the customer is not visible.
CREATE FUNCTION anonymise_customer(p_customer_id uuid) RETURNS boolean
    LANGUAGE plpgsql VOLATILE
    SET search_path = velora
AS $$
BEGIN
    UPDATE customers
       SET phone_e164 = NULL,
           display_name = NULL,
           deleted_at = coalesce(deleted_at, now()),
           anonymised_at = now(),
           updated_at = now()
     WHERE id = p_customer_id AND anonymised_at IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    UPDATE orders
       SET delivery_address = NULL, delivery_note = NULL, updated_at = now()
     WHERE customer_id = p_customer_id;
    RETURN true;
END
$$;
