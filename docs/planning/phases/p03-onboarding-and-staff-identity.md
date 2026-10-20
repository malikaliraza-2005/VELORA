# P3: Business onboarding and staff identity

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** Needs the owner's explicit approval. |
| **Depends on** | P2 |
| **Blocks** | P4, P8, P9 |
| **Effort (estimate)** | about 1 week |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-02: staff web dashboard | APPROVED |
| AD-12: human owner verification; manual recovery; staff added by owner invite | ADOPTED (delegated) |
| AD-18: separate ordering number plus written consent | ADOPTED (delegated) |
| AD-23: staff authentication | ADOPTED (delegated) |
| Magic-link delivery channel | **Unresolved** (blocker B7) |

## Objective and scope
- **Objective:** create businesses safely and give staff authenticated, scoped access.
- **Scope:**
  - **Admin (founder) tools** to create a tenant, record owner verification (AD-12) and consent (AD-18), and register the channel (later linked in P5).
  - **Owner invite flow.** The owner invites staff with roles: owner, manager, kitchen, kitchen-device.
  - **Login:** magic link → device session.
    - 12-hour idle expiry, 30-day absolute expiry.
    - Logout; revocation when a staff member is removed or a role changes; "log out all devices".
  - **Payment PIN:** a personal staff PIN, hashed, required for confirming payments.
  - **Audit:** every privileged action is logged.

## Phase security and reliability requirements
- Magic links are single-use, expire after 15 minutes or less, and are bound to the requesting identity.
- Session tokens are random, stored hashed, and kept in HttpOnly, Secure, SameSite cookies.
- CSRF protection.
- Login and PIN attempts are rate-limited.
- Each session is scoped to one tenant.

## Tasks

### P3-T1: Admin (founder) onboarding tools
- **Prerequisites:** P2.
- **Deliverables:** minimal admin onboarding screens to:
  - create a tenant;
  - record owner verification (AD-12) and consent (AD-18);
  - register the channel (later linked in P5).
- **Security:** admin actions are audited. Consent and verification records can't be edited after they're saved; a correction is a new record.
- **Tests:** a tenant can't be marked ready for go-live without both a verification record and a consent record.
- **Acceptance:** the test passes, and the audit entries exist.

### P3-T2: Magic-link login
- **Prerequisites:** T1. **Delivery channel decided (B7).** Fallback: the founder delivers the link manually during the pilot.
- **Deliverables:** issuing magic links, verifying them and creating a device session.
- **Security:** **magic links are single-use, expire after 15 minutes or less, and are bound to the requesting identity.** Login attempts are rate-limited.
- **Tests:** **a reused or expired link fails.** Login requests above the rate limit are refused.
- **Acceptance:** both tests pass.

### P3-T3: Device sessions
- **Prerequisites:** T2.
- **Deliverables:**
  - 12-hour idle expiry and 30-day absolute expiry;
  - logout;
  - revocation when a staff member is removed or a role changes;
  - "log out all devices".
- **Security:**
  - Session tokens are random, stored hashed, and kept in HttpOnly, Secure, SameSite cookies.
  - CSRF protection.
  - Each session is scoped to one tenant.
- **Tests:**
  - **a revoked session gets a 401 on its next request;**
  - idle and absolute expiry are enforced (simulated clock);
  - **cross-tenant dashboard access is denied.**
- **Acceptance:** all tests pass.

### P3-T4: Owner invite flow and roles
- **Prerequisites:** T3.
- **Deliverables:** the owner invites staff with roles: owner, manager, kitchen, kitchen-device.
- **Tests:** **a kitchen-device session can't change the menu or settings.**
- **Acceptance:** the role-permission matrix test passes.

### P3-T5: Payment PIN
- **Prerequisites:** T3.
- **Deliverables:** a personal staff PIN, hashed, required for confirming payments (used in P8).
- **Security:** PIN attempts are rate-limited.
- **Tests:** **confirming a payment without a valid PIN is rejected and logged.**
- **Acceptance:** the test passes.

### P3-T6: Audit logging
- **Prerequisites:** P2 (`audit_log`).
- **Deliverables:** every privileged action is logged (actor, tenant, action, time).
- **Tests:** each privileged endpoint writes an audit row.
- **Acceptance:** the coverage test passes.

## Phase deliverables
- **Deliverables:** auth service, invite flow, session management, audit logging, admin onboarding screens (minimal).

## Phase acceptance criteria (exit)
- A reused or expired link fails.
- A revoked session gets a 401 on its next request.
- A kitchen-device session can't change the menu or settings.
- Confirming a payment without a valid PIN is rejected and logged.
- Cross-tenant dashboard access is denied.

## Risks and rollback
- **Risks and rollback:** delivering the magic link depends on a channel. Fallback: the founder delivers the link manually during the pilot.

## Open items
- B7: magic-link delivery channel (email, or manual delivery by the founder; WhatsApp would depend on the provider).
