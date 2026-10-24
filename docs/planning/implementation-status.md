# VELORA implementation status

- **Last updated:** 2026-10-24
- **Plan:** [`implementation-plan.md`](implementation-plan.md) (master roadmap); detailed tasks per phase in [`phases/`](phases/)
- **Decisions:** [`../architecture/architecture-decisions.md`](../architecture/architecture-decisions.md)

> **Evidence rule:** a phase or check counts as done only when a real artifact backs it, such as a test report, an evidence-log row, a CI run link, a drill record, or a signed document. Never mark anything done on assumption. **No live test results exist yet.**

## Overall status

| Area | Status |
|---|---|
| **P0 desk research** | **In progress.** Free, non-code work only (owner choice, 2026-10-21). |
| **P0 live OpenWA validation** | **Paused** until a test SIM and the required setup exist (see "Resuming the live test"). |
| **P1** | **Complete (2026-10-21).** Both exit criteria verified in CI. |
| **P2** | **Complete (2026-10-21).** Authorised by the owner ("Start P2"). Merged in PR #3; CI green. |
| **P3** | **Complete (2026-10-21).** Authorised by the owner ("Start P3"). PR #4; all five exit criteria verified in CI. See "P3 task evidence". |
| **P4** | **Complete (2026-10-21).** Authorised by the owner ("Start P4"). PR #5; all four exit criteria verified in CI. See "P4 task evidence". |
| **P5** | **Provider-neutral part complete (2026-10-21).** Authorised by the owner ("Start P5"). Built under AD-25 against the `FakeProvider`. **The OpenWA part (T2, the OpenWA parts of T3 and T5, and T6) is not built and stays blocked** until the Go (V-01). See "P5 task evidence". |
| **P6** | **Provider-neutral part built (2026-10-21).** Authorised by the owner ("Start P6"). Built under AD-25 against the `FakeProvider`, with Groq as the first AI adapter (AD-30). Order creation on YES is P7. **Real customer text must not go to Groq** until the owner decides on AD-04 (see AD-30). See "P6 task evidence". |
| **P7** | **Provider-neutral part built (2026-10-21).** Authorised by the owner ("Start P7"). Built under AD-25 against the `FakeProvider`. Choices recorded in AD-31. See "P7 task evidence". |
| **P8** | **Provider-neutral part built (2026-10-21).** Authorised by the owner ("Start P8"). Built under AD-25 against the `FakeProvider`. Choices recorded in AD-32. Each restaurant must enter its account details before transfer is offered. See "P8 task evidence". |
| **P9** | **Provider-neutral part built (2026-10-22).** Authorised by the owner ("Start P9"). Built under AD-25 against the `FakeProvider`. Choices recorded in AD-33. The staging end-to-end run with a test number and the usability check with kitchen staff (P9-T8) wait for the Go, P12 and P13. See "P9 task evidence". |
| **P10** | **Provider-neutral part built (2026-10-22).** Authorised by the owner ("Start P10"), read as the provider-neutral part before the Go, like P5–P9 (to be confirmed, AD-34). Built against the `FakeProvider`. Structured logs, the monitor role and metrics, alerts with the four drills, and the founder's status and daily metrics. Alerts will use free services (AD-35: zero cost); real delivery to the founder is verified once they are set up. See "P10 task evidence". |
| **P11** | **Built (2026-10-23), except what needs the Go or staging.** Authorised by the owner ("Start P11"). Encrypted backups by a dedicated backup role, restore with verification, and recovery mode on every worker start, with all nine steps (inbound reconciliation is a hook until V-04). The restore drill and the recovery drill pass in CI; the runbooks are written. The 24-hour backup run, the staging drills and the runbook walk-throughs wait for P12. Choices in AD-36. See "P11 task evidence". |
| **P12** | **Built (2026-10-23), except what needs a server or the Go.** Authorised by the owner ("Start P12"). The application image, the container layout (only 443 published; OpenWA on its own internal network once the Go is given), HTTPS with Caddy and HSTS, owner-only settings files with a check, server provisioning, the host-hardening script, release and rollback, hourly backups, and an external port scan. A CI job rehearses the whole deployment on every push: a first release, the edge and secrets checks, the drills (worker crash, API down with the alert, backup and restore into service) and a release with a rollback. **No server exists yet** (V-03, B5, B12): the timed from-scratch run, the real port scan, staging and the staging drills wait for one. Choices in AD-37. See "P12 task evidence". |
| **Local completion (AD-38, AD-39)** | **Done (2026-10-24).** Data retention, the worker-stopped alert, the consent form draft, the owner-verification runbook, and the OpenWA trial kit (reduced variant). Everything left needs the SIM, the Go, a server, or the owner. |
| **P13** | **Not started. Not authorised.** Two of its preconditions are drafted: the consent form and the owner-verification runbook (AD-38). |
| **Provider-neutral track (AD-25)** | Owner decision 2026-10-21: the provider-neutral parts of P5, and P6–P9, may be built against the `FakeProvider` before the OpenWA Go, each once it is authorised. |
| **OpenWA adapter, real WhatsApp traffic, P12's OpenWA container and drills, P13** | **Blocked** until the live validation is complete and the owner gives a Go (V-01). |
| **Application code** | P1 skeleton, the P2 database layer, P3 staff identity (login, sessions, roles, PINs, audit, founder onboarding tool), P4 menu and ordering controls (versioned catalogue, CSV import, publish and rollback, availability, pause, capacity, settings, server-side pricing), and the P5 provider-neutral messaging core (provider interface, FakeProvider, durable inbox and outbox, channel state machine, circuit breaker, global pause, worker loop, log redaction). P6 conversations (engine, commands, numbered menus, AI proposals via Groq, cart, checkout, templated summary, handoff). P7 order lifecycle (YES nonce, order transaction, transition table, timers, status messages, recovery hook, staff order API). P8 payment verification (append-only ledger, reference + PIN verification, duplicate references, late-payment review, owner reconciliation and reversal, COD blocklist, transfer details, customer evidence). P9 staff dashboard (installable PWA in `web/`: order board, payments, chats, menu availability, controls, today summary, WhatsApp status and QR re-link, staff, settings; idempotency keys on every action; CSP with Trusted Types; browser tests). P10 monitoring (JSON logs with tenant and correlation ids, `/readyz`, the `velora_monitor` role, metrics, alert rules with email and webhook delivery, heartbeat, founder status and daily pilot metrics). Data retention (`velora.retention`, AD-38). P12 deployment (the `velora.serve` production server, `velora.deploy` settings files and port scan, `velora.db provision`, and `infra/`: the image, compose, Caddy, the host script, the `velora` tool). P11 backups and recovery (the `velora_backup` role, backups encrypted for the founder alone, stored in a directory or an S3-compatible bucket, restore with verification, recovery mode on startup, the "placed after an outage" flag, runbooks). No real WhatsApp connection. |
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
| [P0 Pre-implementation gates](phases/p00-pre-implementation-gates.md) | **Desk research in progress; live test paused** | Test SIM. The reduced variant is ADOPTED (delegated, AD-39), so one SIM, the owner's phone and computer are enough; the 14-day soak needs a machine left on. (No lawyer or accountant: AD-35) | Desk review notes (see P0 tasks) | Finish desk tasks; owner decides on the reduced trial variant |
| [P1 Repository foundation](phases/p01-repository-foundation.md) | **Complete** (2026-10-21) | — | CI runs 39587396126 (main) and 39587837550 (PR #2 self-test); see "P1 task evidence" | Owner: apply the branch protection rule (see README) |
| [P2 Database and tenant isolation](phases/p02-database-and-tenant-isolation.md) | **Complete** (2026-10-21) | — | PR #3; CI runs 39590467933 (PR) and 39590889951 (main); 187 tests | — |
| [P3 Onboarding and staff identity](phases/p03-onboarding-and-staff-identity.md) | **Complete** (2026-10-21) | — (B7 resolved for the pilot: manual delivery, AD-27) | PR #4; CI run 39594079256 (315 tests); see "P3 task evidence" | Owner: review AD-27 |
| [P4 Menu, availability and settings](phases/p04-menu-availability-and-settings.md) | **Complete** (2026-10-21) | — (tax values wait for the accountant, B3; quotes are refused until set) | PR #5; CI run 39598901486 (491 tests); see "P4 task evidence" | Owner: review AD-28; authorise the next phase when ready |
| [P5 Provider abstraction and OpenWA adapter](phases/p05-whatsapp-provider-and-openwa-adapter.md) | **Provider-neutral part complete** (2026-10-21). **OpenWA part blocked** | OpenWA part: **live validation + Go (V-01)**; V-02, V-04, V-05, V-06, V-07, V-08 | PR #6; CI run 39604566511 (580 tests). See "P5 task evidence" | Owner: review AD-29 |
| [P6 Conversation workflows](phases/p06-conversation-workflows.md) | **Provider-neutral part built** (2026-10-21) | Real-WhatsApp exit tests: Go (V-01). Split limit provisional: V-09. AI accuracy on real data: P0-F6. Groq for real customers: decided, free tier (AD-35) | PR #7; CI run 39613089570 (652 tests). See "P6 task evidence" | Owner: review AD-30 and decide on Groq vs AD-04; confirm the 90% target; authorise P7 when ready |
| [P7 Order lifecycle](phases/p07-order-lifecycle.md) | **Provider-neutral part built** (2026-10-21) | Real-WhatsApp exit tests: Go (V-01). The P11 drill also exercises the recovery hook | PR #9; CI run 39636189526 (702 tests). See "P7 task evidence" | Owner: review AD-31 |
| [P8 Payment verification](phases/p08-payment-verification.md) | **Provider-neutral part built** (2026-10-21) | Real-WhatsApp exit tests: Go (V-01). Evidence media for staff: P5-T2 | PR #10; CI run 39641355254 (761 tests). See "P8 task evidence" | Owner: review AD-32 |
| [P9 Staff dashboard](phases/p09-staff-dashboard.md) | **Provider-neutral part built** (2026-10-22) | Staging E2E with a test number: Go (V-01) and P12. Usability check: P13 | PR #12; CI run 39775329133 (810 tests). See "P9 task evidence" | Owner: review AD-33 |
| [P10 Monitoring](phases/p10-monitoring-and-alerting.md) | **Provider-neutral part built** (2026-10-22) | Real alert delivery: free mailbox and heartbeat set up (AD-35). Daily metrics in staging: P12. HMAC-failure alert from real webhooks: P5-T3 | PR #15; CI run 39808148631 (929 tests). See "P10 task evidence" | Owner: review AD-34 |
| [P11 Backups and recovery](phases/p11-backups-recovery-and-runbooks.md) | **Built** (2026-10-23), except the staging runs | 24-hour backup run, staging drills, runbook walk-throughs: P12. Inbound reconciliation: V-04. Storage vendor: B5 | PR #17; CI run 39921013960 (981 tests). See "P11 task evidence" | Owner: review AD-36; make the backup key (runbook R4) |
| [P12 Deployment and staging](phases/p12-deployment-and-staging.md) | **Built** (2026-10-23), rehearsed in CI; no server yet | A free VM (V-03, B5), a hostname (B12), alert delivery (B11), the backup bucket (B5); then the timed run (T6), the real scan (T1), staging (T4, T5) and its drills (T7). OpenWA container and its two drills: the Go (V-01) | PR #18; CI run 39941118477 (1027 tests, and the deployment rehearsal). See "P12 task evidence" | Owner: review AD-37; create the VM and hostname |
| [P13 Pilot onboarding](phases/p13-pilot-onboarding.md) | Not started — blocked | P12; legal brief; consent form; tax confirmation; owner approval | — | — |

## P0 task breakdown

A task only counts as done when its evidence exists.

### Founder (paused unless noted)

| # | Task | Status | Evidence when done |
|---|---|---|---|
| P0-F1 | Get **one** prepaid Pakistani test SIM for the "restaurant" number (the full plan's 3 SIMs are reduced in the proposed variant). **Never use merchants' numbers or your personal WhatsApp number.** | Paused | SIM noted privately; number masked in shared docs |
| P0-F2 | Test customers: the owner's own phone, plus volunteers if available (reduced variant) | Paused | Consent list (private) |
| P0-F3 | Machine to run OpenWA: a Windows computer for short sessions, and/or a small VPS for the 14-day soak | Paused | Noted in the price sheet |
| P0-F4 | Lawyer and accountant quotes, using the prepared brief questions | **Withdrawn** by the owner (AD-35: zero cost; risk accepted) | — |
| P0-F5 | Merchant interviews, using the prepared question list | Paused; optional while waiting | Interview notes and a summary |
| P0-F6 | Real, anonymised order messages (target ≥200) | Paused. Synthetic samples prepared meanwhile (provisional) | Dataset with no phone numbers |
| P0-F7 | *Insurance:* Meta Tech Provider application; Coexistence test | Deferred | Application reference |

### Engineering (Claude)

| # | Task | Status | Evidence |
|---|---|---|---|
| P0-E1 | Trial tooling (webhook recorder, OpenWA compose, resource sampler, evidence-log template) | **Done (2026-10-24), ADOPTED (delegated), AD-39.** Checked on the owner's computer without a linked number: the stack starts, a session reaches `qr_ready`, signed test webhooks reach the recorder. Tests in `backend/tests/trial/` | [`../../trial/`](../../trial/) |
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
| P1-T4 Secret scanning | **Verified in CI** | Full-history gitleaks scan: `no leaks found` ([run 39587837550](https://github.com/malikaliraza-2005/VELORA/actions/runs/39587837550)). Self-test: the fake-key commit was flagged (`leaks found: 1`, then PASS) in the same run. The local pre-commit hook remains blocked on the founder's machine by Windows Application Control, so CI is the enforcing control. |
| P1-T5 CI and branch protection guidance | **Verified** | On merged `main`, [CI run 39587396126](https://github.com/malikaliraza-2005/VELORA/actions/runs/39587396126): `Backend checks` passed and `Secret scan (gitleaks)` passed. The first run (39586964758) failed only in the old gitleaks-action step, fixed in PR #1. Branch protection guidance is in the root README. **Applying the protection rule on GitHub is pending the owner** (this session's permission settings blocked it). |

**P1 exit criteria:**
- **CI passes on an empty skeleton:** **met** (run 39587396126: both jobs green).
- **The secret scanner blocks a fake-key commit:** **met**. The CI self-test flagged the fake-key commit (run 39587837550).

## P2 task evidence (2026-10-21)

| Task | Status | Evidence |
|---|---|---|
| P2-T1 Migration tooling and roles | Done | Role-attribute tests: `app_rw` is NOBYPASSRLS and owns nothing |
| P2-T2 Core schema | Done | Schema tests: no float money; same order number allowed across tenants; customer delete blocked; anonymisation |
| P2-T3 RLS policy module | Done | Enumeration test over every table |
| P2-T4 Bootstrap resolver | Done | Returns only `tenant_id`; PUBLIC can't execute it; the only SECURITY DEFINER function (catalog test) |
| P2-T5 Database-access helper | Done | Raises before any SQL without a tenant |
| P2-T6 Isolation suite | Done | Cross-tenant, fail-closed and restore tests, in CI on every run |

**P2 exit criteria:** all four met in CI: [run 39590467933](https://github.com/malikaliraza-2005/VELORA/actions/runs/39590467933) (PR #3) and [run 39590889951](https://github.com/malikaliraza-2005/VELORA/actions/runs/39590889951) (`main`). Implementation choices: decisions AD-26.

## P3 task evidence (2026-10-21)

Implementation choices are recorded as **ADOPTED (delegated)** in decisions AD-27, for owner review.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P3-T1 Founder onboarding tools | Done (command-line tool, AD-27) | `db/test_onboarding.py`: go-live refused without both records (tool and database); audit rows exist; records immutable; platform role scoped to one tenant |
| P3-T2 Magic-link login | Done | `api/test_login_and_sessions.py`: reused link fails; expired link fails; forged tenant fails; rate limit refuses |
| P3-T3 Device sessions | Done | Same file: logout, log out all devices, owner revocation, role change and removal each give 401; idle and absolute expiry (simulated clock); CSRF; cross-tenant access denied |
| P3-T4 Invite flow and roles | Done | `identity/test_units.py` (matrix); `api/test_roles_and_pins.py` (kitchen device can't change menu or settings) |
| P3-T5 Payment PIN | Done | `api/test_roles_and_pins.py`: confirming without a valid PIN is rejected and logged; lockout; kitchen-device flow |
| P3-T6 Audit logging | Done | `api/test_audit_coverage.py`: every state-changing endpoint writes an audit row, and the test fails if a new endpoint is not covered |

**P3 exit criteria:** all five are **met in CI**: [run 39594079256](https://github.com/malikaliraza-2005/VELORA/actions/runs/39594079256) (PR #4). 315 tests passed against PostgreSQL 17, and the secret scan was clean.

**Findings for later phases:**
- **P8:** call `StaffService.verify_payment_pin` before confirming a payment. The test stand-in shows the pattern.
- **P4:** menu endpoints must require `Permission.MENU_EDIT`.
- **P9:** the login page reads the token from the URL fragment and posts it to `/api/auth/login`.
- **P12:** configure trusted proxy headers, or the login rate limit becomes global.

## P4 task evidence (2026-10-21)

Implementation choices are recorded as **ADOPTED (delegated)** in decisions AD-28, for owner review.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P4-T1 Catalogue schema and APIs | Done | Migration `0006`. `db/test_isolation.py` covers the 9 new tables (FORCE RLS, cross-tenant, fail-closed). `db/test_menu_schema.py`: published versions can't change (even for the administrator), prices can't be negative, SKUs are unique. `api/test_menu.py`: price changes audited with from/to values |
| P4-T2 Import and validation | Done | `menu/test_csv_import.py`: each rule (negative price, required fields, duplicate SKU, format, header, options) gives a line-level error. `api/test_menu.py`: an invalid CSV is rejected with line errors and nothing is saved; founder `menu-import` fills a draft only |
| P4-T3 Draft, review and publish | Done | `api/test_menu.py`: draft items are invisible to customer-facing reads; re-publishing the previous version restores it; publishing a stale revision is refused. `db/test_menu_schema.py`: only a frozen version can be published; the platform role can't publish or freeze |
| P4-T4 Availability toggles | Done | `api/test_menu.py`: an unavailable item is never offered or priced, and stays unavailable across a re-publish |
| P4-T5 Pause and capacity | Done | `api/test_ordering.py`: pausing blocks new carts within 5 seconds; the capacity cap rejects orders above the limit and reopens in the next slot; 10 concurrent claims never overshoot a cap of 3 |
| P4-T6 Settings | Done (placeholder tax values only, B3) | `api/test_ordering.py`: each setting is applied by pricing (fee, minimum, tax by payment method, switched-off options); an unset tax rate refuses the quote; changes are audited with old and new values; kitchen roles are refused. `ordering/test_pricing.py`: pricing and rule unit tests |

**P4 exit criteria:** all four are **met in CI**: [run 39598901486](https://github.com/malikaliraza-2005/VELORA/actions/runs/39598901486) (PR #5). 491 tests passed against PostgreSQL 17, and the secret scan was clean.

**Findings for later phases:**
- **P6:** offer only `LiveMenu.offerable()`. Price every cart with `velora.ordering.pricing.price_cart` and check `gate.check_new_cart` before starting a cart. Pickup times use `check_pickup_time`.
- **P7:** inside the order transaction, re-run the gate and `price_cart` and compare with the quote (`menu_version_id` and the totals). Then call `gate.claim_capacity`. Releasing a place on cancellation still needs designing. `order_items` has no column for chosen options yet, so a migration is needed to snapshot them.
- **P8:** use `cod_allowed` (first-order limit) and `transfer_deadline` (transfer window).
- **P9:** the dashboard uses `/api/menu/*`, `/api/ordering/*` and `/api/settings/ordering`.
- **P13:** set tax rates only after the accountant confirms them (B3). Until then, quotes for that payment method are refused.

## P5 task evidence (2026-10-21)

Only the provider-neutral track that AD-25 allows was built. Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-29, for owner review.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P5-T1 Interface, canonical events, FakeProvider | Done | `messaging/test_provider_contract.py`: the interface has exactly the planned methods; a contract suite runs against every working adapter (the FakeProvider now; OpenWA joins after the Go); webhook round trip; a bad, missing or tampered signature is rejected; a payload naming another session is rejected |
| P5-T2 OpenWA adapter | **Not started: blocked (V-01, V-02)** | — |
| P5-T3 Ingress and filtering | **Core done**; the OpenWA webhook endpoint is blocked | `messaging/test_inbox.py`: a duplicate webhook doesn't create a second event; group, status and broadcast events leave no row; a bad signature stores nothing; a mismatched or unknown session stores nothing; events are committed before `receive` returns; **a real worker process is killed mid-processing, and the event is processed exactly once after its lease expires**; a worker that lost its lease can't commit; per-customer order |
| P5-T4 Outbox sender | **Core done**; the OpenWA timeout handling waits for V-06 | `messaging/test_outbox.py`: **with the channel down, messages are held and then flushed in order once reconnected**, using no attempts; stale replies are replaced by one "we're back" message per customer; a critical message with an unknown outcome is not re-sent; backoff 10 s to 15 m, then failed; no overtaking; rate cap; receipts only move forward; stored error text is redacted |
| P5-T5 Channel state machine and health monitor | **Core done**; detection from OpenWA webhooks and polls is blocked | `messaging/test_channels.py`: **the circuit breaker pauses new orders within 60 seconds of `needs_qr`** (health poll), and at once from a session event or a send error; reconnecting reopens; **the global pause blocks new orders for every tenant**; only the founder's tool can set it, and its history can't be changed |
| P5-T6 Inbound recovery | **Not started: blocked (V-04)** | — |
| P5-T7 Meta Cloud stub | Done | `messaging/test_provider_contract.py`: it fits the interface and every method raises "not implemented" (AD-06) |
| P5-T8 Provider boundary and log redaction | Done | `test_provider_boundary.py`: a static import-graph check that business modules have no path to an adapter, plus a self-test that it catches direct and indirect paths. `test_redaction.py`: secrets, URL passwords, tokens and phone numbers are scrubbed from log lines and tracebacks |
| Work discovery (AD-26 finding) | Done | `messaging/test_dispatch.py`: `velora_dispatcher` finds only tenants with due work, can't read any payload or tenant data, and can't write; a worker round serves every tenant, and one tenant's failure doesn't stop the others. `db/test_isolation.py` lists its policies; `db/test_resolver.py` still finds only one SECURITY DEFINER function |

**P5 exit criteria** (the provider-neutral ones):
- **Replays:** met. A duplicate webhook doesn't create a second event.
- **Crash test:** met. A killed worker's event is processed exactly once after its lease expires.
- **Channel down:** met. Held messages are flushed in order.
- **Circuit breaker:** met. It closes within 60 seconds of `needs_qr`.
- **Provider boundary:** met. A static check enforces it.
- **Signatures:** met for the core (nothing is stored). The 4xx response belongs to the OpenWA endpoint, which is **blocked**.
- **Contract tests against real OpenWA payloads recorded in P0:** **not met; blocked** (V-01, and the paused live trial).

**Verified in CI:** [run 39604566511](https://github.com/malikaliraza-2005/VELORA/actions/runs/39604566511) (PR #6). 580 tests passed against PostgreSQL 17, and the secret scan was clean.

**Findings for later phases:**
- **P5-T2 (after the Go):** add `OpenWAAdapter` to `CONTRACT_PROVIDERS` with payloads recorded in P0, register it in `velora.messaging.registry`, and add the webhook endpoint. The endpoint must call `parse_webhook`, then `Ingress.receive`, and return 2xx only after `receive` returns. It must map `InvalidWebhookError` and `SessionMismatchError` to 4xx, `UnknownChannelError` to 404, and anything else to 5xx.
- **P6:**
  - Register a `message` handler with `InboxProcessor`. Inbound messages wait, stored, until then.
  - Queue replies with `velora.messaging.outbox.enqueue` inside the same transaction as the state change.
  - Use `purpose="conversational"` for replies and `"status_update"` for order updates. Set `critical=True` for payment instructions and order confirmations.
  - Write the customer wording for `WE_ARE_BACK_TEXT`.
  - Check `capabilities()` before using buttons or lists.
- **P9:** show `unknown` and `failed` messages to staff. Show the channel state and the `channel_unavailable` and `global_pause` gate reasons.
- **P10:** alert on channel state changes (`channel_events`), on `unknown` or `failed` messages, and on inbox events that end `failed`.
- **P11:**
  - Recovery mode marks in-flight (`sending`) messages `unknown`. The outbox already does this for critical messages after a lease ends.
  - The outbox payload holds the recipient's number, so retention must cover `outbox_messages` and `inbox_events`.
- **P12:**
  - Run `python -m velora.workers` with `VELORA_DISPATCHER_DATABASE_URL`, and set the `velora_dispatcher` password out of band.
  - Send the API's (uvicorn) logs through `velora.redaction.RedactingFormatter` too. The worker already does this.

## P6 task evidence (2026-10-21)

Only the provider-neutral track that AD-25 allows was built. Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-30, for owner review.

| Task | Status | Evidence (tests in `backend/tests/conversation/`) |
|---|---|---|
| P6-T1 Engine and serialisation | Done | `test_concurrency.py`: **two simultaneous messages, raced by two workers, are processed in order** (5 rounds). The handler, called directly from two transactions, serialises; removing the locks makes this test fail (checked by hand) |
| P6-T2 Fixed commands and disclosures | Done | `test_commands.py`: the first reply has the disclosure; "CANCEL" never opts out; "STOP" opts out, cancels queued replies, then silence until START; only exactly STOP; "status"; voice note reply |
| P6-T3 Menu renderer | Done (split limit provisional, V-09) | `test_menu.py`: **an unavailable item is never offered or added**, even from an old list, and is removed from the cart if it runs out; a 60-item menu is split within the limit |
| P6-T4 AI proposal module | Done (Groq adapter; real-customer use waits for the owner, AD-30) | `test_ai.py`: **6 injection prompts answered by a compromised model never change prices, tenant or state** (database snapshots equal); the Groq request body has no phone, address, identity or price fields; timeout, error and garbage fall back to the menu; the per-tenant switch; strict output parsing; the key appears only in the Authorization header |
| P6-T5 Cart, fulfilment and templated summary | Done (order creation is P7) | `test_checkout.py`: **summary amounts equal independently computed database values**; **the tax line changes with the payment method**; expired or changed summary shown again; delivery minimum; pickup window; pause; an unset tax rate removes the method. `test_ai.py`: model totals and text never reach a message |
| P6-T6 Human handoff | Done (inbox screen is P9) | `test_handoff.py`: every trigger, including two misses; the bot sends nothing while handed over; Resume bot restores it, keeps the cart and is audited; kitchen roles refused. `api/test_audit_coverage.py` covers the endpoint |
| P6-T7 Offline evaluation harness | Done (harness only; accuracy not measured) | `test_units.py`: the harness scores correct and failed cases. The synthetic A15 dataset is in `backend/evaluation/`. Running it against Groq needs `VELORA_GROQ_API_KEY` |

**Verified in CI:** [run 39613089570](https://github.com/malikaliraza-2005/VELORA/actions/runs/39613089570) (PR #7). 652 tests passed against PostgreSQL 17, and the secret scan was clean.

**P6 exit criteria:**
- **Deterministic:** met. An unavailable item is never added; prices always equal the database values; AI output is never sent verbatim as a total.
- **Injection:** met for the suite above.
- **Concurrency:** met.
- **AI accuracy ≥90% (offline, not a CI gate):** **not measured on real data.** It needs the real P0-F6 dataset, and the target is still an estimate.
  - First sanity check, 2026-10-21: Groq free tier, `openai/gpt-oss-20b`, synthetic A15 set, **18/20 = 90%**, about 1 s per request.
  - Misses: "3 chicken biryani half" was read as quantity 1. "cod karunga" was read as a payment claim, but the customer only means to pay cash, so the reply would be wrong.
  - Both are safe in the engine: the customer confirms a proposal with YES, and a payment claim confirms nothing.
  - 20 cases is too few to choose a model. The free tier needs about 8 seconds between requests to stay under its tokens-per-minute limit.
- **Real-WhatsApp exit tests:** gated by the Go (V-01).

**Findings for later phases:** see AD-30. In short:
- **P5-T2:** location coordinates.
- **P7:** order creation on YES against `context.quote`, CANCEL of placed orders, and whether status updates go to opted-out customers.
- **P9:** the handoff inbox and the `ai_enabled` switch.
- **P11:** `conversations.context` holds addresses.

## P7 task evidence (2026-10-21)

Only the provider-neutral track that AD-25 allows was built. Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-31, for owner review.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P7-T1 Confirmation nonce | Done | `orders/test_confirmation.py`: **an expired nonce can't create an order** (directly, and YES shows a new summary); a nonce bound to another customer, conversation, cart or address is rejected; a nonce works once; a new summary voids the old one; only the nonce's hash is stored |
| P7-T2 Order-creation transaction | Done | `orders/test_placement.py`: **two simultaneous YES replies create exactly one order, 100 of 100 runs**; 20 runs of two racing order transactions without the chat lock also create one; **a price change or an item running out between the summary and YES is refused and re-confirmed**; a pause or a full slot refuses; **an injected failure after each of the 5 steps commits nothing**, and the same YES then works; snapshots, lines with options, first transition, audit row and the critical confirmation |
| P7-T3 States and transition table | Done | `orders/test_lifecycle.py`: **the database guard accepts exactly the table's moves** (every pair, for every payment and fulfilment); **the engine refuses every illegal move and every wrong actor** (every pair × actor × kind); a stale move is refused; staff must give a reason; **a customer cancel after acceptance is refused**; cancel before acceptance works and gives the capacity place back; **every transition writes a history row and an audit row**. `api/test_orders.py`: the kitchen device progresses an order; repeated or stale moves get 409; payment verification is refused (P8); another restaurant's order is not found. `db/test_schema.py`: amounts and snapshots can't change |
| P7-T4 Timers | Done | `orders/test_timers.py`: an unpaid transfer order expires exactly at its window (simulated clock), and the customer is told; a cash order never expires; the worker finds the tenant through `velora_dispatcher` and expires the order in its round. Nonce expiry: `test_confirmation.py` |
| P7-T5 Duplicate flag | Done | `orders/test_placement.py`: a second identical order within 10 minutes is flagged and **not** cancelled; one more than 10 minutes later is not flagged |
| P7-T6 Customer status notifications | Done | `orders/test_notifications.py`: **with every send failing, every transition still succeeds** and the order ends `completed`; opted-out customers get no status messages |
| P7-T7 Recovery hook | Done (P11 calls it) | `orders/test_recovery.py`: **pending actions created before an outage are expired in recovery**; the customer is asked to confirm again, and the old YES places nothing; a later summary is untouched; an already-expired one is voided silently |

**Verified in CI:** [run 39636189526](https://github.com/malikaliraza-2005/VELORA/actions/runs/39636189526) (PR #9). 702 tests passed against PostgreSQL 17, and the secret scan was clean.

**P7 exit criteria** (the provider-neutral ones):
- **Double YES:** met. Exactly one order in 100 of 100 runs.
- **Stale confirmation:** met. A price change or an unavailable item is rejected, and the customer confirms again.
- **Illegal transitions:** met. They are rejected by the engine and by the database guard.
- **Expiry:** met. An expired nonce can't create an order.
- **Recovery:** met for the hook. The full recovery-mode drill is P11.
- **Real-WhatsApp exit tests:** gated by the Go (V-01).

**Findings for later phases:** see AD-31. In short:
- **P8:** verification uses `lifecycle.transition`; send the account details after a transfer order; late payments after expiry; `refund_pending` → `cancelled`, or a `refunded` state.
- **P9:** show `possible_duplicate`, the allowed next moves and the history.
- **P11:** call `expire_pre_outage` and extend `payment_due_at` by the outage length; retention must cover `pending_actions.payload`.

## P8 task evidence (2026-10-21)

Only the provider-neutral track that AD-25 allows was built. Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-32, for owner review.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P8-T1 Payments ledger | Done | `db/test_payments_ledger.py`: **UPDATE and DELETE on ledger rows are refused for `app_rw`**, and by a trigger for everyone; a correction is a new `reversed` row, once per entry, naming what it corrects and why. `db/test_isolation.py` lists `payments` and `payment_evidence` as append-only and tenant-isolated |
| P8-T2 COD controls | Done | `payments/test_customer_side.py`: **a first order over the limit is refused with a clear message** (when COD is offered, and again at YES); **a blocklisted customer can't choose COD** (and a block set after the summary is refused at YES); a customer is on their first order until one is completed. `api/test_payments.py`: managers block and unblock, the kitchen can't, both audited |
| P8-T3 Transfer instructions and screenshots | Done (media fetching is P5-T2) | `payments/test_customer_side.py`: the confirmation and "status" carry the account details; without them transfer isn't offered; **a screenshot alone never changes payment status** (orders, ledger and history unchanged; the evidence is kept for staff); **"I have paid" never changes payment status**, typed or read by the AI; evidence is kept silently during a handoff |
| P8-T4 Staff verification | Done | `api/test_payments.py`: reference + PIN sets `paid_staff_verified`, writes the ledger and audits the confirmer; **a confirmation without a PIN (or with a wrong one), or from another tenant, is rejected**; a kitchen device names a person; cash orders and the generic move can't be marked paid; **a duplicate reference is flagged** (409 naming the other order, audited), per tenant; two simultaneous confirmations with one reference verify one order |
| P8-T5 Expiry and late payments | Done | `payments/test_customer_side.py`: **an expired transfer moves to `expired`, not paid** (no ledger entry), and late evidence still reaches staff for 24 hours. `api/test_payments.py`: **the late-payment review offers only "accept" or "refund"** (anything else is 422; ordinary verification and generic moves are refused); accept verifies then accepts; refund records the payment and marks `refund_pending` |
| P8-T6 Reconciliation and reversal | Done | `api/test_payments.py`: **the report lists every staff-verified transfer of the day** (by Pakistan date, with confirmer, late flag and totals); **only the owner sees it or reverses**; a reversal needs the owner's PIN and a reason, is audited, happens once, returns a still-unaccepted order to `awaiting_transfer` and frees the reference; after acceptance the order keeps its status |
| P8-T7 Paid-writer guard | Done | `test_paid_writers.py`: **only reviewed code names `paid_staff_verified`, writes the ledger or writes an order's status**, and every paid move checks a PIN first; a self-test proves a planted writer is found. `db/test_payments_ledger.py`: **the database refuses `paid_staff_verified` without a verified, unreversed ledger entry**, even for a direct UPDATE |

**Verified in CI:** [run 39641355254](https://github.com/malikaliraza-2005/VELORA/actions/runs/39641355254) (PR #10). 761 tests passed against PostgreSQL 17, and the secret scan was clean.

**P8 exit criteria** (all provider-neutral):
- **Screenshot alone never changes status:** met.
- **"I have paid" never changes status:** met.
- **A confirmation without a PIN, or from another tenant, is rejected:** met.
- **A duplicate reference is flagged:** met.
- **An expired transfer becomes `expired`, not paid:** met.
- **Real-WhatsApp exit tests:** gated by the Go (V-01).

**Findings for later phases:** see AD-32. In short:
- **P9:** the payment panel (verify, confirmer picker on the tablet, late review, evidence, the owner's report and reversal) and a "late payments" view, because expired orders aren't in the active list.
- **P5-T2:** fetching evidence media for staff.
- **P11:** retention must cover `payment_evidence`.
- **P13:** each restaurant enters its account details before transfer is offered.

## P9 task evidence (2026-10-22)

Only the provider-neutral track that AD-25 allows was built. Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-33, for owner review. Browser tests drive headless Chromium against the real app and database, with the `FakeProvider` as WhatsApp.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P9-T1 PWA shell, login, role-scoped views | Done | `web/test_acceptance.py`: **the kitchen-device role can't open settings** (no menu entry, a refusal screen, and 403 from the API). `web/test_dashboard.py`: **browser storage holds no tokens** (local and session storage and IndexedDB empty; the service-worker cache holds no API answer and no login token; the session cookie is HttpOnly, Secure and SameSite=Strict; the token is gone from the address bar); every response carries the CSP (Trusted Types required); every screen opens for the owner without a script error. `api/test_dashboard.py`: the page has no inline script, and the code never writes HTML strings, evaluates code or uses browser storage |
| P9-T2 Order board | Done | `web/test_acceptance.py`: **a double tap makes one transition**; the same request sent twice with one key is replayed, not repeated; new orders raise the alert banner. `api/test_dashboard.py`: an action without a key is refused (428); a repeat replays the first status (one move); a key reused for another request is refused (422); a failed action frees its key; keys are per session, refused while in progress, taken over once abandoned, and dropped after 24 hours |
| P9-T3 Payment received | Done | `web/test_acceptance.py`: **a missing reference or PIN is refused** in the panel before anything is sent; a wrong PIN is refused by the server and records nothing; the right one marks the order paid, with its ledger entry; **on the kitchen tablet the confirmer must be named**, and the ledger records that person. `api/test_dashboard.py`: the confirmer list and the late-payments list. The P8 API tests still pass |
| P9-T4 Chats and handoff inbox | Done (staging with a test number gated) | `web/test_acceptance.py`: **a staff reply reaches the (simulated) customer through the outbox**, and **Resume restores the bot** (the cart is kept). `api/test_dashboard.py`: replies go only to handed-over chats of customers who haven't opted out; audited with the length, not the text; kitchen roles refused; failed and unknown messages listed |
| P9-T5 Menu, controls, summary, staff | Done | `api/test_dashboard.py`: **every API path the dashboard calls exists** (the same endpoints, so the same permissions, as P3 and P4); the today summary's counts, and kitchen roles refused. The P3 and P4 API tests still pass |
| P9-T6 WhatsApp status and QR re-link | Done (staging with a test number gated) | `web/test_acceptance.py`: **QR re-link restores the channel and clears the banner** (needs_qr, re-link, the QR image, the phone "scans", the health poll, connected); a manager can't see channels or the QR. `api/test_dashboard.py`: re-link is audited and recorded; the QR is an SVG sent `no-store`, and is never in the audit or event records; a connected channel, or one whose adapter isn't running, is not re-linked |
| P9-T7 Live updates and offline awareness | Done | `web/test_acceptance.py`: **with the network cut, an action is refused visibly** ("Connection lost — this was not sent") and **nothing is queued**: after reconnecting, the order is unchanged |
| P9-T8 End-to-end and usability | **Gated** | The staging run (order, accept, preparing, done, with WhatsApp updates to a test number) needs the Go (V-01) and staging (P12). The usability check with kitchen staff is part of P13 |

**Verified in CI:** [run 39775329133](https://github.com/malikaliraza-2005/VELORA/actions/runs/39775329133) (PR #12). 810 tests passed against PostgreSQL 17, including the browser tests in headless Chromium, and the secret scan was clean.

**P9 exit criteria:**
- **A double tap creates no duplicate transition:** met.
- **The kitchen-device role can't open settings:** met.
- **QR re-link restores the channel and clears the banner:** met against the `FakeProvider`. The run with a test number is gated by the Go.
- **End-to-end in staging, with matching WhatsApp updates:** gated by the Go (V-01) and P12.
- **Usability with a real kitchen staff member:** P13.

**Findings for later phases:** see AD-33. In short:
- **P5-T2:** evidence images for staff, and the OpenWA adapter's QR code.
- **P10:** alert on failed and unknown messages.
- **P12:** serve `velora.asgi:app` behind TLS with HSTS, and trust proxy headers.
- **P13:** the usability check and the staging end-to-end run.

## P10 task evidence (2026-10-22)

Only the provider-neutral part was built (see AD-34 on the scope). Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-34, for owner review. The monitor's database tests use a database of their own, so its cross-tenant counts are exact.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P10-T1 Structured logging with redaction | Done | `test_structured_logs.py`: **the log redaction test finds no secrets**: fake secrets seeded into requests (bearer token, session cookie, CSRF token, query, JSON body, phone number), a webhook that fails verification, and an exception with its traceback, logged carelessly on purpose; none appear, and every line is JSON. Correlation ids are echoed in `X-Request-ID` and appear on the request's log lines; tenant and correlation ids nest and restore. The P5 redaction tests still pass |
| P10-T2 Metrics | Done | `monitoring/test_metrics.py`: **each metric changes when its condition is simulated**: inbox and outbox queue age, dead letters (inbox and outbox), messages held for a down channel, channel state changes and time in `needs_qr`, refused webhooks by reason, backup runs, the order funnel, the global pause. `monitoring/test_monitor_role.py`: what `velora_monitor` may and may not read (no payload, message, address, amount, reference or customer), that it changes nothing else, and that the runtime can't see its records |
| P10-T3 Alerts and external uptime check | Done in code; **real delivery gated** | `monitoring/test_drills.py`: **all four drills produce an alert** through the real monitor loop and database (the API stops answering, a session is unlinked, the dead-letter queue fills, a backup fails); each is sent once, recorded in `alert_events`, and cleared; a restarted monitor doesn't repeat it; an unreadable database is itself an alert. `monitoring/test_alerts.py`: every rule; sent once, reminded after 4 hours, re-sent when it grows, retried when undelivered, resolved; email and webhook notifiers (also over real HTTP); the heartbeat and readiness probe; settings refuse a deployment with no way to reach the founder, or without https. **Delivery to the founder's real phone or email needs the free mailbox and heartbeat set up (AD-35)** and is checked in staging with `python -m velora.monitoring test-alert` |
| P10-T4 Founder dashboard and daily pilot metrics | Done (staging run gated by P12) | `monitoring/test_metrics.py`: the daily metrics per restaurant (orders, completed, completed without a human, cancelled, handoffs, disconnects, minutes needing a QR scan, payment verifications, dead letters); each handoff is recorded once. The founder's dashboard is `python -m velora.monitoring status` and `daily`, checked by hand on a scratch database. **Produced daily in staging:** P12 |

**Verified in CI:** [run 39808148631](https://github.com/malikaliraza-2005/VELORA/actions/runs/39808148631) (PR #15). 929 tests passed against PostgreSQL 17, including the browser tests, and the secret scan was clean.

**P10 exit criteria:**
- **Each alert fires in a drill (kill the ingress, disconnect a session, fill the DLQ, fail a backup):** met in automated drills against the `FakeProvider`, with a recording notifier. The same drills with real (free) alert delivery are part of staging (P12).
- **The log redaction test finds no secrets:** met.

**Findings for later phases:** see AD-34. In short:
- **P5-T3:** the webhook endpoint must call `Ingress.reject` for each refused webhook.
- **P11:** the backup job reports each run with `python -m velora.monitoring record-backup`.
- **P12:** run the monitor as its own container; set the probe URL, the heartbeat, an external `/readyz` check and the free alert mailbox (AD-35).

## P11 task evidence (2026-10-23)

Only what works without the OpenWA Go was built (see AD-36 on the scope). Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-36, for owner review. The backup tests use a database of their own; the dump tools run as the new `velora_backup` role.

| Task | Status | Evidence (tests in `backend/tests/`) |
|---|---|---|
| P11-T1 Database backups | Done in code; **the 24-hour staging run is gated by P12** | `backup/test_backup_restore.py`: a backup is three files (roles, database, manifest); **each is confirmed encrypted** (no dump header, role script, tenant name or phone number in the files) and nothing is left in the work directory; a failed dump leaves no complete backup; **each run is reported to the monitor** (`backup_runs`, ok and failed, without the password); the backup role reads every tenant and writes nothing. `backup/test_crypto.py`: round trips; a changed byte, a dropped chunk, reordered chunks and the wrong key are refused. `backup/test_storage.py`: the S3 signature matches AWS's published example; put, get and paged listing against a local S3 stand-in; settings |
| P11-T2 OpenWA session backup policy | Done (the PC-8 default) | `backup/test_backup_restore.py`: **the backup's contents are exactly the database's three files**; OpenWA's session volume is never read. Policy in the runbooks (R5) |
| P11-T3 Restore script and restore drill | Done in CI; **the staging drill is gated by P12** | `backup/test_backup_restore.py::test_restore_drill`: restored to a fresh database, **all six checks pass** (roles, schema version, every row count, FORCE RLS and policies, database grants, **tenant isolation as `app_rw` across every tenant table and tenant**); roles, grants and policies equal the original's; RTO and RPO reported (well under 4 h and 1 h); **a test order succeeds on the restored copy**. A changed file, the wrong key and an existing database are refused; an incomplete backup is never chosen; verification catches a copy without FORCE RLS, without a policy, or with rows missing |
| P11-T4 Recovery mode on startup | Done (step 3, inbound reconciliation, waits for V-04: a hook, recorded as skipped) | `orders/test_recovery_mode.py`, **one test per step**: (1) a database that doesn't answer, or isn't migrated, stops the start; one worker at a time; a first start voids nothing; (2) an event a dead worker was processing is processed once; (3) the hook is skipped, or called with the outage; (4) **a YES on a summary from before the outage places nothing**, with one apology; (5) transfer windows open at the outage are extended by its length, and one already closed expires; (6) **a send in flight becomes unknown and is never re-sent**; (7) stale replies are replaced by one message per customer; (8) recovery sends nothing, and the flush keeps the rate cap; (9) **orders placed from the backlog are highlighted** until it clears. An interrupted recovery resumes without extending a window twice. Each step's test fails when that step is removed (checked by hand for steps 4, 5 and 6) |
| P11-T5 Recovery-mode drill | Done in CI against the `FakeProvider`; **the staging drill is gated by P12** | `orders/test_recovery_mode.py::test_recovery_drill_after_a_one_hour_outage`: the real worker, a crash mid-send, a simulated 1-hour outage with messages arriving meanwhile, then a restart: **no duplicate orders, no stale YES accepted, every outbox message resolved** (sent, unknown or stale), every inbound message processed, the transfer window extended, the backlog order highlighted, at most 20 sends a minute |
| P11-T6 Runbooks and manual fallback | Written; **walk-throughs gated by P12** | [`../operations/runbooks.md`](../operations/runbooks.md): R1 session down / QR, R2 engine broken for all, R3 number restricted, R4 database restore (with backup setup), R5 server lost, R6 payment dispute; the manual fallback. The walk-through table lists the two automated drills; no staging walk-through yet |

**Verified in CI:** [run 39921013960](https://github.com/malikaliraza-2005/VELORA/actions/runs/39921013960) (PR #17). 981 tests passed against PostgreSQL 17, including the browser tests, the restore drill and the recovery drill, and the secret scan was clean.

**P11 exit criteria:**
- **Restore drill:**
  - restored to a fresh instance within 4 hours (RTO): **met in CI** (seconds, on a small database). The staging drill with realistic data is P12.
  - data loss of 1 hour or less (RPO): **met by design** (hourly backups; each restore reports the data age). The 24-hour staging run proves the schedule (P12).
  - roles, FORCE RLS and policies present: **met.**
  - cross-tenant tests pass after the restore: **met** (the isolation check over every tenant table and tenant, as `app_rw`).
  - a test order succeeds: **met** on the restored copy, against the `FakeProvider`.
- **Recovery-mode drill** after a simulated 1-hour outage (no duplicate orders, no stale YES accepted, every outbox message resolved): **met in CI** against the `FakeProvider`. The staging run is P12.

**Findings for later phases:** see AD-36. In short:
- **P12:**
  - an hourly timer for `python -m velora.backup run`;
  - the bucket, with a lifecycle rule and a key that can't delete;
  - PostgreSQL 17 client tools;
  - the `velora_backup` password;
  - `VELORA_MONITOR_EXPECT_BACKUPS=true`;
  - a supervisor that restarts the worker;
  - the staging drills and runbook walk-throughs.
- **V-04:** the inbound reconciliation hook.
- **P10 (optional):** a "worker heartbeat stale" alert.
- **P13:** data retention is still to do; backups keep what the database holds.

## P12 task evidence (2026-10-23)

Only what works without a server or the OpenWA Go was built (see AD-37 on the scope). Implementation choices are recorded as **ADOPTED (delegated)** in decision AD-37, for owner review. **No server exists yet**, so nothing below ran on one: the CI rehearsal runs the real stack on a CI machine, with a private certificate authority and a stand-in for the founder's webhook.

| Task | Status | Evidence |
|---|---|---|
| P12-T1 Provision and harden the VPS | Script and scan tool done; **the VM and its external scan wait for V-03, B5** | `infra/host/install.sh`: SSH keys only, ufw with 443 and SSH from listed addresses (refuses to lock out the current session), automatic security updates, Docker from Ubuntu's packages, swap. `tests/deploy/test_scan.py`: the scan finds every open port, and fails on an unexpected open port and on a closed expected one. `tests/deploy/test_compose_policy.py`: the host script's key settings. **The scan of a real server:** to do |
| P12-T2 Container topology | Done; **OpenWA waits for the Go** | `tests/deploy/test_compose_policy.py`, on the configuration compose runs: **only Caddy publishes a port, only 443**; the database is on an internal network only; OpenWA (overlay) has no port and is reachable only from the API and worker; every container is read-only, drops all capabilities (two written exceptions) and has `no-new-privileges`; images pinned by digest; each container gets only its own settings file. Rehearsal: the published-port check and a scan of the stack's ports (only 443 open) |
| P12-T3 TLS and secrets | Done in the rehearsal; **a public certificate needs the hostname (B12)** | Rehearsal: the TLS chain verifies, TLS 1.2+ and HTTP/2, HSTS and CSP sent, no `Server` or `Via` header; settings files 0600 in a 0700 directory, and `env-check` refuses a world-readable one; **none of the 8 database passwords appears in any container log**; the login rate limit holds through the proxy against spoofed `X-Forwarded-For`. `tests/deploy/test_envfiles.py`: fresh passwords per role; each container's file holds only its own secrets; every inconsistency is reported. `tests/deploy/test_serve.py`: proxy headers trusted from the named proxy only; `*` refused. `tests/db/test_provision.py`: roles, passwords from the settings and the database, idempotent; mismatched settings refused. The P10 redaction tests still pass |
| P12-T4 Staging environment | Tooling done; **no staging yet (VM, hostname; test numbers need the Go)** | `tests/deploy/test_staging_guard.py`: in staging, a restaurant slug without `test-` is refused. Rehearsal: the same through `velora admin` |
| P12-T5 Release and rollback procedure | Done in the rehearsal; **a staging run waits for staging** | Rehearsal: a second release (backup first, migrations, switch), **a rollback to the first release** (image and files), and a roll forward. The checklist is in the [deployment guide](../operations/deployment.md) |
| P12-T6 From-scratch deployment | **Waits for a VM** | The guide's "From scratch" has a timing table. The rehearsal does steps 4–7 from nothing (build, settings, first release) |
| P12-T7 Failure drills | Two of four rehearsed in CI, plus a worker crash; **OpenWA drills wait for the Go; staging runs for a server** | Rehearsal: **API down: the `api_down` alert reached the HTTPS webhook stand-in, then cleared**; **database restore: a backup, a verified restore, and the copy put into service (runbook R4)**; worker killed with SIGKILL: Docker restarted it and recovery mode ran |

**Verified in CI:** [run 39941118477](https://github.com/malikaliraza-2005/VELORA/actions/runs/39941118477) (PR #18). 1027 tests passed against PostgreSQL 17, including the browser tests; the secret scan was clean; and the "Deployment rehearsal" job passed every check. From its drill record (the first CI run, 39940522360, gave the same results):
- first release from nothing, including the image build: **41 s** to a ready dashboard over HTTPS;
- only Caddy publishes a port (443); the port scan of the host found only 443 open;
- TLS 1.3 and HTTP/2, the chain verifies, HSTS and CSP sent, no `Server` or `Via`;
- settings 0600 in a 0700 directory; **none of the 8 passwords in any container log**;
- 11 logins with 11 spoofed `X-Forwarded-For` addresses: `401` ten times, then `429`;
- worker killed with SIGKILL: restarted, recovery mode ran, within 4 s;
- **API down: the `api_down` alert reached the webhook after 46 s and cleared 59 s after the API came back**;
- backup and verified restore (6 checks OK) in 4 s; the R4 swap put the copy into service in 11 s, with the test restaurant intact;
- release B (backup first) in 24 s; **rollback to A in 17 s**; rollback again returned to B.

**P12 exit criteria:**
- **A full deployment from scratch takes 1 hour or less, following the document:** not yet: no VM. Rehearsed in CI.
- **Rollback is demonstrated:** in the CI rehearsal. Staging: to do.
- **An external port scan shows only the expected ports:** the tool is done and tested; the stack publishes only 443. The scan of a real server: to do.
- **All failure drills pass with the alerts firing:** API down and the restore pass in the rehearsal, with the alert received. The two OpenWA drills are gated by the Go; staging runs need a server.

**Findings for later phases:** see AD-37. In short:
- **P5-T2:** pin the OpenWA image, harden its container, add its drills.
- **P13:** data retention; the usability check and the staging end-to-end run.
- **B12:** once chosen, the hostname should not change.

## Resuming the live test

The owner provides:
1. One prepaid Pakistani SIM, registered on WhatsApp Business (dual-SIM on the owner's phone is acceptable).
2. A machine: a Windows computer for short sessions, and either that computer left on or a small VPS for the 14-day soak.
3. The owner's own WhatsApp as the test customer; volunteers optional.

The reduced variant and the trial tooling are adopted (AD-39), so only item 1 is missing. With the SIM, follow the validation plan's appendix and [`../../trial/README.md`](../../trial/README.md).

## Open blockers

| # | Blocker | Blocks | Owner of the action |
|---|---|---|---|
| B1 | Live OpenWA validation (G1–G5) not run; paused for resources | The OpenWA part of P5, real WhatsApp traffic, P10–P13 | Founder (resources) + engineering |
| B2 | ~~Legal opinion on WhatsApp Terms-of-Service exposure and consent wording~~ **Closed:** the owner withdrew AD-08 and accepts the risk (AD-35) | — | — |
| B3 | Tax by payment method not confirmed. No accountant (AD-35): the owner sets each restaurant's rate from public sources | Correct totals (P4, P13) | Founder |
| B4 | Merchant acceptance of a separate number and kitchen device not confirmed | P13 | Founder (interviews) |
| B5 | Free-tier terms not verified (Oracle Always Free: region, capacity, commercial use, idle reclamation, arm64 (the image supports it), how many VMs (staging and production, AD-37); free backup storage, B2 or R2, both supported by the P11 code; free heartbeat/uptime; Groq free limits). Zero cost (AD-35) | Hosting (P12), backups in staging (P11-T1) | Engineering |
| B6 | AI model not selected (no real dataset). Groq gpt-oss-20b is the built adapter (AD-30), but the selection needs the P0-F6 evaluation | Pilot use of AI | Engineering |
| B7 | ~~Delivery channel for staff magic links undecided~~ **Resolved for the pilot:** manual delivery (AD-27, ADOPTED (delegated)). Email is a later option | — | Engineering |
| B8 | Owner authorisation for P13 (P1 to P8 authorised 2026-10-21, P9 and P10 on 2026-10-22, P11 and P12 on 2026-10-23; P5 to P12 for the parts that work without the Go) | P13 | Owner |
| B12 | No hostname for the dashboard yet. Zero cost (AD-35): a free subdomain service, or a domain the owner already has. It should not change once restaurants install the dashboard (AD-37) | TLS and staging (P12-T3, T4) | Owner |
| B11 | Free alert mailbox and heartbeat not yet set up (AD-35; PC-11 rejected) | Real alert delivery to the founder (P10-T3 in staging) | Founder + engineering (P12) |
| B10 | ~~Groq's free tier vs AD-04~~ **Decided:** Groq's free tier is used (AD-35, owner accepts unverified training terms) | — | — |
| B9 | ~~Reduced trial variant and trial tooling (P0-E1) not approved~~ **Resolved:** ADOPTED (delegated), AD-39; the kit is built. Only the test SIM is missing (P0-F1) | — | — |

## Decisions awaiting the owner

- Review the **ADOPTED (delegated)** decisions AD-11 to AD-24, AD-26 to AD-34 and AD-36 to AD-39 ([decisions §8](../architecture/architecture-decisions.md#8-items-for-owner-review)).
- Approve or reject the **reduced live-trial variant** ([validation plan §9](openwa-validation-plan.md)).
- **Go/No-Go and V-01 to V-10:** after the live trial.
- **Infrastructure cost:** $0 a month by owner decision (AD-35); only free-tier terms remain to verify (B5).
- **Optional customer order-tracking link:** not approved.
- Proposed changes arising from the desk review (see validation plan §10 and decisions §9).

## Change log

| Date | Change |
|---|---|
| 2026-10-23 | Owner authorised **P11** ("Start P11"). Built: the `velora_backup` role and migration 0013, encrypted backups (roles, database and a manifest from one snapshot) to a directory or an S3-compatible bucket, restore into a new database with six checks, recovery mode with all nine steps on every worker start (inbound reconciliation a hook until V-04), the dashboard's "placed after an outage" flag, and the runbooks in `docs/operations/` (PR #17, CI run 39921013960; 981 tests). New dependency: `cryptography`. Decision AD-36 recorded as ADOPTED (delegated). |
| 2026-10-23 | Owner authorised **P12** ("Start P12"). Built: the application image, the compose stack (Caddy with HTTPS and HSTS, API, worker, monitor, PostgreSQL; only 443 published; the OpenWA overlay unused until the Go), owner-only settings files (`python -m velora.deploy env-init`/`env-check`), `python -m velora.db provision`, the host-hardening script, the `velora` release/rollback tool, the hourly backup timer, the external port scan, the staging guard, restores reading the key from standard input, and the CI "Deployment rehearsal" job (PR #18, CI run 39941118477; 1027 tests). No server yet (V-03, B5, B12). `uvicorn` moves to the runtime. Decision AD-37 recorded as ADOPTED (delegated). |
| 2026-10-24 | Owner: "Get everything you can do completed with your best possible decisions." Built: data retention (`velora.retention`: hourly sweep and `forget`), the `worker_down` alert (migration 0014), the restaurant consent form draft and the owner-verification runbook (AD-38); the reduced trial variant adopted and the OpenWA trial kit in `trial/` (AD-39). Both ADOPTED (delegated). PR #19, CI run 40103170326 (1045 tests). |
| 2026-10-22 | Owner decision **AD-35: zero cost.** A free VM with PostgreSQL on it and our own off-site backups (amends AD-04); Groq's free tier for the AI (settles B10); free monitoring (PC-11 rejected); **AD-08 withdrawn**: no lawyer or accountant, risks accepted (B2 closed; the owner sets tax rates, B3). |
| 2026-10-22 | Owner authorised **P10** ("Start P10"). Built the provider-neutral part: JSON logs with tenant and correlation ids (cookies now redacted too), `/readyz`, the `velora_monitor` role and migration 0012, metrics, alert rules with email and webhook delivery and a heartbeat, the four drills, and the founder's `status` and `daily` commands (PR #15, CI run 39808148631; 929 tests). Decision AD-34 recorded as ADOPTED (delegated). No alert vendor chosen (B11). |
| 2026-10-22 | Added a local testing tool (`python -m velora.dev`: demo restaurant, dashboard, simulated WhatsApp customer) and the guide [`local-testing-guide.md`](local-testing-guide.md). The Menu screen gained draft review, CSV import, publishing and rollback, which a fresh restaurant needs before it can take orders. Checked by hand end to end on a fresh install: order on simulated WhatsApp → dashboard → accept → the customer's status messages. |
| 2026-10-22 | Owner authorised **P9** ("Start P9"). Built the provider-neutral part: the staff dashboard PWA in `web/`, idempotency keys on every action (migration 0011), the chat reply, channel and QR re-link, summary, late-payment and confirmer endpoints, security headers, and browser tests in headless Chromium (PR #12, CI run 39775329133; 810 tests). Decision AD-33 recorded as ADOPTED (delegated). |
| 2026-10-21 | Owner authorised **P6** ("Start P6") and asked to consider Groq as a free LLM. Built the provider-neutral part: engine, commands, renderer, AI proposals with a Groq adapter, cart and checkout, handoff, and the offline harness (PR #7, CI run 39613089570; migration 0008; 652 tests). Decision AD-30 recorded as ADOPTED (delegated). Groq's free tier for real customer messages conflicts with AD-04 and is left to the owner (B10). |
| 2026-10-21 | Owner authorised **P5** ("Start P5"). Built only the provider-neutral track (AD-25): T1, T7 and T8, plus the cores of T3, T4 and T5 (PR #6, CI run 39604566511; migration 0007; 580 tests). The OpenWA parts (T2, T6, the OpenWA endpoint and detection) stay blocked by V-01. Decision AD-29 (P5 choices, including the `velora_dispatcher` role that resolves the AD-26 finding) recorded as ADOPTED (delegated). |
| 2026-10-21 | Owner authorised **P4** ("Start P4"). P4 implemented and verified in CI (PR #5, run 39598901486; migration 0006; 491 tests). Decision AD-28 (P4 choices) recorded as ADOPTED (delegated). |
| 2026-10-21 | Owner authorised **P3** ("Start P3"). P3 implemented and verified in CI (PR #4, run 39594079256), and B7 resolved for the pilot (manual delivery). Decisions AD-26 (P2 choices) and AD-27 (P3 choices) recorded as ADOPTED (delegated). |
| 2026-10-21 | **P2 complete.** Owner authorised P2 ("Start P2"); merged in PR #3, CI green (runs 39590467933 and 39590889951). |
| 2026-10-21 | **P1 complete.** The PR #2 CI self-test proved fake-key detection (run 39587837550). |
| 2026-10-21 | PR #1 merged; CI green on `main` (run 39587396126). At the owner's request, the commit history on `main` was rewritten to remove AI co-author trailers (contents unchanged; force-pushed with lease; local backup tag kept). New SHAs: `cd57482`, `24ee91b`, `4e40fae`, `d4e134a`. Self-test branch for fake-key detection opened. |
| 2026-10-21 | Owner pushed `main`. CI backend checks passed; the secret-scan job failed (tool error). Fix on branch `fix/ci-secret-scan`. Owner asked to work in branches from now on. |
| 2026-10-21 | P1 implemented and committed locally (not pushed). Local checks pass. The CI run and secret-scanner blocking are **unverified** (see P1 task evidence). |
| 2026-10-21 | Owner authorised **P1** and chose **Option 2** (provider-neutral build track, recorded as AD-25). GitHub repository provided; commit locally, **do not push**. |
| 2026-10-21 | Implementation plan restructured: master roadmap plus 14 phase files in `phases/`. Phase rows now link to their files. **No status, decision or requirement changed.** |
| 2026-10-21 | Desk tasks completed: OpenWA desk review (validation plan §10), vendor price sheet (decisions §6), sample conversations and interview questions (MVP scope Appendices A–B), beginner setup guide (validation plan Appendix), reduced trial variant (§9, proposed). Proposals PC-1 to PC-13 added to decisions §9 for review. No decisions changed; no gate marked passed. |
| 2026-10-21 | Owner chose: pause the live test, continue free desk work. Status split into desk research (in progress), live validation (paused), P1–P13 (not started), and P5+ (blocked). Reduced trial variant proposed. |
| 2026-10-20 | Owner authorised **P0 only**. P0 task breakdown added. |
| 2026-10-20 | Research moved to `docs/research/`, verified unchanged by SHA-256. Created README, architecture decisions, implementation plan, MVP scope, OpenWA validation plan and this status file. |
