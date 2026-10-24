# OpenWA validation plan (two weeks, test numbers only)

- **Last updated:** 2026-10-21
- **Governing decisions:** [AD-05](../architecture/architecture-decisions.md#ad-05-openwa-is-the-intended-mvp-whatsapp-provider-behind-a-replaceable-interface) and the validation-dependent items V-01 to V-10.
- **Status:** NOT STARTED. No results exist yet. Do not record a result without the evidence listed below.

> OpenWA is **unvalidated**. It is not claimed to be compliant, reliable or production-ready. OpenWA's own README calls it unofficial and warns of "always a non-zero risk of account restriction or ban". WhatsApp's Terms of Service prohibit "auto-messaging" and "non-personal use … unless otherwise authorized". This trial measures technical behaviour. **It cannot make OpenWA compliant.**

## 1. Purpose

Answer the five questions the architecture depends on:
- **G1:** Are webhooks retried?
- **G2:** Can missed messages be recovered from history?
- **G3:** Are session credentials persistent and protectable?
- **G4:** Are first messages and replies delivered, and are menus usable?
- **G5:** What resources does each engine need, and how stable is it over 14 days?

The results decide V-01 to V-10.

## 2. What this trial can and cannot establish

| It can establish | It cannot establish |
|---|---|
| Whether webhooks are retried, and on what schedule | How likely a ban is across many merchants or over months |
| Whether message history can fill an outage gap | Whether WhatsApp will tighten enforcement in future |
| How sessions are stored and secured | Legal or contractual exposure (that's the lawyer's job) |
| First-message and reply delivery at low volume | Stability across future WhatsApp Web updates |
| RAM and CPU per session; short-term disconnect behaviour | Behaviour with 5+ busy restaurants at once |
| Whether numbered text menus work | Real customers' acceptance (that's the pilot's job) |

## 3. Setup (Day 0)

### Numbers and phones
- **Business test numbers (new SIMs owned by VELORA, never merchants' numbers):**
  - BIZ-1 runs the whatsapp-web.js engine.
  - BIZ-2 runs the Baileys engine.
  - BIZ-SPARE is held in reserve.
- **Customer test phones:** 3–4 phones run by team members or consenting volunteers.
- **First-contact test:** at least 20 extra numbers, with their owners' consent, that have never messaged the business numbers. **No messages go to anyone who hasn't agreed.**

### Server and OpenWA
- A VPS similar to the planned pilot host.
- OpenWA with its version or commit **pinned and recorded**.
- One OpenWA instance per engine.
- PostgreSQL as OpenWA's storage backend.
- OpenWA's API and its bundled dashboard reachable **only on the private network**.

### Tools and records
- **Webhook recorder:** throwaway test tooling, not product code. It logs raw headers, body and arrival time, and can be switched to return 200, return 500, hang, or be down.
- **Evidence log:** a spreadsheet, one row per test run. Template in §7.

### Traffic rules
- Customers start every conversation.
- At human pace.
- No more than about 10 conversations at once.
- No bulk sending.
- **No detection-evasion features.** No proxy rotation, no simulated typing, no fingerprint changes. The trial must measure the same behaviour the pilot will have.

### Stop rule
Any warning, restriction or ban on a test number **stops that engine's tests immediately**. It is recorded as a critical result and reviewed before anything continues.

## 4. Test cases and gates

### G1: Webhook delivery and retries

| ID | Test | Evidence to record |
|---|---|---|
| T1.1 | Recorder returns 500 for 2 min, 15 min and 1 h while a customer sends messages | Whether retries happen, their schedule, maximum attempts, and whether events are eventually lost |
| T1.2 | Recorder hangs for 30 s or longer | OpenWA's timeout value, whether it retries, any duplicates |
| T1.3 | Recorder is fully down (connection refused) for 5 min, 30 min and 2 h | Same as T1.1 |
| T1.4 | Signature check: tampered body, tampered header, replay of an old payload | Header name and format; whether the raw body is signed; whether a timestamp or nonce is included (V-08) |
| T1.5 | The same message is delivered more than once (retry or reconnect) | Whether the message ID is stable across deliveries (V-05) |
| T1.6 | Burst of 10 quick messages from one customer | Arrival order compared with send order; any gaps |

- **Pass:** either (a) retries are documented and lossless for at least 30 minutes, **or** (b) G2 provides complete recovery. In both cases signatures must be verifiable and message IDs stable.
- **Fail:** any of these:
  - no retries **and** no recovery;
  - signatures can't be verified;
  - message IDs are unstable and there is no workable fallback key.

### G2: Message-history recovery

| ID | Test | Evidence to record |
|---|---|---|
| T2.1 | Recorder down; customer sends 5 messages (text, location, image); recorder restored | Whether an API lists messages per chat or since a given time; completeness; whether IDs match the webhook IDs |
| T2.2 | Same as T2.1, but OpenWA is restarted during the outage | Completeness after restart |
| T2.3 | Business phone offline, session disconnected; customer sends messages; session reconnects | Whether queued messages arrive; whether webhooks fire for them |
| T2.4 | 2-hour outage across 3 chats | Time to recover; completeness |

- **Pass:** 100% of messages sent during the tested outages can be recovered, with IDs that can be used to remove duplicates.
- **Fail:** any message that can neither be recovered nor detected as missing.

### G3: Session persistence, encryption and secret protection

| ID | Test | Evidence to record |
|---|---|---|
| T3.1 | Inspect where session state is stored, file permissions, and whether OpenWA encrypts it | Notes from source and config review; file listing (V-07) |
| T3.2 | Restart the container; reboot the host | Whether it reconnects without a QR scan; how long it takes |
| T3.3 | Restore the session volume on a fresh host | Whether the session resumes. If it does, a stolen backup means account takeover (V-07) |
| T3.4 | Use one session's scoped API key against the other session | Must be refused; record the response |
| T3.5 | Unlink the device from the phone's Linked Devices screen | Whether an event fires; how long until it's detected |
| T3.6 | Business phone powered off for 24 h, 72 h and 7 days (use BIZ-SPARE) | Whether the session keeps working; when a QR scan is needed |
| T3.7 | Scan OpenWA logs and recorder logs for QR codes, tokens and keys | No secrets may appear |
| T3.8 | Upgrade OpenWA to a newer version | Whether sessions survive; the steps needed |

- **Pass:**
  - Restarts reconnect without a QR scan.
  - Scoped keys are enforced.
  - Unlinking is detected within 2 minutes or less.
  - No secrets appear in logs.
  - Session storage can be protected, either by OpenWA itself or on an encrypted volume that still works.
- **Fail:** any of these:
  - keys aren't enforced;
  - secrets appear in logs;
  - sessions don't survive a restart.

### G4: First-message delivery and messaging behaviour

| ID | Test | Evidence to record |
|---|---|---|
| T4.1 | At least 20 never-contacted numbers each message BIZ-1 and BIZ-2, spread over several days | Whether the inbound message arrives; whether the reply is delivered (phone ticks plus status event); latency (issue #830) |
| T4.2 | Status events for outbound messages (sent / delivered / read / failed) | Whether they're available and reliable (V-06) |
| T4.3 | The send call times out or hangs | Whether a message ID comes back synchronously; whether the outcome can be looked up later; whether retries cause duplicates (V-06) |
| T4.4 | Numbered menu with about 40 items; Urdu script; Roman Urdu; long messages | Rendering, length limits, whether messages need splitting (V-09) |
| T4.5 | Inbound location pin, image, voice note | Whether payloads can be parsed and media downloaded |
| T4.6 | Customer edits or deletes a message, or replies to a specific message | Which events arrive and what the payloads contain |
| T4.7 | Group, status and broadcast events | Whether they can be filtered out reliably |

- **Pass:**
  - At least 99% of inbound messages are received.
  - At least 99% of replies reach "delivered", with 95% delivered within 10 seconds.
  - **No silent loss:** every failure is reported or detectable.
  - Location and media payloads are usable.
- **Fail:** first messages are lost reproducibly with no way to detect it, or there are no delivery-status events at all.

### G5: Engine resources and stability (14-day soak test)

| ID | Test | Evidence to record |
|---|---|---|
| T5.1 | RAM and CPU per session, idle and during a simulated 8–11 pm rush | Hourly readings; growth per day (V-03) |
| T5.2 | Disconnects over 14 days | Count, cause, time to recover automatically, QR re-scans needed (V-10) |
| T5.3 | Pilot-like load: about 50 simulated orders a day per number, about 10 messages each | Errors, latency, any warnings |

- **Pass:**
  - RAM per session: Baileys 150 MB or less; whatsapp-web.js 600 MB or less.
  - Memory grows by less than 5% per day.
  - No more than one unprompted QR re-scan per number over the 14 days.
  - Short drops reconnect in under 2 minutes.
  - **Zero restrictions or warnings.**
- **Fail:** any restriction, repeated unprompted QR re-scans, or memory that keeps growing.

## 5. Schedule

| Days | Activity |
|---|---|
| 0 | Setup (§3) |
| 1–2 | G3 basics (T3.1–T3.4, T3.7); start the G5 soak |
| 3–5 | G1 and G2 (retries, outages, recovery) |
| 6–9 | G4 (first-contact numbers spread across days; statuses; menus; media) |
| 10–13 | Failure drills (T3.5, T3.6, T3.8, T2.2–T2.4); peak load T5.3 |
| 14 | Evidence review; gate verdicts; Go/No-Go recommendation to the owner |

## 6. Decisions after the trial

- **V-01 Go/No-Go:** **Go** only if all of the following hold. Otherwise **No-Go**: the owner then chooses between the Meta Cloud API (AD-06) and stopping.
  - G1–G5 each pass, or pass with a documented mitigation.
  - **Zero restrictions** occurred.
  - ~~The legal brief does not advise against proceeding (AD-08).~~ Withdrawn by the owner (AD-35).
- **V-02 engine:** whichever has the better G4 and G5 results.
- **V-04 recovery design:**

  | Trial result | Design |
  |---|---|
  | (a) Retries **and** history work | Current design |
  | (b) History works, but no retries | Mandatory reconciliation job after every outage |
  | (c) Neither works | Proceed only with highly available ingress **and** the owner accepting possible message loss in writing. Otherwise reconsider the provider. |

- **V-03, V-05 to V-10:** set from the recorded evidence.
- **Owner approval:** every Go/No-Go and every V-decision is presented to the owner with the evidence, for approval.

## 7. Evidence log template

| Field | Description |
|---|---|
| Test ID | e.g. T1.1 |
| Engine / OpenWA version | e.g. baileys / commit abc123 |
| Date-time (PKT) | Start and end |
| Steps | What was done |
| Expected | From this plan |
| Actual | What happened |
| Artifacts | Raw payload files, screenshots (phone ticks, Linked Devices), RAM/CPU logs |
| Verdict | Pass / Fail / Partial |
| Notes | Anomalies, follow-ups |

**Raw artifacts:**
- Store them in a dated evidence folder.
- **Strip or mask phone numbers** of volunteers before sharing.
- **Never store the session credential files** among the evidence.

## 8. Comparison with the alternative (Meta Cloud API, AD-06)

| Factor | OpenWA | Meta Cloud API |
|---|---|---|
| Policy | Outside the WhatsApp Terms of Service | Official |
| Restriction risk | Present, and can't be ruled out | Lower; restrictions based on message quality still exist |
| Per-message cost | None | 1,000 free service messages per number per month, then charged; delivery stops without a payment method (F1) |
| Setup | QR scan | Tech Provider or BSP; number migration or Coexistence (+92 unverified); billing (Pakistani card acceptance unverified) |
| Buttons and lists | Likely not available (F7) | Native |
| Webhook retries | Unknown (G1) | Up to 7 days (F3) |
| Data exposure | Every chat on the linked number | Business messaging only |

The application is built behind `WhatsAppProvider`, so a restaurant can move to the Meta adapter without the order, payment or database code changing.

## 9. Reduced trial variant (ADOPTED (delegated), AD-39, 2026-10-24; awaiting owner review)

**Why:** the owner can currently provide only their own phone and, later, one prepaid SIM. There are no volunteers, no always-on machine and no budget yet. The full plan in §3 assumes 3 SIMs, 3–4 customer phones, at least 20 volunteers and a VPS.

**What changes:**

| Item | Full plan | Reduced variant |
|---|---|---|
| Business numbers | BIZ-1, BIZ-2, BIZ-SPARE | **One** prepaid Pakistani SIM on WhatsApp Business, on the owner's phone (dual-SIM). **Not** the owner's personal number. |
| Engines | Both engines, at the same time | **One at a time** on the same number: Baileys first (lighter), then whatsapp-web.js if time allows. Switching means logging out and re-linking. |
| Customers | 3–4 phones plus ≥20 first-contact volunteers | The owner's own WhatsApp; add friends or family later if available |
| Machine | VPS | The owner's Windows computer for short sessions. The 14-day soak needs that computer left on, or a small VPS (costs in the [price sheet](../architecture/architecture-decisions.md#6-cost-estimates-not-verified-recheck-before-committing)). |
| Duration | 14 days | Short sessions as available. Soak only if a machine can stay on. |

**What the reduced variant can still answer:**
- G1 (retries): T1.1–T1.6
- G2 (history recovery): T2.1, T2.2, T2.4
- G3 (session security): T3.1–T3.5, T3.7, T3.8
- Most of G4: T4.2–T4.7
- Short-term resource use (part of T5.1)

**What it cannot answer well, so these stay "INSUFFICIENT EVIDENCE":**
- **T4.1 first-message delivery.** With one customer there's only one "first contact". It needs at least 5–20 never-contacted numbers.
- **T3.6 business phone offline.** The business and customer accounts share one phone, so the business phone can't go offline on its own.
- **T5.2/T5.3 14-day stability and pilot-like load.** These need an always-on machine and several customers.
- **T2.3 queued messages while the phone is offline.** Same reason as T3.6.

**Gate rule (unchanged):**
- A gate with any INSUFFICIENT EVIDENCE item **cannot pass**.
- The Go/No-Go recommendation must list every such gap.
- The owner can choose to accept specific gaps **in writing**. That acceptance is recorded as a risk, not as a pass.

**Decision:** adopted under the owner's delegation of 2026-10-24 (AD-39). The owner can still keep the full plan instead (§3–5). The kit is in [`../../trial/`](../../trial/).

## 10. Desk review of OpenWA source and docs (P0-E2, 2026-10-21)

> **This is desk evidence only.** Reading source code **does not pass any gate.** G1–G5 stay **NOT STARTED** until the live tests produce evidence.

**What was reviewed:**
- `rmyndharis/OpenWA`, `main` at commit `a3fc0ae4056c704eb51125e44b6511b79a69d4f2` (2026-10-18).
- Latest release **v0.25.0** (2026-10-15).
- Pinned engines: whatsapp-web.js 1.34.7 and Baileys 7.0.0-rc14 (a release candidate).
- MIT licence.

**Method:** GitHub API and raw files. Some long docs were read only in parts, and line numbers are approximate.

**Labels:**
- **VERIFIED:** stated in the source or docs.
- **INFERRED:** reasoned from them, not stated directly.
- **LIVE TEST:** can only be settled by running it.

### 10.1 Webhook delivery and retries (G1)
- **VERIFIED: failures and retries.**
  - Any non-2xx response, timeout or connection error counts as a failure.
  - The default is **3 attempts in total** (`retryCount`, configurable 0–5). Backoff is exponential from 5 s, so about 5 s and then 10 s.
  - Each POST times out after 10 s.
- **INFERRED: 4xx responses are retried like 5xx**, because the code has no status classification. → LIVE TEST.
- **VERIFIED: durable outbox.**
  - An outbox row is written **before** the first attempt.
  - A reconciler replays rows stuck in "pending": every 60 s, after a 60 s grace period, up to 5 replays. After that the row is marked failed.
  - Delivery is "at-least-once". An event can still be lost if the write fails between storing the message and storing the outbox row.
- **VERIFIED: failed-delivery store and redrive.**
  - Failed deliveries are listed at `GET /api/webhooks/delivery-failures` and kept for 90 days.
  - **Redrive only works if `WEBHOOK_FAILURE_PAYLOAD_RETENTION_HOURS` is above 0. The default is 0**, so by default there is nothing to redrive.
- **VERIFIED: queue options.** The default is an in-process retry loop. `QUEUE_ENABLED=true` with Redis switches to BullMQ.
- **VERIFIED: signature.**
  - HMAC-SHA256 over the exact body, in header `X-OpenWA-Signature: sha256=<hex>`. It must be verified on the raw bytes with a constant-time compare.
  - If no secret is set, the header is left out.
- **VERIFIED: idempotency headers.**
  - `X-OpenWA-Idempotency-Key` stays the same across retries.
  - `X-OpenWA-Delivery-Id` changes on replays.
  - `X-OpenWA-Retry-Count`.
- **VERIFIED: replay protection.**
  - There is **no** timestamp or nonce header.
  - The signed body does carry a `timestamp`. A strict freshness window would wrongly reject legitimate retries (INFERRED).
- **VERIFIED: one webhook per session, each with its own secret** (16–255 characters, write-only), via `POST /api/sessions/:sessionId/webhooks`.
- **VERIFIED: SSRF guard on by default** (`WEBHOOK_SSRF_PROTECT=true`). A receiver on a private IP may be refused unless this is configured.

### 10.2 Message history and recovery (G2)
- **VERIFIED: OpenWA stores inbound messages in its own database** before sending the webhook.
  - Default retention is **forever** (`MESSAGE_RETENTION_DAYS=0`).
  - If the database insert fails, the webhook is still sent (fail-open).
- **VERIFIED: reconciliation API.**
  - `GET /api/sessions/:sessionId/messages` reads OpenWA's own store, on both engines.
  - Filters: `chatId`, `direction`, and `since` / `until` (epoch ms). Pages through results with `after`.
- **VERIFIED: live history from WhatsApp is limited.**
  - `GET /messages/:chatId/history` works on **whatsapp-web.js only** (up to 100 messages, or 2000 with `deep`).
  - On **Baileys** it returns 501. Full history needs `BAILEYS_SYNC_FULL_HISTORY=true` set before linking.
- **VERIFIED: message IDs.**
  - The webhook's `data.id` is the engine's message ID.
  - The store keeps a unique index on (sessionId, waMessageId).
  - IDs are unique per account, not globally.
- **INFERRED: IDs are probably the same across webhook, store and acks.** An ID-normalising function wasn't reviewed. → LIVE TEST.
- **INFERRED: fixed.** A Baileys bug that dropped messages queued while offline (#1660, closed 2026-09-19) appears fixed. → LIVE TEST.

### 10.3 Sessions, secrets and authentication (G3)
- **VERIFIED: nothing is encrypted at rest by OpenWA.**
  - Plaintext items: session/auth state (whatsapp-web.js Chromium profile; Baileys `creds.json` and key files), stored messages, and webhook secrets.
  - OpenWA's own docs recommend an encrypted volume or encrypted database.
  - Baileys key files are mode 0600; the directory permissions are not set.
- **VERIFIED: everything sits in one Docker volume** (`/app/data`). Losing it means **every session must be re-linked and the API keys are lost**. `scripts/backup.sh` produces an **unencrypted** archive.
- **VERIFIED: session statuses.**
  - States: `created`, `initializing`, `qr_ready`, `authenticating`, `ready`, `disconnected`, `action_required`, `failed`.
  - `failed` is terminal and is never resumed automatically.
  - Logging out from the phone wipes the credentials, so a new QR scan is needed.
- **VERIFIED: reconnection.**
  - Exponential backoff with jitter, capped at 5 minutes.
  - **`maxReconnectAttempts` defaults to unlimited.**
  - A `session.reconnect_loop` event fires every 5th attempt.
- **VERIFIED: session events.** `session.status`, `.qr`, `.authenticated`, `.disconnected`, `.reconnect_loop`, `.restriction`.
- **VERIFIED: API keys.**
  - Stored as SHA-256 hashes, or HMAC if `API_KEY_PEPPER` is set. Setting the pepper later invalidates every existing key.
  - Roles are admin, operator and viewer. Keys can be scoped by session, chat, IP and expiry.
- **VERIFIED: admin key exposure.** The admin key is written to `data/.api-key` (0600) and **printed in the first-boot log**, so those logs are sensitive.
- **VERIFIED: dashboard.**
  - It runs on the API port (2785) and can be turned off with `SERVE_DASHBOARD=false`.
  - There's no built-in TLS.
  - Swagger is off in production mode.
- **VERIFIED: QR codes not logged on whatsapp-web.js.** The Baileys logging path wasn't checked. → LIVE TEST.
- **SEEN while building the kit (2026-10-24, no number linked):** v0.25.0 writes the admin key into its log at first boot even when it is supplied as `API_MASTER_KEY`. To be confirmed and recorded under T3.7.

### 10.4 Messaging (G4)
- **VERIFIED: sending.**
  - A send returns **201 with `{messageId, timestamp}` right away.** That means "accepted", **not delivered**.
  - Delivery is tracked through `message.ack` (pending / sent / delivered / read / failed) and `message.failed`.
- **VERIFIED: send idempotency.**
  - Sends accept an optional **`Idempotency-Key`** header (since v0.25.0). A repeat returns the stored response.
  - A repeat sent while the first request is still in progress gets a 409.
  - The key's lifetime wasn't found.
- **VERIFIED: no buttons or lists.** There are no endpoints for sending them, which confirms numbered text menus. Polls exist.
- **VERIFIED: locations and groups.**
  - Incoming locations include latitude and longitude on both engines.
  - Group, status and broadcast events still arrive, marked with `kind` and `isGroup`. They can be filtered per webhook.
- **VERIFIED: issue #830 is still open.** A first message *sent* to a brand-new contact can return 201 and never arrive. There's no fix. → LIVE TEST T4.1. Customer-started chats may behave differently.
- **VERIFIED: optional send pacing.** It's off by default (`SEND_PACING_ENABLED`) and includes a "warm-up schedule". See proposal PC-5 in the decisions document.

### 10.5 Operational risks
- **VERIFIED, open: #560.** A mandatory WhatsApp passkey can **block all device linking** (QR and pairing code). It's blocked upstream and there's no workaround. **It could stop the trial at the linking step.**
- **VERIFIED, closed reports:** bans (#469, #1304), frequent disconnects (#1243, #1546), repeated LOGOUTs (#982, #1072), a stuck failed session (#1580), and engine breakage after WhatsApp Web changes (#1643, #708). whatsapp-web.js relies on several local patch scripts.
- **VERIFIED: no high availability.** One OpenWA instance per data volume, with replicas = 1. This is consistent with AD-19.
- **VERIFIED: resources.**
  - RAM per session: whatsapp-web.js ~300–500 MB, Baileys ~30–80 MB.
  - The docs' own sizing tables are "unbenchmarked" and disagree (1 GB vs 2 GB for 1–5 sessions).
  - Docker defaults to `OPENWA_MEM_LIMIT=2g`.
- **VERIFIED: disclaimer.** The README says "always a non-zero risk of account restriction or ban". The user is responsible for compliance with WhatsApp's terms, and it's **not** for regulated deployments.

### 10.6 What the desk review changes for the live trial (ADOPTED (delegated), AD-39)
Adopted for the trial (AD-39): T3.9 joins G3, the baseline is the kit's configuration, and the added checks are in the evidence log template. The original test cases in §4 stay in force.

1. **Add T3.9 (linking):** record whether a passkey prompt (#560) blocks linking. If it does, the trial stops at that step, which is a No-Go risk.
2. **Trial configuration baseline:**
   - `retryCount=5`
   - `WEBHOOK_FAILURE_PAYLOAD_RETENTION_HOURS` above 0
   - `SERVE_DASHBOARD=false`, or local-only
   - `API_KEY_PEPPER` set **before** first boot
   - a deliberate `maxReconnectAttempts`
   - a scoped operator key per session
   - an encrypted volume
   - first-boot logs scrubbed

   The same baseline would apply to the pilot.
3. **Add reconciliation tests:**
   - check that `GET /messages?since=` returns every message received during a receiver outage, on both engines (part of G2);
   - check that IDs match across webhook, store and ack (T1.5);
   - check whether 4xx responses are retried (T1.1);
   - check whether `since` filters on message time or storage time.
4. **Add send-idempotency test:** replay a send with the same `Idempotency-Key` and confirm no duplicate message (part of T4.3).
5. **Add log check (T3.7):** cover first-boot logs, which contain the admin key, and the Baileys QR path.

### 10.7 Questions only the live trial can answer
1. Are message IDs byte-identical across the webhook, the send response, acks and the store, on both engines?
2. Are messages that arrive while **OpenWA itself** is down delivered after it reconnects?
3. What are the real retry timings, and how does the reconciler behave after a forced restart?
4. What are the delivery rates and any restriction behaviour on a Pakistani number?
5. How much RAM and CPU does each engine actually use, and does it grow over days?
6. Does Baileys log QR codes, and what are the real file and directory permissions?
7. How long does a send idempotency key last?
8. Does the passkey prompt appear for our test number?

## Appendix: Beginner setup guide for the live test (draft, 2026-10-21)

**Before you start:**
- Written for someone without technical experience.
- The variant and the tooling are adopted (AD-39). **Do not begin** without the test SIM.
- The exact commands are in [`../../trial/README.md`](../../trial/README.md).

### Part 1: Phone (about 15 minutes)
1. Buy **one prepaid Pakistani SIM** registered to your CNIC. **Don't use your personal WhatsApp number** for the test business.
2. Put it in your phone's second SIM slot, or set it up as an eSIM.
3. Install **WhatsApp Business** from the Play Store or App Store and register it with the new SIM's number.
4. Business name: "VELORA Test" (anything is fine). Don't add contacts, join groups or post a status from this number.
5. Your normal WhatsApp stays unchanged. It plays the **customer**.

### Part 2: Computer (about 30–60 minutes, one time)
1. You need **Windows 10 or 11**, with at least 8 GB of RAM if possible.
2. Install **Docker Desktop** from docker.com and restart when asked.
   - If it complains about "virtualization" or "WSL", follow its on-screen link. You may need to enable virtualization in the computer's BIOS. I'll walk you through it.
   - *Licence:* Docker Desktop is free for personal use and small businesses under Docker's terms. **Check their current terms** before installing.
3. In Windows **Settings → Power**, set **Sleep: Never** while the test runs, so the computer doesn't sleep in the middle of a test.

### Part 3: Start the test tools (about 10 minutes)
1. Use the `trial/` folder of the repository on the computer.
2. Open the folder, right-click an empty area, choose **Open in Terminal**, and run:
   ```
   powershell -ExecutionPolicy Bypass -File setup.ps1
   docker compose up -d
   ```
3. Check that it's running with:
   ```
   docker compose ps
   ```
   Everything should say "running" or "healthy".

### Part 4: Link the test number (about 5 minutes)
1. Open the OpenWA local page in your browser. The address is in the trial README, something like `http://localhost:<port>`. **This page must never be opened to the internet.**
2. Create a session (for example `biz1`). A **QR code** appears.
3. On your phone, open **WhatsApp Business** (the test number) → **⋮ menu → Linked devices → Link a device**, and scan the QR code.
4. The session status should change to **connected**.
5. **Never screenshot or share the QR code.** Anyone who scans it controls that WhatsApp account.

### Part 5: Running a test session
1. Claude gives you a short script, for example:
   > "From your personal WhatsApp, send `menu dikhao` to the test number. Wait 1 minute. Screenshot the ticks."
2. Do each step, note the time, and save screenshots to the dated evidence folder.
3. Claude checks the recorder logs and fills in the evidence log. **Nothing is marked passed without evidence.**
4. Some tests ask you to switch the recorder off and on, or restart a container. The README will have a one-line command for each.

### Part 6: Safety rules
- Only message **your own** numbers, or people who have agreed to help. **No strangers. No bulk messages.**
- If WhatsApp shows **any warning, restriction or ban** on the test number:
  1. **stop immediately**;
  2. take a screenshot;
  3. tell Claude.

  **Don't** try to work around it (no new numbers to dodge it, no proxies, no tricks).
- Never share the session folder, the QR code, or any key from the README.
- Keep volunteers' numbers private. Shared evidence uses masked numbers.

### Part 7: Finishing or pausing
1. On the phone: **WhatsApp Business → Linked devices**, tap the test device, **Log out**.
2. On the computer:
   ```
   docker compose down
   ```
3. Keep the evidence folder. Delete the session data folder if told to (the README will say which).

### Part 8: Troubleshooting
| Problem | Try this |
|---|---|
| Docker Desktop won't start | Restart Windows. Check virtualization is enabled (Task Manager → Performance → CPU → "Virtualization: Enabled"). |
| QR code won't scan | Refresh the page to get a new QR code; check the phone has internet; make sure you're scanning from the **WhatsApp Business** app, not your personal one. |
| Status shows "disconnected" | Write down the time (that's evidence), then follow the README's reconnect step. If it asks for a new QR, scan again and note it. |
| The computer slept or rebooted | Write down the time. Restart with `docker compose up -d`. Note whether a new QR was needed. |
| Any WhatsApp warning on the test number | **Stop.** See Part 6. |
