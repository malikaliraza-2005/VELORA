-- P9 (provider-neutral track, AD-25): the staff dashboard (AD-02, AD-33).
--
-- * idempotency_keys: every state-changing dashboard request carries an Idempotency-Key
--   (AD-20). The first request with a key runs; a repeat of it (a double tap, a retry after a
--   lost response) gets the first one's status back and changes nothing. Only the status is
--   kept, never a response body, so no customer data or login link is copied here. Rows are
--   per session and are dropped after 24 hours.

ALTER TABLE staff_sessions ADD CONSTRAINT staff_sessions_tenant_id_key UNIQUE (tenant_id, id);

CREATE TABLE idempotency_keys (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    session_id      uuid NOT NULL,
    key             text NOT NULL CHECK (key ~ '^[A-Za-z0-9_-]{16,100}$'),
    -- SHA-256 of the method, path, query and body: a key reused for another request is refused.
    fingerprint     bytea NOT NULL CHECK (length(fingerprint) = 32),
    state           text NOT NULL CHECK (state IN ('in_progress', 'done')),
    response_status smallint CHECK (response_status BETWEEN 200 AND 299),
    created_at      timestamptz NOT NULL,
    completed_at    timestamptz,
    CHECK ((state = 'done') = (response_status IS NOT NULL AND completed_at IS NOT NULL)),
    UNIQUE (tenant_id, session_id, key),
    FOREIGN KEY (tenant_id, session_id) REFERENCES staff_sessions (tenant_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (tenant_id, created_at);

SELECT enable_tenant_rls('idempotency_keys');
GRANT SELECT, INSERT, UPDATE (state, response_status, created_at, completed_at), DELETE
    ON idempotency_keys TO app_rw;
