# P0: Pre-implementation gates (no application code)

| Field | Value |
|---|---|
| **Authorisation** | **AUTHORISED (P0 only), 2026-10-20.** Desk work continues. The live OpenWA validation is **PAUSED** (owner choice, 2026-10-21). |
| **Depends on** | Test SIMs; volunteer phones; a VPS for the trial; lawyer and accountant engaged (see P0-B and P0-C) |
| **Blocks** | P5 and everything after it (needs the Go, V-01). P1–P4 do not depend on P0, but are **not authorised**. |
| **Effort (estimate)** | 2–3 calendar weeks. Mostly elapsed time. |
| **Live status** | [`../implementation-status.md`](../implementation-status.md#p0-task-breakdown) is the only place where task status is tracked. This file defines the tasks. |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions

| Decision | Status |
|---|---|
| AD-05: OpenWA is the intended provider; gates G1–G5 are mandatory | APPROVED |
| AD-06: Meta Cloud API is the alternative; insurance track | APPROVED |
| AD-08: lawyer and accountant | **WITHDRAWN** by the owner (AD-35) |
| AD-18: separate ordering number plus consent | ADOPTED (delegated) |
| V-01 to V-10 | VALIDATION-DEPENDENT ([decisions §3](../../architecture/architecture-decisions.md#3-validation-dependent-decisions-decided-only-after-the-openwa-trial)) |
| Reduced trial variant | PROPOSED, not approved ([validation plan §9](../openwa-validation-plan.md#9-reduced-trial-variant-proposed-not-approved-2026-10-21)) |
| PC-1 to PC-13 | PROPOSED, awaiting review ([decisions §9](../../architecture/architecture-decisions.md#9-proposed-changes-from-p0-desk-research-2026-10-21-for-owner-review)) |

## Objective
- **Objective:** produce the evidence the architecture needs before building on OpenWA.

## Scope
- The OpenWA trial ([`openwa-validation-plan.md`](../openwa-validation-plan.md)).
- ~~Legal and accounting brief (AD-08).~~ Withdrawn (AD-35: zero cost, risk accepted).
- Merchant interviews.
- Vendor price checks.
- Collecting a Roman-Urdu utterance set for evaluating AI models.
- Meta Tech Provider application and Coexistence test (AD-06, insurance only).

## Phase deliverables
- Trial evidence log and gate verdicts. Recommendations for V-01 to V-10.
- Lawyer's opinion on Terms-of-Service exposure and consent/indemnity wording.
- Accountant's note on tax by payment method.
- Interview summary covering: willingness to use a separate number, kitchen device, pricing, COD and transfer habits.
- Vendor quote sheet.
- At least 200 anonymised real order utterances.

## Phase security and reliability requirements
- Test numbers only.
- The OpenWA dashboard and API stay on a private network.
- Volunteer numbers are masked in the evidence.
- Session files are never copied into the evidence.

The task IDs (P0-E*, P0-F*) match the [status tracker](../implementation-status.md#p0-task-breakdown).

---

## P0-A: Free desk work (no cost, no code, no live tests)

### P0-E2: OpenWA source and documentation desk review
- **Prerequisites:** none. Read-only web access.
- **Deliverables:** findings labelled VERIFIED / INFERRED / LIVE TEST, with the commit and release recorded ([validation plan §10](../openwa-validation-plan.md#10-desk-review-of-openwa-source-and-docs-p0-e2-2026-10-21)).
- **Security:** nothing cloned, installed or run. No accounts created.
- **Tests:** every claim cites a source file or URL.
- **Acceptance:**
  - A reviewed commit SHA is recorded.
  - Each of G1–G5 has desk findings.
  - **No gate is marked passed from desk evidence.**
- **Recovery:** if OpenWA changes, re-review at the version pinned for the live trial.

### P0-E4: Vendor price sheet
- **Prerequisites:** none.
- **Deliverables:** a price sheet with sources, dates checked, free-tier limits and uncertainties ([decisions §6](../../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing)).
- **Security:** no purchases or sign-ups.
- **Tests:** every row is marked VERIFIED (official page) or UNVERIFIED.
- **Acceptance:** VPS, database, backup storage, monitoring and LLM are all covered, with low, typical and high totals.
- **Recovery:** re-check prices before any purchase (P12).

### P0-E7: Customer message samples, merchant interview questions, beginner setup guide
- **Prerequisites:** none.
- **Deliverables:**
  - [MVP scope Appendix A](../mvp-scope.md#appendix-a-sample-customer-whatsapp-conversations-draft-2026-10-21) (conversations and synthetic utterances);
  - [Appendix B](../mvp-scope.md#appendix-b-merchant-interview-questions-draft-2026-10-21) (interview questions);
  - [validation plan Appendix](../openwa-validation-plan.md#appendix-beginner-setup-guide-for-the-live-test-draft-2026-10-21) (setup guide).
- **Security:** fictional names and numbers only. The guide forbids sharing QR codes or session files.
- **Tests:** the samples are consistent with AD-03 (no paid status from screenshots), AD-20 (YES confirmation, templated totals) and AD-14 (voice deferred).
- **Acceptance:** each is marked as a draft, not tested with real users.
- **Recovery:** revise after the interviews and the live trial.

### P0-E8: Proposals from the desk research
- **Prerequisites:** E2, E4.
- **Deliverables:** PC-1 to PC-13 in [decisions §9](../../architecture/architecture-decisions.md#9-proposed-changes-from-p0-desk-research-2026-10-21-for-owner-review).
- **Acceptance:** each proposal names the decision it affects and its evidence. **No existing decision is changed** until the owner approves.

---

## P0-B: Live OpenWA validation (**PAUSED**)

Test cases, pass and fail criteria, schedule and evidence template are defined **only** in [`../openwa-validation-plan.md`](../openwa-validation-plan.md), §3–§7. They are not copied here.

To resume, follow the steps in the [status tracker](../implementation-status.md#resuming-the-live-test).

### P0-F1: Test business number
- **Prerequisites:** owner decision on the full plan or the reduced variant.
- **Deliverables:** one prepaid Pakistani SIM registered on WhatsApp Business for the reduced variant, or BIZ-1, BIZ-2 and BIZ-SPARE for the full plan.
- **Security:** **never** use merchants' numbers or the owner's personal WhatsApp number. Numbers are masked in shared docs.
- **Acceptance:** the number is registered and noted privately.

### P0-F2: Test customers
- **Prerequisites:** F1.
- **Deliverables:** the owner's phone; 3–4 customer phones plus ≥20 first-contact volunteers for the full plan. Volunteers are optional in the reduced variant.
- **Security:** written or recorded consent from every volunteer. **No messages to anyone who hasn't agreed.**
- **Acceptance:** a consent list is kept privately.

### P0-F3: Machine to run OpenWA
- **Prerequisites:** none.
- **Deliverables:**
  - a Windows computer for short sessions;
  - a computer left on, or a small VPS, for the 14-day soak (costs in the price sheet).
- **Security:** OpenWA dashboard and API on a private network only. Sleep disabled during the test.
- **Acceptance:** Docker runs (see the setup guide, Part 2).

### P0-E1: Trial tooling (throwaway; **NOT AUTHORISED** — it is code)
- **Prerequisites:** owner authorisation.
- **Deliverables:**
  - a webhook recorder (modes 200 / 500 / hang / down);
  - an OpenWA docker-compose per engine on a private network;
  - a resource-sampling script;
  - an evidence-log template;
  - all in a separate `trial/` folder.
- **Security:**
  - OpenWA pinned to a recorded version.
  - No secrets committed.
  - Session data excluded from any copy or evidence.
  - Config baseline per [validation plan §10.6](../openwa-validation-plan.md) (proposed, PC-12).
- **Tests:** the recorder logs raw headers and body. Each mode can be switched with one command.
- **Acceptance:** a dry run with no WhatsApp linked shows all recorder modes working.
- **Rollback:** delete the `trial/` folder. It is never part of product code.

### P0-E3: Run the live schedule
- **Prerequisites:** F1–F3, E1, and the owner's choice of plan variant.
- **Deliverables:** an evidence log with one row per test run (validation plan §7).
- **Security:**
  - Test numbers only.
  - Masked volunteer numbers.
  - Session files never in the evidence.
  - **No detection-evasion features.**
- **Tests:** G1–G5 per validation plan §4. Possible additions are listed in §10.6 (proposed, not applied).
- **Acceptance:** **every test in the validation plan has an evidence row.** Any item that can't be tested is recorded as INSUFFICIENT EVIDENCE. **A gate with any INSUFFICIENT EVIDENCE cannot pass.**
- **Stop and recovery:** **a test number gets restricted:** stop that engine. This counts as critical evidence. A passkey prompt that blocks linking (#560) is also recorded as critical evidence.

---

## P0-C: External gates

### P0-F4: Lawyer and accountant
- **Status:** deferred for budget. **Required before any real merchant is connected** (P13).
- **Deliverables:**
  - **Lawyer's opinion** on Terms-of-Service exposure and consent/indemnity wording.
  - **Accountant's note** on tax by payment method.
  - The question list is in [decisions §4](../../architecture/architecture-decisions.md#4-pending-external).
- **Acceptance:** written opinions received.

### P0-F5: Merchant interviews
- **Deliverables:** an interview summary covering:
  - willingness to use a separate number;
  - kitchen device;
  - pricing;
  - COD and transfer habits.
  - Uses the questions in [MVP scope Appendix B](../mvp-scope.md#appendix-b-merchant-interview-questions-draft-2026-10-21).
- **Security:** consent before notes. No customer phone numbers recorded.
- **Acceptance:** summaries are completed for the assumptions S2 and S3 (MVP scope §4).

### P0-F6: Real order-message dataset
- **Deliverables:** **at least 200 anonymised real order utterances.**
- **Security:** consent from contributors; no phone numbers.
- **Acceptance:** at least 200 utterances, each labelled with the expected interpretation.

### P0-E5: AI model evaluation (offline)
- **Prerequisites:** F6. The synthetic set in MVP scope A15 is a provisional sanity check only.
- **Deliverables:** an accuracy report per candidate model. The candidate list is proposed in PC-10.
- **Security:** paid tiers with no training on data (AD-04); anonymised data only.
- **Acceptance:** results recorded. The target of at least 90% correct proposals is an estimate the owner must confirm (P6).

### P0-F7: Meta insurance track
- **Status:** deferred.
- **Deliverables:** Meta Tech Provider application; a Coexistence test on a +92 number (AD-06).
- **Acceptance:** an application reference or test notes.

---

## P0-D: Gate review and Go / No-Go

### P0-E6: Gate review
- **Prerequisites:** E3 is complete, or the owner explicitly accepts the gaps.
- **Deliverables:**
  - gate verdicts for G1–G5;
  - recommendations for V-01 to V-10;
  - a Go/No-Go recommendation ([validation plan §6](../openwa-validation-plan.md#6-decisions-after-the-trial)).
- **Acceptance:**
  - **A Go/No-Go recommendation is presented to the owner.**
  - **The owner has decided V-01 to V-10.**
  - Any gaps the owner accepts are recorded in writing as risks, not as passes.

## Phase acceptance criteria (exit)
- Every test in the validation plan has an evidence row.
- A Go/No-Go recommendation is presented to the owner.
- The owner has decided V-01 to V-10.

## Risks and rollback
- **A test number gets restricted:** stop that engine. This counts as critical evidence.
- **No-Go:** the owner chooses between the Meta adapter (AD-06) and stopping. P1–P4 work can still be reused, since it doesn't depend on the provider.

## Open items
- Reduced trial variant: approval pending.
- P0-E1 tooling authorisation pending.
- PC-5 (OpenWA send pacing) needs an explicit owner decision.
- PC-12 test additions are awaiting review.
