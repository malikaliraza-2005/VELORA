# P13: Pilot onboarding

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** The pilot needs its own explicit owner approval to start, even after P12 is done. |
| **Depends on** | P12, plus the preconditions below |
| **Blocks** | End-of-pilot Go / Pivot / Stop review |
| **Effort (estimate)** | 4–8 calendar weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-01: narrow pilot (3–5 restaurants, one city, one branch, delivery and takeaway) | APPROVED |
| AD-05: OpenWA only after the Go; not claimed compliant | APPROVED |
| AD-07: free or discounted pilot | APPROVED |
| AD-08: legal brief before any real merchant is connected | **WITHDRAWN** by the owner (AD-35) |
| AD-12: human owner verification | ADOPTED (delegated) |
| AD-18: separate number plus written consent | ADOPTED (delegated) |
| AD-22: stop rules | ADOPTED (delegated) |
| Pilot city (Karachi or Lahore) | **Unresolved** |
| COD limit and transfer window per restaurant | PENDING EXTERNAL |
| PC-13: #560 and #830 as pilot risks | PROPOSED, awaiting review |

## Objective and scope
- **Objective:** a controlled real-world pilot.
- **Preconditions:**
  - P12 complete.
  - Consent form finalised (drafted by engineering; not legal advice, AD-35). **Drafted:** [`../../operations/consent-form.md`](../../operations/consent-form.md) (AD-38); the owner finalises it.
  - Tax values set by the owner from public sources (AD-35).
  - Owner-verification runbook ready. **Written:** [`../../operations/owner-verification.md`](../../operations/owner-verification.md) (AD-38).
  - The owner approves starting the pilot.
- **Scope:**
  1. **One restaurant, supervised soft launch.** The founder watches the dashboard live for the first few days.
  2. Then expand to 3–5 restaurants.
  3. **Daily metrics:**
     - orders;
     - orders completed without a human;
     - handoffs;
     - disconnects;
     - payment verifications;
     - incidents.
  4. **Stop rules (AD-22).**
  5. **End-of-pilot Go / Pivot / Stop review** with the owner.

## Tasks

### P13-T1: Preconditions check
- **Prerequisites:** none beyond the list.
- **Deliverables:** a signed-off checklist:
  - P12 complete;
  - consent form finalised (engineering draft, AD-35);
  - tax values set by the owner (AD-35);
  - owner-verification runbook ready;
  - the owner approves starting the pilot;
  - pilot city chosen.
- **Acceptance:** every item has evidence. **No real merchant is connected before this.**

### P13-T2: Per-restaurant onboarding
- **Prerequisites:** T1.
- **Deliverables, per restaurant:**
  - a verification record (AD-12);
  - a consent record (AD-18, wording from the engineering draft, AD-35);
  - a separate ordering number linked;
  - menu entered, reviewed and published (P4);
  - COD limit and transfer window set;
  - a test order.
- **Security:** a merchant's main number is never linked.
- **Acceptance:** **every onboarding has a verification record, a consent record and a test order.**
- **Rollback:** unlink the session (logout) and confirm the device is removed from the phone (AD-24).

### P13-T3: Supervised soft launch (one restaurant)
- **Prerequisites:** T2 for the first restaurant.
- **Deliverables:** the founder watches the dashboard live for the first few days. A usability check with at least one real kitchen staff member (from P9).
- **Acceptance:** the usability check is recorded; no stop rule triggered.

### P13-T4: Expand to 3–5 restaurants
- **Prerequisites:** T3 completed without a stop-rule trigger; owner agrees to expand.
- **Acceptance:** each additional restaurant meets the T2 acceptance.

### P13-T5: Daily metrics
- **Prerequisites:** P10 daily pilot metrics.
- **Deliverables:** orders; orders completed without a human; handoffs; disconnects; payment verifications; incidents.
- **Acceptance:** **metrics are recorded throughout.**

### P13-T6: Stop rules and incidents
- **Deliverables:** apply AD-22: pause on any restriction, any wrong price or false "paid", or disconnects over the V-10 threshold. Every incident is handled with a runbook.
- **Acceptance:**
  - **zero wrong prices or false "paid" statuses;**
  - **every incident has a runbook entry.**
- **Rollback:** **a restriction or a serious incident pauses onboarding (AD-22). Restaurants fall back to manual operation.**

### P13-T7: End-of-pilot review
- **Deliverables:** a Go / Pivot / Stop review with the owner, using the metrics and incidents.
- **Acceptance:** the owner's decision is recorded.

## Phase acceptance criteria (exit)
- Every onboarding has a verification record, a consent record and a test order.
- Zero wrong prices or false "paid" statuses.
- Every incident has a runbook entry.
- Metrics are recorded throughout.

## Risks and rollback
- **Risks and rollback:** a restriction or a serious incident pauses onboarding (AD-22). Restaurants fall back to manual operation.

## Open items
- Pilot city.
- PC-13 risks (#560 passkey linking block, #830 first-message delivery).
- Merchant acceptance of a separate number (assumption S2, blocker B4).
