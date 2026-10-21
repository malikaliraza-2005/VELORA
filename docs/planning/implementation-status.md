# VELORA implementation status

- **Last updated:** 2026-10-21
- **Plan:** [`implementation-plan.md`](implementation-plan.md) (master roadmap); detailed tasks per phase in [`phases/`](phases/)
- **Decisions:** [`../architecture/architecture-decisions.md`](../architecture/architecture-decisions.md)

> **Evidence rule:** a phase or check counts as done only when a real artifact backs it, such as a test report, an evidence-log row, a CI run link, a drill record, or a signed document. Never mark anything done on assumption. **No live test results exist yet.**

## Overall status

| Area | Status |
|---|---|
| **P0 desk research** | **In progress.** Free, non-code work only (owner choice, 2026-10-21). |
| **P0 live OpenWA validation** | **Paused** until a test SIM and the required setup exist (see "Resuming the live test"). |
| **P1** | **Authorised 2026-10-21. In progress.** |
| **P2–P13** | **Not started. Not authorised.** |
| **Provider-neutral track (AD-25)** | Owner decision 2026-10-21: the provider-neutral parts of P5, and P6–P9, may be built against the `FakeProvider` before the OpenWA Go, each once it is authorised. |
| **OpenWA adapter, real WhatsApp traffic, P10–P13** | **Blocked** until the live validation is complete and the owner gives a Go (V-01). |
| **Application code** | P1 repository skeleton only (no product features). |
| **Purchases / subscriptions** | None. |

**What the owner can provide right now:**
- their own phone (as the test customer);
- one prepaid Pakistani SIM, to be obtained later.

**Not available yet:**
- a second phone;
- volunteers;
- an always-on computer or server;
- budget for a lawyer or accountant.

A reduced live-trial variant that fits these constraints is **proposed, not approved**. See [`openwa-validation-plan.md`](openwa-validation-plan.md) §9.

## Phase checklist

| Phase | Status | Blockers | Evidence | Next action |
|---|---|---|---|---|
| [P0 Pre-implementation gates](phases/p00-pre-implementation-gates.md) | **Desk research in progress; live test paused** | Test SIM; machine to run OpenWA; volunteers (reduced variant proposed); lawyer/accountant budget | Desk review notes (see P0 tasks) | Finish desk tasks; owner decides on the reduced trial variant |
| [P1 Repository foundation](phases/p01-repository-foundation.md) | **In progress**: implemented and committed locally; **exit criteria not yet verified** | CI can only run after a push; local secret-scan hook blocked by Windows Application Control | See "P1 task evidence" below | Owner: push when ready, then confirm CI results |
| [P2 Database and tenant isolation](phases/p02-database-and-tenant-isolation.md) | Not started; not authorised | P1 | — | — |
| [P3 Onboarding and staff identity](phases/p03-onboarding-and-staff-identity.md) | Not started; not authorised | P2; delivery channel for magic links (gap) | — | — |
| [P4 Menu, availability and settings](phases/p04-menu-availability-and-settings.md) | Not started; not authorised | P2, P3; tax values (accountant) | — | — |
| [P5 Provider abstraction and OpenWA adapter](phases/p05-whatsapp-provider-and-openwa-adapter.md) | Not started — **blocked** | **Live validation + Go (V-01)**; V-02, V-04, V-05, V-06, V-07, V-08 | — | — |
| [P6 Conversation workflows](phases/p06-conversation-workflows.md) | Not started — blocked (depends on P5) | P4, P5; AI model choice; V-09 | — | — |
| [P7 Order lifecycle](phases/p07-order-lifecycle.md) | Not started — blocked (depends on P6) | P6 | — | — |
| [P8 Payment verification](phases/p08-payment-verification.md) | Not started — blocked (depends on P7) | P7, P3 | — | — |
| [P9 Staff dashboard](phases/p09-staff-dashboard.md) | Not started — blocked (depends on P5–P8) | P3–P8 | — | — |
| [P10 Monitoring](phases/p10-monitoring-and-alerting.md) | Not started — blocked | P5–P9 | — | — |
| [P11 Backups and recovery](phases/p11-backups-recovery-and-runbooks.md) | Not started — blocked | P2, P5, P7; V-07 | — | — |
| [P12 Deployment and staging](phases/p12-deployment-and-staging.md) | Not started — blocked | P10, P11; V-03 | — | — |
| [P13 Pilot onboarding](phases/p13-pilot-onboarding.md) | Not started — blocked | P12; legal brief; consent form; tax confirmation; owner approval | — | — |

