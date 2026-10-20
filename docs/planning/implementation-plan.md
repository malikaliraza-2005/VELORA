# VELORA implementation plan (master roadmap)

- **Last updated:** 2026-10-21 (restructured into per-phase files; no requirements changed)
- **Status:**
  - **P0 is authorised** (2026-10-20). Desk work continues; the **live OpenWA validation is paused**.
  - **P1 is authorised** (2026-10-21).
  - **P2–P13 are not authorised.**
  - Under **AD-25** (owner, 2026-10-21), the provider-neutral parts of P5 and P6–P9 may be built against the `FakeProvider` before the OpenWA Go, each once it is authorised. The OpenWA adapter, real WhatsApp traffic and P10–P13 stay gated by the Go.
- **Governing decisions:** [`../architecture/architecture-decisions.md`](../architecture/architecture-decisions.md)
- **Scope:** [`mvp-scope.md`](mvp-scope.md)
- **Progress tracking:** [`implementation-status.md`](implementation-status.md). This is the only place where phase and task status is recorded.
- **Detailed tasks:** one file per phase in [`phases/`](phases/). Each task there has prerequisites, deliverables, security requirements, tests, acceptance criteria and rollback.

> **Effort figures** are rough estimates for one experienced developer. They are not commitments.
> **Phase order** follows dependencies. P0 must give a **Go** before any work that depends on OpenWA (P5 onwards) starts.

## Non-negotiable safeguards (apply to every phase)

1. **Order confirmation.** An order is never confirmed until availability, pricing and the database transaction have all been validated successfully.
2. **Payment verification.** A payment is never marked verified from AI output, customer text or a screenshot alone. Only an authenticated staff action with a recorded reference counts (AD-03).
3. **Duplicates and retries.**
   - Duplicate orders are prevented with an idempotency key on every side effect.
   - Retries must be safe to repeat.
   - Order state never depends on whether a message was delivered.
4. **Tenant isolation.** Each business's data and conversations are isolated at the application layer **and** by FORCE RLS (AD-17).
5. **OpenWA.** OpenWA is **unvalidated** until gates G1–G5 pass. The Meta Cloud API alternative stays documented and possible through the provider interface.
6. **Secrets and credentials.**
   - No real merchant credentials go into the repository, fixtures or tickets.
   - No secrets appear in logs.
   - Test data uses test numbers only.
7. **Fallback and recovery.** Every failure mode has a manual fallback and a recovery runbook (P11).

