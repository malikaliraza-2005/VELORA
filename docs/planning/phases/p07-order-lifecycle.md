# P7: Order lifecycle

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** Once authorised and once P6 is complete (as built so far), it may be built and tested against the `FakeProvider` before the OpenWA Go ([AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits)). Tests that need real WhatsApp traffic stay gated by the Go. |
| **Depends on** | P6 |
| **Blocks** | P8, P9, P11 |
| **Effort (estimate)** | 1–1.5 weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-03: payment states (COD, staff-verified transfer) | APPROVED |
| AD-16: outbox | ADOPTED (delegated) |
| AD-20: order safety (YES nonce, guarded transitions, duplicate controls, independence from delivery) | ADOPTED (delegated) |
| AD-21: recovery mode (expire pre-outage nonces) | ADOPTED (delegated) |

## Objective and scope
- **Objective:** orders are created exactly once, after validation, and move only through permitted states.
- **Scope:**
  - **Confirmation:** a `pending_actions` nonce (single-use, expiring, bound to tenant, customer and cart hash). The customer's **YES** consumes it.
  - **A single transaction** that:
    1. re-validates availability, pause and capacity;
    2. re-prices;
    3. consumes the nonce;
    4. creates the order with price snapshots;
    5. writes the outbox message.

    If any step fails, nothing is committed and the customer gets a clear message.
  - **States:** `draft`, `awaiting_confirmation`, `cod_confirmed`, `awaiting_transfer`, `paid_staff_verified`, `accepted`, `rejected`, `preparing`, `out_for_delivery`/`ready`, `completed`, `cancelled`, `expired`, `refund_pending`.
  - **Transition table** listing the allowed actor for each transition. Every transition is guarded (`UPDATE … WHERE status = expected`).
  - **Cancellation:** the customer can cancel only before the order is accepted.
  - **Timers:** transfer-window expiry; nonce expiry.
  - **Duplicate flag:** same customer and cart hash within 10 minutes is flagged for staff, never cancelled automatically.

## Phase security and reliability requirements
- **Security and reliability:** order state never depends on whether a message was delivered. Every transition is audited.

## Tasks

### P7-T1: Confirmation nonce
- **Prerequisites:** P6 (templated summary).
- **Deliverables:** a `pending_actions` nonce (single-use, expiring, bound to tenant, customer and cart hash), consumed by the customer's **YES**.
- **Tests:** **an expired nonce can't create an order**; a nonce bound to another customer or cart hash is rejected.
- **Acceptance:** the tests pass.

### P7-T2: Order-creation transaction
- **Prerequisites:** T1; P4 (availability, pause, capacity, pricing).
- **Deliverables:** a single transaction that re-validates availability, pause and capacity; re-prices; consumes the nonce; creates the order with price snapshots; and writes the outbox message. If any step fails, nothing is committed and the customer gets a clear message.
- **Tests:**
  - **two YES replies at the same moment create exactly one order (concurrency test, 100 runs);**
  - **a price change or item becoming unavailable between the summary and YES causes rejection and re-confirmation;**
  - an injected failure at each step commits nothing.
- **Acceptance:** all tests pass. 100 of 100 concurrency runs create exactly one order.

### P7-T3: States and transition table
- **Prerequisites:** T2.
- **Deliverables:**
  - the states listed in Scope;
  - a transition table listing the allowed actor for each transition;
  - every transition guarded (`UPDATE … WHERE status = expected`) and audited;
  - the customer can cancel only before the order is accepted.
- **Tests:** **illegal transitions are rejected**; a customer cancel after acceptance is refused; every transition writes an audit row.
- **Acceptance:** the transition-table tests pass.

### P7-T4: Timers
- **Prerequisites:** T3.
- **Deliverables:** transfer-window expiry; nonce expiry.
- **Tests:** expiry moves orders to `expired` and nonces become unusable (simulated clock).
- **Acceptance:** the tests pass.

### P7-T5: Duplicate flag
- **Prerequisites:** T2.
- **Deliverables:** same customer and cart hash within 10 minutes is flagged for staff, never cancelled automatically.
- **Tests:** a second identical order within 10 minutes is flagged and **not** cancelled.
- **Acceptance:** the test passes.

### P7-T6: Customer status notifications
- **Prerequisites:** T3; P5 outbox.
- **Deliverables:** customer status notifications through the outbox.
- **Security:** **order state never depends on whether a message was delivered.**
- **Tests:** with sends failing, transitions still succeed and the order state is correct.
- **Acceptance:** the test passes.

### P7-T7: Recovery hook
- **Prerequisites:** T1; P11 recovery mode.
- **Deliverables:** expiry of pending actions created before an outage.
- **Tests:** **pending actions created before an outage are expired during recovery mode.**
- **Acceptance:** the test passes (also exercised in the P11 drill).

## Phase deliverables
- **Deliverables:** order service, transition engine, timers, customer status notifications through the outbox.

## Phase acceptance criteria (exit)
- **Double YES:** two YES replies at the same moment create **exactly one** order (concurrency test, 100 runs).
- **Stale confirmation:** a price change or item becoming unavailable between the summary and YES causes rejection and re-confirmation.
- **Illegal transitions** are rejected.
- **Expiry:** an expired nonce can't create an order.
- **Recovery:** pending actions created before an outage are expired during recovery mode.

## Risks and rollback
- **Risks and rollback:** state-machine bugs. Mitigated by transition-table tests; rollback by redeploying the previous version, since data stays valid.
