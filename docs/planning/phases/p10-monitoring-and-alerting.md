# P10: Monitoring and alerting

| Field | Value |
|---|---|
| **Authorisation** | **Authorised 2026-10-22** ("Start P10"), read as the provider-neutral part before the Go (AD-34, to be confirmed). Real alert delivery waits for the free mailbox and heartbeat (AD-35); the staging run for P12. |
| **Depends on** | P5–P9 |
| **Blocks** | P12 |
| **Effort (estimate)** | 4–5 days |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-19: one VPS with alerts | ADOPTED (delegated) |
| AD-22: pilot stop rules (fed by metrics) | ADOPTED (delegated) |
| AD-24: no secrets in logs | ADOPTED (delegated) |
| PC-11: paid uptime-monitoring tier | **REJECTED** (AD-35: zero cost; free tiers) |
| AD-34: P10 implementation choices | ADOPTED (delegated) |
| Monitoring vendor and cost | See the [price sheet](../../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing) (not committed) |

## Objective and scope
- **Objective:** problems reach the founder before restaurants notice them.
- **Scope:**
  - **Logs:** structured JSON with tenant ID and correlation ID, and **secret redaction**.
  - **Metrics:**
    - ingress errors and HMAC failures;
    - inbox and outbox queue age;
    - dead-letter queue depth;
    - channel state changes and time spent in `needs_qr`;
    - send failures;
    - backup success;
    - order funnel.
  - **Alerts:** to the founder by phone or email, not via a WhatsApp channel that may itself be down.
  - **External uptime check.**
  - **Daily pilot metrics.**

## Tasks

### P10-T1: Structured logging with redaction
- **Prerequisites:** P5–P9 code paths.
- **Deliverables:** structured JSON logs with tenant ID and correlation ID, and **secret redaction**.
- **Tests:** **the log redaction test finds no secrets** (seeded fake secrets in requests, webhooks and errors).
- **Acceptance:** the test runs in CI and passes.

### P10-T2: Metrics
- **Prerequisites:** T1.
- **Deliverables:** every metric listed in Scope.
- **Tests:** each metric changes when its condition is simulated.
- **Acceptance:** all listed metrics are visible on the founder dashboard.

### P10-T3: Alerts and external uptime check
- **Prerequisites:** T2; free alert services (AD-35: zero cost; PC-11 rejected).
- **Deliverables:** alert rules sent to the founder by phone or email, not via a WhatsApp channel that may itself be down; an external uptime check.
- **Tests:** **each alert fires in a drill: kill the ingress, disconnect a session, fill the DLQ, fail a backup.**
- **Acceptance:** all four drills produce an alert to the founder.

### P10-T4: Founder dashboard and daily pilot metrics
- **Prerequisites:** T2.
- **Deliverables:** a dashboard for the founder; daily pilot metrics (the inputs P13 needs).
- **Acceptance:** the metrics are produced daily in staging.

## Phase deliverables
- **Deliverables:** logging config, metrics, alert rules, a dashboard for the founder.

## Phase acceptance criteria (exit)
- Each alert fires in a drill: kill the ingress, disconnect a session, fill the DLQ, fail a backup.
- The log redaction test finds no secrets.

## Risks and rollback
- **Risk:** alert noise or missed alerts. *(This risk wasn't listed in the original plan; it's added here as a note.)* Mitigation: drills before the pilot. Rollback: revert the alert-rule configuration.
