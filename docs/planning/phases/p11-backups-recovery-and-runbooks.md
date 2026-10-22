# P11: Backups, recovery and runbooks

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** **BLOCKED** until its WhatsApp-dependent prerequisites (P5, P7) are complete. |
| **Depends on** | P2, P5 (recovery mode), P7. V-07 for session backups. |
| **Blocks** | P12 |
| **Effort (estimate)** | 4–5 days |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-04: managed PostgreSQL with backups | APPROVED, **amended by AD-35**: PostgreSQL on the free VM; our own backups |
| AD-21: backups and restore; RPO ≤1h, RTO ≤4h targets | ADOPTED (delegated) |
| AD-22: stop rules | ADOPTED (delegated) |
| V-04: inbound recovery | VALIDATION-DEPENDENT |
| V-07: session backup policy | VALIDATION-DEPENDENT |
| PC-8: don't back up the OpenWA volume off-site | PROPOSED, awaiting review |
| PC-13: add #560 and #830 as pilot risks | PROPOSED, awaiting review |

## Objective and scope
- **Objective:** recover safely from data loss and outages.
- **Scope:**
  - **Backups:** **hourly encrypted logical dumps** to free off-site storage, including roles, grants and policies. There is no provider backup under AD-35, so these dumps and the restore drills are the only recovery.
  - **Session backups:** OpenWA session state **is not backed up off-site** unless V-07 allows it.
  - **Restore drill.**
  - **Recovery mode on startup:**
    1. database health check;
    2. reclaim expired leases;
    3. reconcile inbound (per V-04);
    4. expire pending YES nonces created before the outage, and ask the customer to reconfirm;
    5. extend transfer windows by the outage length;
    6. mark in-flight outbox messages `unknown`;
    7. replace stale replies;
    8. rate-limited flush;
    9. orders created during recovery are highlighted on the dashboard.
  - **Runbooks (each with detection, immediate action, manual fallback, recovery and communication steps):**
    - OpenWA session down / needs QR
    - OpenWA engine broken for all tenants (global pause; version pin; upgrade on a test number first)
    - Number restricted or banned (stop sending; stop rule AD-22; owner decision on moving to Meta)
    - Database restore
    - VPS loss (rebuild from infrastructure-as-code; restore database; merchants re-scan QR)
    - Payment dispute or staff mistake
    - **Manual fallback:**
      - restaurants take orders by phone or on paper;
      - the founder broadcasts nothing automatically;
      - orders are re-entered later only if the owner wants.

## Tasks

### P11-T1: Database backups
- **Prerequisites:** P2; database vendor and off-site storage chosen ([price sheet](../../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing)).
- **Deliverables:** provider daily backups plus **hourly encrypted logical dumps** off-site, including roles, grants and policies.
- **Security:** dumps are encrypted before upload; keys are kept apart from the backup storage; no plaintext dumps on disk after upload.
- **Tests:** the backup-success metric is reported (P10); a dump file is confirmed encrypted.
- **Acceptance:** hourly dumps run for 24 hours in staging with zero failures.

### P11-T2: OpenWA session backup policy
- **Prerequisites:** **V-07** (PC-8 proposed).
- **Deliverables:** OpenWA session state **is not backed up off-site** unless V-07 allows it. The policy is documented in the runbooks.
- **Acceptance:** the backup job excludes the session volume (verified by listing the backup contents).

### P11-T3: Restore script and restore drill
- **Prerequisites:** T1; the P2 restore-compatibility test.
- **Deliverables:** a restore script; a restore drill.
- **Tests and acceptance:**
  - restore to a fresh instance within 4 hours or less (RTO target);
  - data loss of 1 hour or less (RPO target);
  - roles, FORCE RLS and policies present;
  - the cross-tenant tests pass after restore;
  - a test order succeeds.
- **Recovery:** if the drill fails, the pilot doesn't start (P13 precondition through P12).

### P11-T4: Recovery mode on startup
- **Prerequisites:** P5 (leases, outbox, reconciliation per **V-04**), P7 (nonces, timers).
- **Deliverables:** the nine recovery steps listed in Scope, in that order.
- **Tests:** each step is unit-tested.
- **Acceptance:** see T5.

### P11-T5: Recovery-mode drill
- **Prerequisites:** T4; staging.
- **Tests and acceptance:** **recovery-mode drill** after a simulated 1-hour outage: no duplicate orders, no stale YES accepted, every outbox message resolved.

### P11-T6: Runbooks and manual fallback
- **Prerequisites:** P5, P8, P12 topology.
- **Deliverables:** the runbooks listed in Scope, each with detection, immediate action, manual fallback, recovery and communication steps, plus the manual fallback procedure. (PC-13 proposes adding the #560 passkey linking block. Awaiting review.)
- **Tests:** each runbook is walked through once in staging (tabletop or drill).
- **Acceptance:** every runbook has a walk-through record.

## Phase deliverables
- **Deliverables:** backup jobs, restore script, recovery-mode code, runbook documents.

## Phase acceptance criteria (exit)
- **Restore drill:**
  - restore to a fresh instance within 4 hours or less (RTO target);
  - data loss of 1 hour or less (RPO target);
  - roles, FORCE RLS and policies present;
  - the cross-tenant tests pass after restore;
  - a test order succeeds.
- **Recovery-mode drill** after a simulated 1-hour outage: no duplicate orders, no stale YES accepted, every outbox message resolved.

## Risks and rollback
- **Risk:** a restore that drops roles or policies. Mitigated by the P2 restore-compatibility test and the restore drill. *(This risk wasn't listed in the original plan; it's added here as a note.)*
- If the OpenWA volume is lost, every session must be re-linked (QR) and OpenWA's API keys reissued ([validation plan §10.3](../openwa-validation-plan.md)).