**Decision statuses** (APPROVED, ADOPTED (delegated), VALIDATION-DEPENDENT, PENDING EXTERNAL, PROPOSED) are defined in [`../README.md`](../README.md#status-legend-used-in-all-engineering-documents). Proposals PC-1 to PC-13 are **not applied** anywhere in this plan until the owner approves them ([decisions §9](../architecture/architecture-decisions.md#9-proposed-changes-from-p0-desk-research-2026-10-21-for-owner-review)).

---

## Phase roadmap

| Phase | Objective | Authorisation | Depends on | Effort (estimate) |
|---|---|---|---|---|
| [P0](phases/p00-pre-implementation-gates.md) Pre-implementation gates | Produce the evidence the architecture needs before building on OpenWA | **Authorised.** Desk work in progress; live validation **paused** | Test SIMs; volunteer phones; a VPS for the trial; lawyer and accountant engaged | 2–3 calendar weeks. Mostly elapsed time. |
| [P1](phases/p01-repository-foundation.md) Repository foundation | A clean, secure, testable codebase skeleton | **Authorised (2026-10-21)** | None (can run in parallel with P0) | 3–5 days |
| [P2](phases/p02-database-and-tenant-isolation.md) Database and tenant isolation | One canonical schema with enforced isolation between businesses | Not authorised | P1 | 1–1.5 weeks |
| [P3](phases/p03-onboarding-and-staff-identity.md) Onboarding and staff identity | Create businesses safely; authenticated, scoped staff access | Not authorised | P2 | about 1 week |
| [P4](phases/p04-menu-availability-and-settings.md) Menu, availability and settings | A correct catalogue and day-to-day controls | Not authorised | P2, P3 | about 1 week |
| [P5](phases/p05-whatsapp-provider-and-openwa-adapter.md) WhatsApp provider and OpenWA adapter | Reliable messaging that doesn't depend on any one provider | Not authorised. Provider-neutral tasks may proceed before the Go (AD-25); the OpenWA adapter tasks are **blocked** | **P0 Go (V-01)** for the OpenWA tasks, P2; V-02 and V-04 decided (plus V-05 to V-08 for their tasks) | 1.5–2 weeks |
| [P6](phases/p06-conversation-workflows.md) Conversation workflows | Customers build a correct cart in WhatsApp safely | Not authorised. May be built against the FakeProvider before the Go (AD-25); real-WhatsApp exit tests stay gated | P4, P5; AI model choice; V-09 | about 2 weeks |
| [P7](phases/p07-order-lifecycle.md) Order lifecycle | Orders are created exactly once, after validation, and move only through permitted states | Not authorised. May be built against the FakeProvider before the Go (AD-25); real-WhatsApp exit tests stay gated | P6 | 1–1.5 weeks |
| [P8](phases/p08-payment-verification.md) Payment verification | Payment status that can be trusted, with no automated "paid" status | Not authorised. May be built against the FakeProvider before the Go (AD-25); real-WhatsApp exit tests stay gated | P7, P3 | about 1 week |
| [P9](phases/p09-staff-dashboard.md) Staff dashboard (PWA) | Staff run orders quickly and safely | Not authorised. May be built against the FakeProvider before the Go (AD-25); real-WhatsApp exit tests stay gated | P3–P8 | about 2 weeks |
| [P10](phases/p10-monitoring-and-alerting.md) Monitoring and alerting | Problems reach the founder before restaurants notice them | Not authorised; blocked | P5–P9 | 4–5 days |
| [P11](phases/p11-backups-recovery-and-runbooks.md) Backups, recovery and runbooks | Recover safely from data loss and outages | Not authorised; blocked | P2, P5 (recovery mode), P7; V-07 | 4–5 days |
| [P12](phases/p12-deployment-and-staging.md) Deployment and staging | A hardened, repeatable deployment with staging | Not authorised; blocked | P10, P11, V-03 (VPS size) | about 1 week |
| [P13](phases/p13-pilot-onboarding.md) Pilot onboarding | A controlled real-world pilot | Not authorised; blocked; needs separate owner approval to start | P12; legal brief; consent form; tax confirmation; owner-verification runbook; owner approval | 4–8 calendar weeks |

## Security gates and exit criteria (summary)

The phase files are authoritative. Each lists the full criteria and the tasks that satisfy them.

| Phase | Security gate | Exit criteria |
|---|---|---|
| P0 | Test numbers only; OpenWA dashboard and API on a private network; volunteer numbers masked; session files never in evidence; no detection-evasion features | Every test in the validation plan has an evidence row; a Go/No-Go recommendation is presented to the owner; the owner has decided V-01 to V-10 |
| P1 | No real credentials committed; `.gitignore` covers env files, session data and dumps; lockfiles committed | CI passes on an empty skeleton; the secret scanner blocks a test commit containing a fake key |
| P2 | Runtime never uses the owner role or `service_role`; SECURITY DEFINER only for the resolver (with `SET search_path`, EXECUTE revoked from PUBLIC); no tenant ID from the request body | CI enumeration test; cross-tenant tests as `app_rw` affect 0 rows; fail-closed test; restore-compatibility test |
| P3 | Single-use magic links (≤15 min, bound to identity); hashed random session tokens in HttpOnly/Secure/SameSite cookies; CSRF; rate-limited login and PIN; one tenant per session | Reused or expired link fails; revoked session gets 401; kitchen-device can't change menu or settings; payment without valid PIN rejected and logged; cross-tenant dashboard access denied |
| P4 | Price changes audited; imports validated; a draft menu never goes live by accident | Invalid CSV rejected with line-level errors; unavailable item never offered; pause blocks new carts within 5 s; capacity cap enforced and reset per slot |
| P5 | OpenWA on a private network only; secrets never in logs (redaction tested); session credentials on the encrypted volume (V-07); **no detection-evasion features** | Contract tests on recorded payloads; duplicate webhook creates no second event; crash test processes exactly once; bad signature rejected (4xx) and never stored; outbox holds then flushes in order; circuit breaker within 60 s of `needs_qr`; static provider-boundary check |
| P6 | Injection can at most produce a proposal; AI data minimised; paid no-training AI tier | Unavailable item never added; prices equal database values; AI output never sent verbatim as a total; injection suite changes nothing; in-order concurrency; AI accuracy ≥90% offline (**estimate, to be confirmed**; not a CI gate) |
| P7 | Order state never depends on message delivery; every transition audited | Double YES creates exactly one order (100 runs); stale confirmation rejected and re-confirmed; illegal transitions rejected; expired nonce can't create an order; pre-outage pending actions expired in recovery |
| P8 | No code path sets "paid" except an authenticated staff action (writer-search test); every confirmation audited; funds never pass through VELORA | Screenshot alone never changes status; "I have paid" text never changes status; no-PIN or cross-tenant confirmation rejected; duplicate reference flagged; expired transfer becomes `expired`, not paid |
| P9 | Idempotency key on every action; CSP; no browser secrets beyond the session cookie; role-scoped views | Staging E2E with matching WhatsApp updates; double-tap creates no duplicate; kitchen-device can't open settings; QR re-link restores channel and clears banner; usability check during P13 soft launch |
| P10 | Secret redaction in logs; alerts not sent via a possibly-down WhatsApp channel | Each alert fires in drills (kill ingress, disconnect session, fill DLQ, fail backup); log redaction test finds no secrets |
| P11 | Hourly encrypted dumps including roles, grants and policies; OpenWA session state not backed up off-site unless V-07 allows | Restore drill: RTO ≤4 h, RPO ≤1 h, roles/FORCE RLS/policies present, cross-tenant tests pass, test order succeeds; recovery-mode drill after a 1-hour outage: no duplicates, no stale YES accepted, every outbox message resolved |
| P12 | SSH keys only; firewall 443 (+ SSH from known addresses); auto security updates; OpenWA on an internal network only; TLS; restricted secrets | From-scratch deploy ≤1 h following the document; rollback demonstrated; port scan shows only expected ports; all failure drills pass with alerts firing |
| P13 | Preconditions met; separate numbers with consent; stop rules (AD-22) | Every onboarding has a verification record, a consent record and a test order; zero wrong prices or false "paid"; every incident has a runbook entry; metrics recorded throughout |

## Dependency summary

```
P0 ─────────────────────────────┐ (Go + V-decisions)
P1 → P2 → P3 → P4 ─┐            ▼
           └───────┴──→ P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12 → P13
                                                         ▲ legal, tax, consent
```

**Rough total after P0:** about 12–16 developer-weeks, plus 4–8 weeks of pilot. **This is an estimate.**

## Known gaps to resolve during planning or the build

- **Dashboard magic-link delivery:** the platform needs a channel for sending staff login links. Options are email or the founder handing links over manually during the pilot. Using WhatsApp would depend on the provider. *(P3; blocker B7)*
- **Pilot city:** Karachi or Lahore is not yet chosen. *(P13)*
- **AI accuracy target:** the ≥90% figure is an estimate that the owner should confirm. *(P6)*
- **Order confirmation by text:** the `pending_actions` YES flow assumes OpenWA delivers plain-text replies reliably and quickly. This is covered by trial tests T4.1–T4.4. *(P0, P7)*
