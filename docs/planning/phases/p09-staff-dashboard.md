# P9: Staff dashboard (PWA)

| Field | Value |
|---|---|
| **Authorisation** | **Authorised by the owner ("Start P9", 2026-10-22)** for the provider-neutral part, built and tested against the `FakeProvider` before the OpenWA Go ([AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits)). Tests that need real WhatsApp traffic stay gated by the Go. Implementation choices: [AD-33](../../architecture/architecture-decisions.md#ad-33-p9-implementation-choices-staff-dashboard). |
| **Depends on** | P3–P8 |
| **Blocks** | P10 |
| **Effort (estimate)** | about 2 weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-02: staff web dashboard and human-takeover inbox | APPROVED |
| AD-03: staff-verified transfers | APPROVED |
| AD-23: staff authentication; kitchen-device role; PIN | ADOPTED (delegated) |
| AD-20: idempotency on dashboard actions | ADOPTED (delegated) |
| Optional customer order-tracking link | **NOT APPROVED.** Not in this phase. |

## Objective and scope
- **Objective:** staff can run orders quickly and safely.
- **Scope:**
  - **Order board:** New / Preparing / Ready-Out / Done, with an audible alert for new orders.
  - **Payment received:** reference + PIN.
  - **Chats:** handoff inbox, replies sent through the outbox, Resume bot.
  - **Menu availability.**
  - **Controls:** pause and capacity.
  - **Today summary.**
  - **WhatsApp:** status banner and QR re-link (owner only).
  - **Staff management.**
  - **Live updates:** server-sent events or polling.
  - **Offline-aware UI:** it shows "connection lost" and never queues actions silently.

## Phase security and reliability requirements
- Every action carries an idempotency key.
- Content Security Policy.
- No secrets stored in the browser beyond the session cookie.
- Views are scoped by role.

## Tasks

### P9-T1: PWA shell, authentication and role-scoped views
- **Prerequisites:** P3.
- **Deliverables:** installable PWA; login integration; role-based views.
- **Security:**
  - Content Security Policy;
  - no secrets stored in the browser beyond the session cookie;
  - views scoped by role.
- **Tests:** **the kitchen-device role can't open settings**; browser storage contains no tokens (automated check).
- **Acceptance:** the tests pass.

### P9-T2: Order board
- **Prerequisites:** P7.
- **Deliverables:** New / Preparing / Ready-Out / Done, with an audible alert for new orders. Every action carries an idempotency key.
- **Tests:** **a double-tap creates no duplicate transition.**
- **Acceptance:** the test passes.

### P9-T3: Payment received
- **Prerequisites:** P8.
- **Deliverables:** a "Payment received" action requiring the reference and PIN.
- **Tests:** the P8 tests are exercised through the UI (missing PIN or reference rejected).
- **Acceptance:** the tests pass.

### P9-T4: Chats and handoff inbox
- **Prerequisites:** P6 (handoff), P5 (outbox).
- **Deliverables:** a handoff inbox; replies sent through the outbox; a Resume bot button.
- **Tests:** a staff reply reaches the test customer through the outbox; Resume restores the bot.
- **Acceptance:** the tests pass in staging.

### P9-T5: Menu, controls, today summary, staff management
- **Prerequisites:** P3, P4.
- **Deliverables:** menu availability; pause and capacity; today summary; staff management.
- **Tests:** the dashboard uses the same endpoints and permissions as P3 and P4.
- **Acceptance:** the tests pass.

### P9-T6: WhatsApp status and QR re-link (owner only)
- **Prerequisites:** P5 channel state machine.
- **Deliverables:** a status banner and QR re-link, visible to the owner only.
- **Security:** the QR is shown only to an authenticated owner session and is never logged.
- **Tests:** **QR re-link restores the channel and clears the banner.**
- **Acceptance:** the test passes in staging with a test number.

### P9-T7: Live updates and offline awareness
- **Prerequisites:** T2.
- **Deliverables:** server-sent events or polling. An offline-aware UI that shows "connection lost" and never queues actions silently.
- **Tests:** with the network cut, actions are refused visibly and nothing is queued.
- **Acceptance:** the test passes.

### P9-T8: End-to-end and usability
- **Prerequisites:** T1–T7; staging (P12).
- **Tests:**
  - **end-to-end in staging:** order → accept → preparing → done, with matching WhatsApp updates to the test customer;
  - **usability:** checked with at least one real kitchen staff member during the P13 soft launch.
- **Acceptance:** the E2E test passes. The usability check is recorded in P13.

## Phase deliverables
- **Deliverables:** PWA (installable), role-based views.

## Phase acceptance criteria (exit)
- **End-to-end in staging:** order → accept → preparing → done, with matching WhatsApp updates to the test customer.
- **Double-tap** creates no duplicate transition.
- **Kitchen-device role** can't open settings.
- **QR re-link** restores the channel and clears the banner.
- **Usability:** checked with at least one real kitchen staff member during P13 soft launch.

## Risks and rollback
- **Risks and rollback:** the UI is decoupled from the backend, so rolling back means redeploying the previous web build.
