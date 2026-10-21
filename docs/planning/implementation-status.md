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
| **P1** | **Complete (2026-10-21).** Both exit criteria verified in CI. |
| **P2** | **Complete (2026-10-21).** Authorised by the owner ("Start P2"). Merged in PR #3; CI green. |
| **P3** | **Complete (2026-10-21).** Authorised by the owner ("Start P3"). PR #4; all five exit criteria verified in CI. See "P3 task evidence". |
| **P4** | **Complete (2026-10-21).** Authorised by the owner ("Start P4"). PR #5; all four exit criteria verified in CI. See "P4 task evidence". |
| **P5** | **Provider-neutral part complete (2026-10-21).** Authorised by the owner ("Start P5"). Built under AD-25 against the `FakeProvider`. **The OpenWA part (T2, the OpenWA parts of T3 and T5, and T6) is not built and stays blocked** until the Go (V-01). See "P5 task evidence". |
| **P6** | **Provider-neutral part built (2026-10-21).** Authorised by the owner ("Start P6"). Built under AD-25 against the `FakeProvider`, with Groq as the first AI adapter (AD-30). Order creation on YES is P7. **Real customer text must not go to Groq** until the owner decides on AD-04 (see AD-30). See "P6 task evidence". |
| **P7–P13** | **Not started. Not authorised.** |
| **Provider-neutral track (AD-25)** | Owner decision 2026-10-21: the provider-neutral parts of P5, and P6–P9, may be built against the `FakeProvider` before the OpenWA Go, each once it is authorised. |
| **OpenWA adapter, real WhatsApp traffic, P10–P13** | **Blocked** until the live validation is complete and the owner gives a Go (V-01). |
| **Application code** | P1 skeleton, the P2 database layer, P3 staff identity (login, sessions, roles, PINs, audit, founder onboarding tool), P4 menu and ordering controls (versioned catalogue, CSV import, publish and rollback, availability, pause, capacity, settings, server-side pricing), and the P5 provider-neutral messaging core (provider interface, FakeProvider, durable inbox and outbox, channel state machine, circuit breaker, global pause, worker loop, log redaction). P6 conversations (engine, commands, numbered menus, AI proposals via Groq, cart, checkout, templated summary, handoff). No real WhatsApp connection and no order creation. |
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
| [P1 Repository foundation](phases/p01-repository-foundation.md) | **Complete** (2026-10-21) | — | CI runs 39587396126 (main) and 39587837550 (PR #2 self-test); see "P1 task evidence" | Owner: apply the branch protection rule (see README) |
| [P2 Database and tenant isolation](phases/p02-database-and-tenant-isolation.md) | **Complete** (2026-10-21) | — | PR #3; CI runs 39590467933 (PR) and 39590889951 (main); 187 tests | — |
| [P3 Onboarding and staff identity](phases/p03-onboarding-and-staff-identity.md) | **Complete** (2026-10-21) | — (B7 resolved for the pilot: manual delivery, AD-27) | PR #4; CI run 39594079256 (315 tests); see "P3 task evidence" | Owner: review AD-27 |
| [P4 Menu, availability and settings](phases/p04-menu-availability-and-settings.md) | **Complete** (2026-10-21) | — (tax values wait for the accountant, B3; quotes are refused until set) | PR #5; CI run 39598901486 (491 tests); see "P4 task evidence" | Owner: review AD-28; authorise the next phase when ready |
| [P5 Provider abstraction and OpenWA adapter](phases/p05-whatsapp-provider-and-openwa-adapter.md) | **Provider-neutral part complete** (2026-10-21). **OpenWA part blocked** | OpenWA part: **live validation + Go (V-01)**; V-02, V-04, V-05, V-06, V-07, V-08 | PR #6; CI run 39604566511 (580 tests). See "P5 task evidence" | Owner: review AD-29 |
| [P6 Conversation workflows](phases/p06-conversation-workflows.md) | **Provider-neutral part built** (2026-10-21) | Real-WhatsApp exit tests: Go (V-01). Split limit provisional: V-09. AI accuracy on real data: P0-F6. Groq for real customers: owner decision (AD-30) | Migration 0008; 72 new tests (652 in total). See "P6 task evidence" | Owner: review AD-30 and decide on Groq vs AD-04; confirm the 90% target; authorise P7 when ready |
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

**P6 exit criteria:**
- **Deterministic:** met. An unavailable item is never added; prices always equal the database values; AI output is never sent verbatim as a total.
- **Injection:** met for the suite above.
- **Concurrency:** met.
- **AI accuracy ≥90% (offline, not a CI gate):** **not measured.** It needs the real P0-F6 dataset and a model run, and the target is still an estimate.
- **Real-WhatsApp exit tests:** gated by the Go (V-01).

**Findings for later phases:** see AD-30. In short:
- **P5-T2:** location coordinates.
- **P7:** order creation on YES against `context.quote`, CANCEL of placed orders, and whether status updates go to opted-out customers.
- **P9:** the handoff inbox and the `ai_enabled` switch.
- **P11:** `conversations.context` holds addresses.

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
| B1 | Live OpenWA validation (G1–G5) not run; paused for resources | The OpenWA part of P5, real WhatsApp traffic, P10–P13 | Founder (resources) + engineering |
| B2 | Legal opinion on WhatsApp Terms-of-Service exposure and consent wording; deferred for budget | Connecting any real merchant (P13) | Founder + lawyer |
| B3 | Tax by payment method not confirmed | Correct totals (P4, P13) | Founder + accountant |
| B4 | Merchant acceptance of a separate number and kitchen device not confirmed | P13 | Founder (interviews) |
| B5 | Vendor prices not fully verified (see price sheet) | Infrastructure commitment (P12) | Engineering |
| B6 | AI model not selected (no real dataset). Groq gpt-oss-20b is the built adapter (AD-30), but the selection needs the P0-F6 evaluation | Pilot use of AI | Engineering |
| B7 | ~~Delivery channel for staff magic links undecided~~ **Resolved for the pilot:** manual delivery (AD-27, ADOPTED (delegated)). Email is a later option | — | Engineering |
| B8 | Owner authorisation for P7–P13 (P1 to P6 authorised 2026-10-21; P5 and P6 for their provider-neutral parts only) | P7 onwards | Owner |
| B10 | Groq's free tier vs AD-04 (paid tier, no training on our data). Groq's training terms are unverified | Sending real customer messages to the AI | Owner |
| B9 | Reduced trial variant and trial tooling (P0-E1) not approved | Resuming the live test | Owner |

## Decisions awaiting the owner

- Review the **ADOPTED (delegated)** decisions AD-11 to AD-24 and AD-26 to AD-30 ([decisions §8](../architecture/architecture-decisions.md#8-items-for-owner-review)).
- Approve or reject the **reduced live-trial variant** ([validation plan §9](openwa-validation-plan.md)).
- **Go/No-Go and V-01 to V-10:** after the live trial.
- **Infrastructure cost commitment:** after vendor checks.
- **Optional customer order-tracking link:** not approved.
- Proposed changes arising from the desk review (see validation plan §10 and decisions §9).

## Change log

| Date | Change |
|---|---|
| 2026-10-21 | Owner authorised **P6** ("Start P6") and asked to consider Groq as a free LLM. Built the provider-neutral part: engine, commands, renderer, AI proposals with a Groq adapter, cart and checkout, handoff, and the offline harness (migration 0008; 652 tests). Decision AD-30 recorded as ADOPTED (delegated). Groq's free tier for real customer messages conflicts with AD-04 and is left to the owner (B10). |
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
