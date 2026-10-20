# P12: Deployment and staging

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** **BLOCKED** until P10, P11 and V-03 are complete. |
| **Depends on** | P10, P11, V-03 (VPS size) |
| **Blocks** | P13 |
| **Effort (estimate)** | about 1 week |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-04: paid managed database; always-on server | APPROVED |
| AD-19: one VPS for the pilot | ADOPTED (delegated) |
| AD-24: OpenWA on a private network only; no secrets in logs | ADOPTED (delegated) |
| V-03: VPS size and cost | VALIDATION-DEPENDENT |
| PC-4: OpenWA hardening baseline | PROPOSED, awaiting review |
| PC-9: same-region VPS and database | PROPOSED, awaiting review |
| Infrastructure cost commitment | **Owner approval required** after re-checking prices ([price sheet](../../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing)) |

## Objective and scope
- **Objective:** a hardened, repeatable deployment with a staging environment.
- **Scope:**
  - **VPS hardening:** SSH keys only; firewall allowing 443 (and SSH from known addresses); automatic security updates.
  - **Containers:** docker compose with separate containers for ingress, workers, OpenWA and the web app; **OpenWA on an internal network only**.
  - **Edge and secrets:** TLS; a secret store or env files with restricted permissions.
  - **Staging:** test numbers only.
  - **Releases:** a release checklist, rollback (previous image tags), and pinned OpenWA versions.
  - **Failure drills in staging:**
    - kill OpenWA;
    - force a disconnect;
    - take the API down;
    - restore the database.

## Tasks

### P12-T1: Provision and harden the VPS
- **Prerequisites:** **V-03**; the **owner approves the purchase** after re-checking prices.
- **Deliverables:** SSH keys only; firewall allowing 443 (and SSH from known addresses); automatic security updates.
- **Tests:** **an external port scan shows only the expected ports.**
- **Acceptance:** the scan matches the expected port list.

### P12-T2: Container topology
- **Prerequisites:** T1.
- **Deliverables:** docker compose with separate containers for ingress, workers, OpenWA and the web app. **OpenWA on an internal network only.**
- **Security:** the OpenWA port isn't reachable from outside (verified by the T1 scan); OpenWA's dashboard is not exposed (AD-24).
- **Acceptance:** the scan and an internal connectivity check pass.

### P12-T3: TLS and secrets
- **Prerequisites:** T2.
- **Deliverables:** TLS; a secret store or env files with restricted permissions.
- **Tests:** a TLS check; a file-permission check on the secrets; no secrets in container logs (P10 redaction test).
- **Acceptance:** all checks pass.

### P12-T4: Staging environment
- **Prerequisites:** T2, T3.
- **Deliverables:** staging, using test numbers only.
- **Acceptance:** staging runs the full stack with test numbers, and no merchant data exists in staging.

### P12-T5: Release and rollback procedure
- **Prerequisites:** T4.
- **Deliverables:** a release checklist; rollback via previous image tags; pinned OpenWA versions.
- **Tests:** **rollback is demonstrated.**
- **Acceptance:** a release and a rollback are each completed once in staging.

### P12-T6: From-scratch deployment
- **Prerequisites:** T1–T5.
- **Tests:** **a full deployment from scratch takes 1 hour or less, following the document.**
- **Acceptance:** a timed run is recorded.

### P12-T7: Failure drills
- **Prerequisites:** P10 alerts, P11 runbooks.
- **Deliverables:** in staging: kill OpenWA, force a disconnect, take the API down, restore the database.
- **Tests:** **all failure drills pass with the alerts firing.**
- **Acceptance:** each drill has a record with the alert received.

## Phase deliverables
- **Deliverables:** infrastructure-as-code or scripts, staging environment, release and rollback procedure.

## Phase acceptance criteria (exit)
- A full deployment from scratch takes 1 hour or less, following the document.
- Rollback is demonstrated.
- An external port scan shows only the expected ports.
- All failure drills pass with the alerts firing.

## Risks and rollback
- **Rollback:** roll back to the previous image tags; pinned OpenWA versions.
- **Risk:** single-VPS failure (AD-19). Mitigated by the P11 runbooks and alerts.
