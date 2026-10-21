# P4: Menu, availability and settings

| Field | Value |
|---|---|
| **Authorisation** | **AUTHORISED** by the owner, 2026-10-21 ("Start P4"). **Complete** (PR #5; implementation choices in AD-28). |
| **Depends on** | P2, P3 |
| **Blocks** | P6 |
| **Effort (estimate)** | about 1 week |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-01: MVP scope (delivery and takeaway, one branch) | APPROVED |
| AD-03: COD and staff-verified transfers (COD limit, transfer window) | APPROVED |
| AD-09: own database; inventory is an internal module; Stock Flow excluded | APPROVED |
| AD-20: order safety (server pricing) | ADOPTED (delegated) |
| AD-28: P4 implementation choices (versions, founder import, permissions, pricing rules) | ADOPTED (delegated) |
| Tax values by payment method | **PENDING EXTERNAL** (accountant, blocker B3) |
| COD limit and transfer window per restaurant | PENDING EXTERNAL (set with each merchant) |

## Objective and scope
- **Objective:** a correct catalogue and the controls owners use day to day.
- **Scope:**
  - **Catalogue:** relational `menu_categories`, `menu_items`, `modifier_groups` and `modifier_options`, all carrying prices.
  - **Import:** CSV import and form entry by the founder, then owner review and **publish**. Only a published version is visible to customers.
  - **Availability:** `is_available` toggles.
  - **Pause:** pause orders for N minutes.
  - **Capacity:** a maximum number of orders per 15-minute slot.
  - **Rules:** delivery fee, minimum order, pickup-time rules.
  - **Tax:** `tax_config` per tenant, varying by payment method. Values come from the accountant (PENDING EXTERNAL).
  - **COD and transfers:** COD first-order limit and transfer window per tenant.

## Phase security and reliability requirements
- Price changes are audited.
- Imports are validated: no negative prices, required fields present, no duplicate SKUs within a tenant.
- A draft menu never goes live by accident.

## Tasks

### P4-T1: Catalogue schema and APIs
- **Prerequisites:** P2, P3.
- **Deliverables:** relational `menu_categories`, `menu_items`, `modifier_groups` and `modifier_options`, all carrying prices. Tenant-scoped under P2 RLS.
- **Security:** **price changes are audited.**
- **Tests:** the P2 enumeration and cross-tenant tests are extended to the new tables; a price change writes an audit row.
- **Acceptance:** the tests pass.
- **Rollback:** forward-only migration once data exists.

### P4-T2: Import and validation
- **Prerequisites:** T1.
- **Deliverables:** CSV import and form entry by the founder; an import validator.
- **Security:** **imports are validated:** no negative prices, required fields present, no duplicate SKUs within a tenant.
- **Tests:** **an invalid CSV is rejected with line-level errors.**
- **Acceptance:** the test passes for each validation rule.

### P4-T3: Draft, review and publish (with version history)
- **Prerequisites:** T2.
- **Deliverables:** owner review and **publish**. Only a published version is visible to customers. Version history.
- **Security:** **a draft menu never goes live by accident.**
- **Tests:** draft items are invisible to customer-facing queries; re-publishing the previous version restores it.
- **Acceptance:** both tests pass.
- **Rollback:** re-publish the previous version.

### P4-T4: Availability toggles
- **Prerequisites:** T1.
- **Deliverables:** `is_available` toggles.
- **Tests:** **an unavailable item is never offered** (also tested in P6).
- **Acceptance:** the test passes.

### P4-T5: Pause and capacity
- **Prerequisites:** T1.
- **Deliverables:** pause orders for N minutes; a maximum number of orders per 15-minute slot.
- **Tests:**
  - **pausing blocks new carts within 5 seconds;**
  - **the capacity cap rejects orders above the limit and lets them through again in the next slot.**
- **Acceptance:** both tests pass.

### P4-T6: Settings
- **Prerequisites:** T1.
- **Deliverables:**
  - delivery fee, minimum order, pickup-time rules;
  - `tax_config` per tenant, varying by payment method;
  - COD first-order limit and transfer window per tenant;
  - settings APIs.
- **Security:** settings changes are audited, and only owner or manager roles can change them (P3).
- **Tests:** each setting is applied by the pricing used in P6 and P7 (integration test with placeholder tax values).
- **Acceptance:** the tests pass. **Real tax values aren't entered until the accountant confirms them (B3).**

## Phase deliverables
- **Deliverables:** catalogue schema and APIs, import validator, settings APIs.

## Phase acceptance criteria (exit)
- An invalid CSV is rejected with line-level errors.
- An unavailable item is never offered (also tested in P6).
- Pausing blocks new carts within 5 seconds.
- The capacity cap rejects orders above the limit and lets them through again in the next slot.

## Risks and rollback
- **Risks and rollback:** a bad import. Mitigated by draft/publish with version history; rollback means re-publishing the previous version.

## Open items
- B3: tax values by payment method. Until a rate is set, pricing refuses to quote that payment method.
- **Deviation:** "form entry by the founder" is the founder's `menu-import` command plus the owner's form endpoint (AD-28). There are no admin web screens in the pilot (AD-27).
