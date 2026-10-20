# P8: Payment verification

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** Once authorised and once P7 is complete (as built so far), it may be built and tested against the `FakeProvider` before the OpenWA Go ([AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits)). Tests that need real WhatsApp traffic stay gated by the Go. |
| **Depends on** | P7, P3 |
| **Blocks** | P9 |
| **Effort (estimate)** | about 1 week |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-03: COD plus staff-verified transfers; **AI output, customer text or a screenshot never marks an order as paid**; VELORA never holds funds | APPROVED |
| AD-23: staff PIN for payment confirmation | ADOPTED (delegated) |
| COD limits and transfer windows per restaurant | PENDING EXTERNAL |
| Online payment provider | **Post-MVP.** Only after sandbox tests (decisions §4) |

## Objective and scope
- **Objective:** payment status that can be trusted, with no automated "paid" status.
- **Scope:**
  - **Payments ledger:** `payments` is append-only (method, amount, status, reference, verified_by, verified_at).
  - **COD:** a first-order limit and a blocklist per tenant.
  - **Transfers:**
    - the customer gets the restaurant's account details (from tenant settings);
    - a staff member confirms on the dashboard with the **transaction reference and their PIN**, which sets `paid_staff_verified`;
    - a screenshot only attaches to the order for staff to look at.
  - **Late or uncertain payments:**
    - expiry when the transfer window passes;
    - a late payment after expiry goes to staff review: accept the order, or mark it `refund_pending` and refund manually through the restaurant's own account;
    - the reference must be unique per tenant (a duplicate is flagged).

## Phase security and reliability requirements
- **No code path sets a paid status except an authenticated staff action** (enforced by a test that searches all writers).
- Every confirmation is audited.
- Funds never pass through VELORA.

## Tasks

### P8-T1: Payments ledger
- **Prerequisites:** P2, P7.
- **Deliverables:** `payments` is append-only (method, amount, status, reference, verified_by, verified_at).
- **Tests:** UPDATE and DELETE on ledger rows are refused for `app_rw`; corrections are new rows.
- **Acceptance:** the tests pass.

### P8-T2: COD controls
- **Prerequisites:** P4 settings.
- **Deliverables:** a first-order limit and a blocklist per tenant.
- **Tests:** a first order over the limit is refused with a clear message; a blocklisted customer can't choose COD.
- **Acceptance:** the tests pass.

### P8-T3: Transfer instructions and screenshot handling
- **Prerequisites:** P4 settings; P7.
- **Deliverables:** the customer gets the restaurant's account details (from tenant settings); a screenshot only attaches to the order for staff to look at.
- **Tests:**
  - **a screenshot upload alone never changes payment status;**
  - **text such as "I have paid" never changes payment status.**
- **Acceptance:** the tests pass.

### P8-T4: Staff verification
- **Prerequisites:** P3 (PIN, roles); T1.
- **Deliverables:** a staff member confirms on the dashboard with the **transaction reference and their PIN**, which sets `paid_staff_verified`. The reference must be unique per tenant.
- **Security:** every confirmation is audited.
- **Tests:**
  - **a confirmation without a PIN, or from another tenant, is rejected;**
  - **a duplicate reference is flagged.**
- **Acceptance:** the tests pass.

### P8-T5: Expiry and late payments
- **Prerequisites:** P7 timers.
- **Deliverables:**
  - expiry when the transfer window passes;
  - a late payment after expiry goes to staff review: accept the order, or mark it `refund_pending` and refund manually through the restaurant's own account.
- **Security:** funds never pass through VELORA.
- **Tests:** **an expired transfer moves to `expired`, not paid**; the late-payment review offers only "accept" or "refund_pending".
- **Acceptance:** the tests pass.

### P8-T6: Owner reconciliation report and reversal
- **Prerequisites:** T4.
- **Deliverables:** a reconciliation report (daily list of staff-verified transfers for the owner); an owner-only reversal action that is itself audited.
- **Tests:** the report lists every staff-verified transfer for the day; a non-owner can't reverse.
- **Acceptance:** the tests pass.

### P8-T7: Paid-writer guard
- **Prerequisites:** T4.
- **Deliverables:** **no code path sets a paid status except an authenticated staff action** (enforced by a test that searches all writers).
- **Acceptance:** the guard test runs in CI and passes.

## Phase deliverables
- **Deliverables:** payment service, dashboard endpoints, reconciliation report (daily list of staff-verified transfers for the owner).

## Phase acceptance criteria (exit)
- Screenshot upload alone never changes payment status.
- Text such as "I have paid" never changes payment status.
- A confirmation without a PIN, or from another tenant, is rejected.
- A duplicate reference is flagged.
- An expired transfer moves to `expired`, not paid.

## Risks and rollback
- **Risks and rollback:** staff mistakes. Mitigated by the reference, PIN and audit trail, the owner's daily reconciliation report, and an owner-only reversal action that is itself audited.