## P0 task breakdown

A task only counts as done when its evidence exists.

### Founder (paused unless noted)

| # | Task | Status | Evidence when done |
|---|---|---|---|
| P0-F1 | Get **one** prepaid Pakistani test SIM for the "restaurant" number (the full plan's 3 SIMs are reduced in the proposed variant). **Never use merchants' numbers or your personal WhatsApp number.** | Paused | SIM noted privately; number masked in shared docs |
| P0-F2 | Test customers: the owner's own phone, plus volunteers if available (reduced variant) | Paused | Consent list (private) |
| P0-F3 | Machine to run OpenWA: a Windows computer for short sessions, and/or a small VPS for the 14-day soak | Paused | Noted in the price sheet |
| P0-F4 | Lawyer and accountant quotes, using the prepared brief questions | Deferred (budget). **Required before any real merchant is connected** | Quotes; engagement letter |
| P0-F5 | Merchant interviews, using the prepared question list | Paused; optional while waiting | Interview notes and a summary |
| P0-F6 | Real, anonymised order messages (target ≥200) | Paused. Synthetic samples prepared meanwhile (provisional) | Dataset with no phone numbers |
| P0-F7 | *Insurance:* Meta Tech Provider application; Coexistence test | Deferred | Application reference |

### Engineering (Claude)

| # | Task | Status | Evidence |
|---|---|---|---|
| P0-E1 | Trial tooling (webhook recorder, OpenWA compose, resource sampler, evidence-log template). This is code, so it needs the owner's go-ahead | **Not started; not authorised** | — |
| P0-E2 | OpenWA source and documentation desk review | **Done (desk only, 2026-10-21).** Commit `a3fc0ae`, v0.25.0. **No gate passed.** | [`openwa-validation-plan.md`](openwa-validation-plan.md) §10 |
| P0-E3 | Run the live schedule with the founder | **Paused** | — |
| P0-E4 | Vendor price sheet | **Done (2026-10-21).** Prices must be rechecked before buying | [`../architecture/architecture-decisions.md`](../architecture/architecture-decisions.md) §6 |
| P0-E5 | Evaluate AI models on real messages | Paused (needs F6). Synthetic samples and a candidate list are ready | [`mvp-scope.md`](mvp-scope.md) A15; decisions PC-10 |
| P0-E6 | Gate review (G1–G5 verdicts, V-01–V-10 recommendations) | Paused (needs E3) | — |
| P0-E7 | Customer message samples, merchant interview questions, beginner setup guide | **Drafts done (2026-10-21).** Not tested with real users | [`mvp-scope.md`](mvp-scope.md) Appendices A–B; [`openwa-validation-plan.md`](openwa-validation-plan.md) Appendix |
| P0-E8 | Proposed changes arising from the desk research | **Awaiting owner review** | Decisions §9 (PC-1 to PC-13) |

## P1 task evidence (2026-10-21)

| Task | Status | Evidence |
|---|---|---|
| P1-T1 Repository and layout | Done locally | `git init` with `origin` set to GitHub (**not pushed**). `backend/`, `web/`, `infra/`, `docs/`, README. `.gitignore` checks in `backend/tests/test_repo_hygiene.py` pass locally |
| P1-T2 Toolchain | Done locally | Python 3.12, uv lockfile (`backend/uv.lock`) committed. Locally: `ruff format --check` clean, `ruff check` clean, `mypy --strict` clean, `pytest` 17 passed |
| P1-T3 Configuration | Done locally | `.env.example` with placeholders only. The placeholder test passes |
| P1-T4 Secret scanning | **CI scan verified; fake-key detection pending** | `.pre-commit-config.yaml` (gitleaks v8.30.1). The local hook is blocked on the founder's machine by Windows Application Control. CI scans the **full history** with a pinned gitleaks image, and it passed on `main` (run 39587396126: no leaks). A CI **self-test** now checks that a fake-key commit is flagged (branch `fix/ci-secret-scan-selftest`). Its result will be recorded here. |
| P1-T5 CI and branch protection guidance | **Verified** | On merged `main`, [CI run 39587396126](https://github.com/malikaliraza-2005/VELORA/actions/runs/39587396126): `Backend checks` passed and `Secret scan (gitleaks)` passed. The first run (39586964758) failed only in the old gitleaks-action step, fixed in PR #1. Branch protection guidance is in the root README. **Applying the protection rule on GitHub is pending the owner** (this session's permission settings blocked it). |

**P1 exit criteria:**
- **CI passes on an empty skeleton:** **met** (run 39587396126: both jobs green).
- **The secret scanner blocks a fake-key commit:** *pending* the self-test result on branch `fix/ci-secret-scan-selftest`.

## Resuming the live test

The owner provides:
1. One prepaid Pakistani SIM, registered on WhatsApp Business (dual-SIM on the owner's phone is acceptable).
2. A machine: a Windows computer for short sessions, and either that computer left on or a small VPS for the 14-day soak.
3. The owner's own WhatsApp as the test customer; volunteers optional.

The owner then:
4. Approves the reduced trial variant ([`openwa-validation-plan.md`](openwa-validation-plan.md) §9), or keeps the full plan.
5. Authorises P0-E1, the throwaway trial tooling.

After that, follow the beginner setup guide in the validation plan's appendix.

## Open blockers

| # | Blocker | Blocks | Owner of the action |
|---|---|---|---|
| B1 | Live OpenWA validation (G1–G5) not run; paused for resources | P5 onwards | Founder (resources) + engineering |
| B2 | Legal opinion on WhatsApp Terms-of-Service exposure and consent wording; deferred for budget | Connecting any real merchant (P13) | Founder + lawyer |
| B3 | Tax by payment method not confirmed | Correct totals (P4, P13) | Founder + accountant |
| B4 | Merchant acceptance of a separate number and kitchen device not confirmed | P13 | Founder (interviews) |
| B5 | Vendor prices not fully verified (see price sheet) | Infrastructure commitment (P12) | Engineering |
| B6 | AI model not selected (no real dataset) | P6 | Engineering |
| B7 | Delivery channel for staff magic links undecided | P3 | Engineering (decide during P3) |
| B8 | Owner authorisation for P2–P13 (P1 authorised 2026-10-21) | P2 onwards | Owner |
| B9 | Reduced trial variant and trial tooling (P0-E1) not approved | Resuming the live test | Owner |

## Decisions awaiting the owner

- Review the **ADOPTED (delegated)** decisions AD-11 to AD-24 ([decisions §8](../architecture/architecture-decisions.md#8-items-for-owner-review)).
- Approve or reject the **reduced live-trial variant** ([validation plan §9](openwa-validation-plan.md)).
- **Go/No-Go and V-01 to V-10:** after the live trial.
- **Infrastructure cost commitment:** after vendor checks.
- **Optional customer order-tracking link:** not approved.
- Proposed changes arising from the desk review (see validation plan §10 and decisions §9).

## Change log

| Date | Change |
|---|---|
| 2026-10-21 | PR #1 merged; CI green on `main` (run 39587396126). At the owner's request, the commit history on `main` was rewritten to remove AI co-author trailers (contents unchanged; force-pushed with lease; local backup tag kept). New SHAs: `cd57482`, `24ee91b`, `4e40fae`, `d4e134a`. Self-test branch for fake-key detection opened. |
| 2026-10-21 | Owner pushed `main`. CI backend checks passed; the secret-scan job failed (tool error). Fix on branch `fix/ci-secret-scan`. Owner asked to work in branches from now on. |
| 2026-10-21 | P1 implemented and committed locally (not pushed). Local checks pass. The CI run and secret-scanner blocking are **unverified** (see P1 task evidence). |
| 2026-10-21 | Owner authorised **P1** and chose **Option 2** (provider-neutral build track, recorded as AD-25). GitHub repository provided; commit locally, **do not push**. |
| 2026-10-21 | Implementation plan restructured: master roadmap plus 14 phase files in `phases/`. Phase rows now link to their files. **No status, decision or requirement changed.** |
| 2026-10-21 | Desk tasks completed: OpenWA desk review (validation plan §10), vendor price sheet (decisions §6), sample conversations and interview questions (MVP scope Appendices A–B), beginner setup guide (validation plan Appendix), reduced trial variant (§9, proposed). Proposals PC-1 to PC-13 added to decisions §9 for review. No decisions changed; no gate marked passed. |
| 2026-10-21 | Owner chose: pause the live test, continue free desk work. Status split into desk research (in progress), live validation (paused), P1–P13 (not started), and P5+ (blocked). Reduced trial variant proposed. |
| 2026-10-20 | Owner authorised **P0 only**. P0 task breakdown added. |
| 2026-10-20 | Research moved to `docs/research/`, verified unchanged by SHA-256. Created README, architecture decisions, implementation plan, MVP scope, OpenWA validation plan and this status file. |
