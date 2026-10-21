# P5: WhatsApp provider abstraction and OpenWA adapter

| Field | Value |
|---|---|
| **Authorisation** | **Authorised by the owner on 2026-10-21 ("Start P5").** The choices made are recorded in AD-29. The **provider-neutral tasks** (T1, the core of T3/T4/T5, T7, T8) were built before the Go under [AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits). The **OpenWA-specific tasks** (T2, the OpenWA parts of T3 and T5, and T6) stay **BLOCKED** until the gates below are met. |
| **Hard gates** | **the P0 Go (V-01)**. The engine (V-02) and recovery design (V-04) must already be decided, along with V-05, V-06, V-07 and V-08 for the tasks that use them. |
| **Depends on** | P0 Go, P2 |
| **Blocks** | P6, P10, P11 (recovery mode) |
| **Effort (estimate)** | 1.5–2 weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-05: OpenWA behind a replaceable `WhatsAppProvider`; G1–G5 mandatory; not claimed compliant | APPROVED |
| AD-06: Meta Cloud API, interface only, not implemented | APPROVED |
| AD-16: inbox and outbox | ADOPTED (delegated) |
| AD-18: separate ordering number | ADOPTED (delegated) |
| AD-19: one VPS; circuit breaker; global pause | ADOPTED (delegated) |
| AD-24: security baseline; **no detection-evasion features** | ADOPTED (delegated) |
| V-01, V-02, V-04, V-05, V-06, V-07, V-08 | VALIDATION-DEPENDENT |
| PC-1, PC-2, PC-3, PC-4, PC-5, PC-6, PC-7, PC-12 | **PROPOSED, awaiting review, not applied** ([decisions §9](../../architecture/architecture-decisions.md#9-proposed-changes-from-p0-desk-research-2026-10-21-for-owner-review)) |

Desk evidence on OpenWA's actual behaviour (retries, message store, signatures, idempotency keys) is in [validation plan §10](../openwa-validation-plan.md#10-desk-review-of-openwa-source-and-docs-p0-e2-2026-10-21). It is **not** a gate pass.

## Objective and scope
- **Objective:** reliable messaging that doesn't depend on any one provider.
- **Scope:**
  - **`WhatsAppProvider` interface:**
    - `send_text`, `send_media`, `get_session_status`, `start_session`/`get_qr`, `logout`;
    - `capabilities()`, which reports whether buttons and lists are supported.
  - **Canonical events:** `InboundMessage`, `DeliveryStatus` and `SessionState`.
  - **`FakeProvider`** for tests.
  - **`OpenWAAdapter`:**
    - verifies the per-session HMAC on the raw body;
    - checks that the session in the payload matches the webhook it arrived on;
    - maps payloads to canonical events;
    - manages sessions (QR, status, logout, force-restart);
    - uses one API key per session, stored encrypted.
  - **Ingress:** verify → insert into `inbox_events` (unique on the dedup key from V-05) → commit → **then** acknowledge. If anything fails, return non-2xx.
  - **Outbox sender:**
    - states: pending → sending → sent / delivered / read / failed / unknown;
    - an idempotency key per message;
    - backoff of 10 s, 30 s, 2 m, 5 m and 15 m;
    - while the channel is down, messages wait in `waiting_for_channel` and don't use up retries;
    - **staleness:** conversational replies older than about 30 minutes are replaced with a single "we're back" message; status updates are kept and sent in order;
    - **timeouts:** handled per V-06. Critical messages are never resent blindly.
    - Sending is rate-capped per channel.
  - **Channel state machine:**
    - states: connected / reconnecting / needs_qr / logged_out / failed / suspected_restricted;
    - detected from the session webhook, a 60-second health poll and send errors;
    - **a circuit breaker pauses new orders** for that tenant;
    - a global pause flag.
  - **Inbound recovery:** reconciliation job, according to the V-04 outcome.
  - **Message filtering:** group, status and broadcast events are ignored and never stored.
  - **Interface only:** a `MetaCloudAdapter` stub (AD-06).

## Phase security and reliability requirements
- OpenWA sits on the private network only.
- Secrets never appear in logs (log redaction tested).
- Session credentials stay on the encrypted volume (V-07).
- **No detection-evasion features.**

## Tasks

### P5-T1: Provider interface, canonical events and FakeProvider
- **Track:** provider-neutral ([AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits)).
- **Prerequisites:** P5 authorised; P2.
- **Deliverables:** the `WhatsAppProvider` interface; canonical events `InboundMessage`, `DeliveryStatus` and `SessionState`; `capabilities()`; a `FakeProvider`.
- **Tests:** contract tests run against the `FakeProvider`.
- **Acceptance:** business modules compile and test against the interface only.
- **Rollback:** reversible.

### P5-T2: OpenWA adapter
- **Track:** **OpenWA-gated.**
- **Prerequisites:** **V-01 Go; V-02 engine**; contract payloads recorded in P0.
- **Deliverables:** the `OpenWAAdapter`:
  - verifies the per-session HMAC on the raw body;
  - checks that the session in the payload matches the webhook it arrived on;
  - maps payloads to canonical events;
  - manages sessions (QR, status, logout, force-restart);
  - uses one API key per session, stored encrypted.
- **Security:** OpenWA sits on the private network only. API keys are encrypted at rest. Replay protection follows V-08 (PC-3 proposed).
- **Tests:**
  - **contract tests against real OpenWA payloads recorded in P0;**
  - **a bad signature is rejected (4xx) and never stored.**
- **Acceptance:** all contract and signature tests pass.
- **Rollback:** revert to the previous adapter or OpenWA version.

### P5-T3: Ingress endpoint and message filtering
- **Track:** the inbox insert, dedup and lease core is provider-neutral (built against the FakeProvider under AD-25). The OpenWA webhook endpoint is **OpenWA-gated**.
- **Prerequisites:** T2 (for the OpenWA endpoint); **V-05** (dedup key; PC-2 proposed). The core uses a configurable dedup key until V-05 is decided.
- **Deliverables:**
  - verify → insert into `inbox_events` (unique on the dedup key from V-05) → commit → **then** acknowledge; if anything fails, return non-2xx;
  - group, status and broadcast events are ignored and never stored.
- **Tests:**
  - **a duplicate webhook doesn't create a second event;**
  - a group or status event leaves no row;
  - **killing a worker in the middle of processing means the event is processed exactly once after its lease expires.**
- **Acceptance:** all three tests pass.

### P5-T4: Outbox sender
- **Track:** the outbox core is provider-neutral (AD-25). OpenWA-specific timeout handling waits for **V-06**.
- **Prerequisites:** T1 (core); T2 and **V-06** for the OpenWA timeout handling (PC-6 proposed).
- **Deliverables:**
  - states pending → sending → sent / delivered / read / failed / unknown;
  - an idempotency key per message;
  - backoff of 10 s, 30 s, 2 m, 5 m and 15 m;
  - `waiting_for_channel` with no retry use while the channel is down;
  - the staleness rules;
  - timeouts handled per V-06 (critical messages are never resent blindly);
  - a rate cap per channel.
- **Tests:**
  - **the outbox holds messages and then flushes them in order once reconnected;**
  - stale conversational replies are replaced by one "we're back" message;
  - a critical message whose outcome is unknown is not re-sent.
- **Acceptance:** all tests pass.
- **Recovery:** in-flight messages are marked `unknown` in recovery mode (P11).

### P5-T5: Channel state machine and health monitor
- **Track:** the state machine, circuit breaker and global pause are provider-neutral (AD-25). Detection from OpenWA webhooks and polls is **OpenWA-gated**.
- **Prerequisites:** T1 (core); T2 (OpenWA detection).
- **Deliverables:**
  - the states connected / reconnecting / needs_qr / logged_out / failed / suspected_restricted;
  - detection from the session webhook, a 60-second health poll and send errors;
  - a circuit breaker per tenant;
  - a global pause flag.
- **Tests:** **the circuit breaker pauses new orders within 60 seconds of `needs_qr`;** the global pause blocks new orders for every tenant.
- **Acceptance:** both tests pass.

### P5-T6: Inbound recovery (reconciliation job)
- **Track:** **OpenWA-gated.**
- **Prerequisites:** **V-04** decided (PC-1 proposes option (b): OpenWA's message store queried "since" a time).
- **Deliverables:** the reconciliation job, according to the V-04 outcome.
- **Tests:** with a simulated receiver outage, every message received during the outage is reconciled exactly once (dedup by V-05).
- **Acceptance:** zero missed messages and zero duplicates in the simulation.

### P5-T7: Meta Cloud API adapter stub (interface only)
- **Track:** provider-neutral (AD-25).
- **Prerequisites:** T1.
- **Deliverables:** a `MetaCloudAdapter` stub. **Not implemented** (AD-06).
- **Acceptance:** it compiles against the interface, and every method raises "not implemented".

### P5-T8: Provider-boundary check and log redaction
- **Track:** provider-neutral (AD-25).
- **Prerequisites:** T1–T4 (as built so far).
- **Deliverables:** a static check; a log-redaction test.
- **Tests:**
  - **business modules have no import path to OpenWA (enforced by a static check);**
  - secrets never appear in logs (log redaction tested).
- **Acceptance:** both checks run in CI.

## Phase deliverables
- **Deliverables:** provider package, OpenWA adapter, ingress endpoint, outbox worker, health monitor, reconciliation job.

## Phase acceptance criteria (exit)
- **Contract tests** against real OpenWA payloads recorded in P0.
- **Replays:** a duplicate webhook doesn't create a second event.
- **Crash test:** killing a worker in the middle of processing means the event is processed exactly once after its lease expires.
- **Signatures:** a bad signature is rejected (4xx) and never stored.
- **Channel down:** the outbox holds messages and then flushes them in order once reconnected.
- **Circuit breaker:** it pauses new orders within 60 seconds of `needs_qr`.
- **Provider boundary:** business modules have no import path to OpenWA (enforced by a static check).

## Risks and rollback
- **OpenWA behaviour changes:** pin its version and upgrade only after testing on a test number.
- **Rollback:** revert to the previous adapter or OpenWA version, or switch a tenant to the Meta adapter once one exists.

## Open items
- V-01, V-02, V-04, V-05, V-06, V-07 and V-08 are undecided until the live trial.
- PC-1 to PC-7 and PC-12 are awaiting review. PC-5 (OpenWA send pacing) needs an explicit owner decision.
