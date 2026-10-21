# VELORA architecture decisions

- **Last updated:** 2026-10-21
- **Basis:**
  - The architecture review of the research documents (Critic / Defender / Independent Judge).
  - The owner decision session (A1–A12).
  - The owner's conditional approval of the MVP direction.
  - The owner's delegation of the remaining choices.
- **Status legend:** see [`../README.md`](../README.md#status-legend-used-in-all-engineering-documents).

> **Note on authority.**
> - **APPROVED** = explicitly approved by the owner.
> - **ADOPTED (delegated)** = the owner said "do the best approach" and the engineering lead chose. These are binding for planning but **reversible, and listed for owner review**.
> - Research recommendations are not requirements unless they appear here.

---

## 0. External facts these decisions rely on (checked 2026-10-20)

Facts change. Re-verify anything time-sensitive before relying on it.

| # | Fact | Source |
|---|---|---|
| F1 | Meta has priced per message since 1 Jul 2025. From 1 Oct 2026 each business number gets 1,000 free *service messages* per month; from message 1,001, messages are charged. **Without a payment method, Meta stops delivering them.** Utility templates sent inside the customer-service window are charged from 1 Oct 2026. | Meta WhatsApp pricing page (official) |
| F2 | Cloud API groups: Official Business Account only, max 8 participants, no interactive messages. | Meta Groups docs (official) |
| F3 | Cloud API retries undelivered webhooks for up to 7 days. | Meta webhooks docs (official) |
| F4 | Coexistence (Business app + Cloud API on the same number) exists. Support for Pakistan (+92) numbers is **unverified**. | Meta docs plus third-party sources |
| F5 | OpenWA (github.com/rmyndharis/OpenWA) is MIT-licensed and unofficial. Its README says "always a non-zero risk of account restriction or ban" and to treat it as "not approved" for regulated deployments. Engines: whatsapp-web.js (~300–500 MB per session) and Baileys (~30–80 MB per session). It supports multi-session, HMAC-signed webhooks and session-scoped API keys. **Webhook retries, history recovery, high availability and session encryption are not documented.** Open issues: #830 (first messages to new contacts sometimes not delivered) and #560 (passkey linking hang). | OpenWA README and release notes |
| F6 | WhatsApp Terms of Service prohibit "bulk messaging, auto-messaging, auto-dialing" and "non-personal use … unless otherwise authorized". Accounts may be disabled, and the user agrees not to create another account without permission. | whatsapp.com/legal/terms-of-service |
| F7 | Buttons and lists sent from unofficial clients reportedly have not rendered since about 2023. | Third-party (Green-API, DEV); **verify in trial** |
| F8 | Supabase Free has no automated backups or PITR and pauses after 1 week of inactivity. Pro is about $25/mo with daily backups. Upstash free tiers: 500K Redis commands per month and 1,000 QStash messages per day. | Official pricing pages (prices to recheck) |
| F9 | Gemini 1.5 Flash is retired. Groq lists `llama-3.3-70b-versatile` as a production model, with rate limits per organisation. | Google / Groq |
| F10 | Pakistan's Personal Data Protection Bill had not been enacted as of May 2026. The Punjab Revenue Authority's restaurant tax since 1 Jul 2026 is 8% for card/digital payments and 16% for cash. | Chambers; Geo / ProPakistani |
| F11 | Raast P2M confirmations reach merchants through payment providers, not from the State Bank directly. No public NayaPay Arc webhook specification was found. | SBP coverage; vendor pages |

---

## 1. APPROVED decisions (owner)

### AD-01: MVP scope is restaurants first, narrow pilot
- **Decision:** restaurants and cloud kitchens only, one city, one branch each, delivery and takeaway, 3–5 pilot restaurants. Details are in [`../planning/mvp-scope.md`](../planning/mvp-scope.md).
- **Approved:** A3, reaffirmed in the conditional approval ("restaurants-first scope").
- **Rationale:**
  - It gets real orders fastest, with the fewest failure points.
  - The hotel design in the research had no stays model, a plaintext and guessable PIN, and no folio ledger ([`08`](../research/08-database-and-multi-tenancy.md) §4.1, [`10`](../research/10-business-onboarding-and-user-flows.md) §5).
  - The research itself recommends piloting with kitchens first ([`03`](../research/03-pakistan-market-research.md) §6, [`18`](../research/18-risk-register-and-international-expansion.md) §3).
- **Alternative:** restaurants plus hotel room service from day one.
- **Trade-offs:** lower cost and risk, and no room-folio fraud surface. The pitch is narrower and hotels wait. The schema must still let hotels and branches be added later without a rewrite.
- **Conflicts with research:** [`02`](../research/02-prd-requirements-analysis.md) §1.3 and §7 and [`10`](../research/10-business-onboarding-and-user-flows.md) §5 put hotel room service in the MVP. This decision overrides them.

### AD-02: Staff web dashboard and human-takeover inbox
- **Decision:** customers use WhatsApp only. Staff and owners use an installable web app (PWA) with:
  - an order board;
  - payment confirmation;
  - a chats/takeover inbox;
  - menu availability, pause, and a capacity cap;
  - a daily summary;
  - WhatsApp status and QR re-link.
- **Approved:** A2, reaffirmed.
- **Rationale:**
  - WhatsApp groups can't carry interactive buttons (F2), and OpenWA probably can't send them at all (F7).
  - Payment confirmation needs an authenticated, audited actor.
  - Re-linking a dropped WhatsApp session can't happen inside WhatsApp.
- **Alternative:** WhatsApp-only staff operation using typed commands.
- **Trade-offs:** one more surface to build and secure, and the kitchen needs a device. Errors drop and operations become auditable.
- **Conflicts with research:** the "dashboard-less" claims in [`02`](../research/02-prd-requirements-analysis.md) §1.1, [`04`](../research/04-competitor-analysis.md) §5 and [`10`](../research/10-business-onboarding-and-user-flows.md) are overridden.

### AD-03: Payments at launch are COD plus staff-verified transfers
- **Decision:**
  - **Cash on delivery**, with a limit on a customer's first order and a blocklist.
  - **Bank or wallet transfer into the restaurant's own account.** Staff confirm receipt on the dashboard and record the transaction reference, and the order becomes `paid_staff_verified`, logged with who confirmed it and when.
  - **AI output, customer text or a screenshot never marks an order as paid.**
  - Each restaurant uses its own accounts. VELORA never holds customer funds.
- **Approved:** A4, reaffirmed.
- **Rationale:**
  - No payment provider's signed webhooks, status API and merchant onboarding have been verified (F11).
  - The research's "SBP dispatches an instant webhook" claim ([`12`](../research/12-pakistan-payments-and-localization-v2.md) §2.1) is false.
- **Alternative:** wait for an automated payment provider.
- **Trade-offs:**
  - Launch is faster and VELORA carries no funds-holding or licensing exposure.
  - COD fake-order risk remains, and verification relies on staff attention. Staff actions are audited.
- **Later:** add one online payment provider, only after a sandbox test passes. See the PENDING EXTERNAL items in §4.

### AD-04: Managed PostgreSQL with backups, a small always-on server, and a paid AI model
- **Decision:**
  - A paid managed PostgreSQL service with automated backups.
  - A small, persistent server (not a sleeping free host).
  - A paid LLM tier whose terms exclude training on our data.
- **Approved:** A6, reaffirmed.
- **Rationale:** free database tiers have no backups and pause (F8). The planned free model is retired (F9). Order and payment records need recoverability.
- **Alternatives:** self-hosted Postgres on the VPS (cheaper, but you own backups and recovery); free tiers (rejected for production data).
- **Trade-offs:** about $40–75/mo before any revenue (see §6), in exchange for a large reliability gain.

### AD-05: OpenWA is the intended MVP WhatsApp provider, behind a replaceable interface
- **Decision:**
  - OpenWA is the *intended* integration for the MVP. The app reaches it only through a `WhatsAppProvider` interface. Orders, payments, the database and the AI must not depend on OpenWA.
  - **The five validation gates G1–G5 are mandatory** ([`../planning/openwa-validation-plan.md`](../planning/openwa-validation-plan.md)).
  - Until they pass, webhook retries, history recovery, session encryption, first-message delivery and resource needs are **assumed not to work**.
  - **OpenWA is not claimed to be compliant, reliable or production-ready.**
- **Approved:** owner decision on A7, plus the conditional approval (items 1, 3 and 7).
- **Rationale (owner):** no Meta per-message fees (F1) and fast onboarding by QR scan.
- **Risks the owner accepted, to be re-checked by the legal brief:**
  - It operates outside the WhatsApp Terms of Service (F6).
  - Merchant numbers could be restricted or banned.
  - A WhatsApp Web change could break every restaurant at once.
  - VPS hosts session credentials with full account access.
  - It may receive non-business chats on the linked number.
- **Alternative:** the official Meta Cloud API (AD-06).
- **Engineering lead's note:** the Meta Cloud API is the safer long-term foundation. OpenWA is justified only as a time-limited, gated pilot with informed consent, reactive-only messaging, no detection evasion, stop rules, and a live migration path.

### AD-06: Meta Cloud API is a future alternative; the insurance track continues
- **Decision:**
  - A `MetaCloudAdapter` is designed for at interface level only and **not implemented**.
  - Applying for Meta Tech Provider status and testing Coexistence on a +92 number continue as low-cost insurance.
- **Approved:** A1 (two-track plus Coexistence test) and condition 7.
- **Rationale:** lets any restaurant move to the official API without rewriting the application.
- **Trade-offs:** small effort on paperwork and testing.
- **Applies only if Meta is adopted:**
  - per-message billing (F1);
  - whether Billing Hub accepts Pakistani cards (unverified);
  - Pakistan rate card (unverified).

### AD-07: Pricing is a free pilot, then a PKR hybrid
- **Decision:**
  - The pilot is free or heavily discounted for 30–60 days.
  - Afterwards, a PKR hybrid: a low monthly base plus a small per-order fee. Amounts are set from interviews and measured costs.
  - Meta billing terms (the merchant pays Meta if their card works) apply **only if** Meta Cloud API is adopted.
- **Approved:** A5.
- **Rejected from research:** "$0 Meta", 93% margins, and a $52 ARPU for Pakistan ([`06`](../research/06-technology-stack-and-cost-analysis.md), [`13`](../research/13-seo-geo-customer-acquisition.md), [`14`](../research/14-monetization-pricing-and-revenue-model.md)).

### AD-08: Lawyer and accountant
- **Decision:** get quotes now, and brief a Pakistani tech/corporate lawyer and a tax accountant **before** paid customers. Under AD-05 the legal brief must also cover OpenWA's Terms-of-Service exposure and the consent wording **before any real merchant is connected**.
- **Approved:** A8.

### AD-09: VELORA has its own database; Stock Flow is excluded
- **Decision:** inventory and availability are an internal module of VELORA's own database.
- **Approved:** owner constraint.
- **Conflicts with research:** every "Stock Flow" reference in research docs 01, 02, 04, 06, 07, 08, 10, 12, 13 and 14 is superseded.

### AD-10: Research documents are inputs, not requirements
- **Approved:** owner constraint. See [`../README.md`](../README.md#precedence).

### AD-25: Provider-neutral build track while the OpenWA test waits
- **Decision:**
  - While the live OpenWA validation is paused, the **provider-neutral parts of P5** may be built and tested against the `FakeProvider` (simulated WhatsApp) before the OpenWA Go:
    - the `WhatsAppProvider` interface;
    - canonical events;
    - the `FakeProvider`;
    - the core inbox and outbox logic;
    - the channel state machine and circuit breaker;
    - the Meta stub;
    - the provider-boundary check.
  - **P6–P9** may be built and tested against the `FakeProvider` on the same basis.
- **Still gated by the OpenWA Go (V-01) and the V-decisions:**
  - the OpenWA adapter, its webhook endpoint and its reconciliation (P5-T2, the OpenWA parts of T3 and T5, and T6);
  - any real WhatsApp traffic;
  - P10–P13 and the pilot.
- **Phase authorisation is unchanged.** Every phase still needs the owner's explicit authorisation. This decision only changes which phases *can* be authorised before the Go.
- **Approved:** owner, 2026-10-21 ("Option 2: simulated WhatsApp").
- **Rationale:** progress continues without a test SIM. The business logic never depends on OpenWA (AD-05), so the work carries over to OpenWA or to the Meta Cloud API.
- **Trade-off:** some details may need small rework after the trial, such as menu length limits (V-09), the deduplication key (V-05) and send-timeout handling (V-06).
- **Alternative rejected by the owner:** stop after P4 until the Go.

---

## 2. ADOPTED (delegated) decisions

The owner said "do the best approach" for these. Each is reversible and listed in §7 for owner review.

### AD-11 (A9): Pakistan only until the pilot proves traction
- **What:** PKR; Urdu, Roman Urdu and English; Pakistani payment methods only. The international gate is set from pilot data.
- **Alternative:** the international timelines in [`13`](../research/13-seo-geo-customer-acquisition.md) §7 and [`14`](../research/14-monetization-pricing-and-revenue-model.md) §9, which this rejects.

### AD-12 (A10): Human owner verification and manual recovery
- **Before a restaurant goes live:** verify the owner's identity and phone, and that the business exists (for example an NTN, shop visit or photos).
- **Consent:** keep the owner's signed consent naming the ban risk.
- **Staff:** added only by owner invite.
- **Recovery:** account recovery (lost phone, staff departure, banned number) follows a manual runbook.
- **Why:** scanning the QR code proves who holds the phone, not who owns the business.

### AD-13 (A11): No original PRD or doc 16 is assumed
If either turns up, it is reconciled against these decisions. It does not override them.

### AD-14 (A12): Voice notes are deferred
The bot replies: "Please type your order, or reply 0 to talk to staff."

### AD-15: Python 3.12 + FastAPI
- **What:** a small ingress service plus separate worker processes.
- **Why:** mature async web stack, strong data and AI tooling, and consistency with the research.
- **Alternative:** Node.js/TypeScript (the same language as OpenWA).
- **Trade-off:** two languages across the system, but OpenWA is only ever reached over HTTP.

### AD-16: PostgreSQL job queue with inbox and outbox (no Redis, no QStash)
- **What:** jobs live in Postgres and are claimed with `SELECT … FOR UPDATE SKIP LOCKED`.
  - **Inbox:** every inbound event is stored durably **before** the webhook is acknowledged.
  - **Outbox:** every outbound message carries an idempotency key, retries with backoff, and has staleness rules.
- **Why:** state changes and messages commit in the same transaction, with fewer vendors to run. It avoids the research's lossy design (SETNX before processing, plus a 24-hour TTL).
- **Alternative:** Redis or QStash.
- **Trade-off:** lower throughput, which is ample for the pilot.

### AD-17: Tenant isolation
- **Schema:** one shared schema with `tenant_id` on every tenant row.
- **Roles:** a non-owner runtime role (`app_rw`, NOBYPASSRLS) with **FORCE ROW LEVEL SECURITY**. Migrations run under a separate owner role.
- **App layer:** tenant scoping in code as well as in RLS (defence in depth).
- **Tenant resolution:** a narrow bootstrap function resolves the tenant from the provider session. **The tenant ID never comes from a request body or from the AI.**
- **Keys and deletes:** per-tenant composite unique keys (SKU, order number). Financial foreign keys use `RESTRICT`.
- **CI:** a check that enumerates every table and fails if RLS, FORCE or a tenant policy is missing.
- **Rejected:** research doc [`08`](../research/08-database-and-multi-tenancy.md) §5–6, which uses SECURITY DEFINER functions taking a caller-supplied tenant, global unique SKUs, and `service_role` at runtime.

### AD-18: Separate ordering number per restaurant, with written consent
- **What:** each restaurant uses a separate ordering number, not its main line, and signs consent naming the ban risk. The old number can carry an away message pointing customers to the new one.
- **Why:** a ban does not take down the restaurant's main customer line.
- **Pending external:** merchant interviews to confirm restaurants will accept this.

### AD-19: One VPS for the pilot
- **What:** the API, workers and OpenWA run as separate containers on one host, with:
  - a circuit breaker per channel;
  - a global pause;
  - uptime alerts;
  - runbooks.
- **Trade-off:** cheap, but a host failure takes down every restaurant until it is restored.
- **Size:** set by V-03.

### AD-20: Order safety
- **Pricing:** the server prices every cart from the database, with price snapshots.
- **AI:** it only *proposes* a cart and has no write tools. It never receives the tenant, the actor's identity, phone numbers or addresses.
- **Summary:** the cart summary is rendered from a template, including tax for the chosen payment method.
- **Confirmation:** the customer replies **YES**. That YES maps to a single-use, expiring nonce, consumed in the same transaction that creates the order.
- **Duplicates:** prevented by:
  - one-at-a-time processing per conversation;
  - an inbox unique key;
  - guarded state transitions;
  - idempotency keys on dashboard actions;
  - a "possible duplicate" flag for staff (never an automatic cancellation).
- **Delivery independence:** order and payment state never depend on whether a WhatsApp message was delivered. A customer can reply "status" at any time.

### AD-21: Backups and restore
- **What:** the provider's daily backups plus **hourly encrypted logical dumps** off-site.
  - Dumps must include roles, grants and RLS policies.
  - A **restore drill is required before the pilot.**
  - Targets: data loss of 1 hour or less (RPO ≤ 1 h) and recovery within 4 hours (RTO ≤ 4 h). These are targets, not guarantees.
- **Restart behaviour:** every restart runs a recovery mode (defined in [`../planning/implementation-plan.md`](../planning/implementation-plan.md) P11).

### AD-22: Pilot stop rules
Stop or pause the pilot on any of these:
- any number restriction or ban (pause onboarding and reassess, including moving that restaurant to Meta);
- any wrong price, or any order falsely marked "paid";
- disconnects above the threshold set by V-10.

### AD-23: Staff authentication
- **Login:** a one-time magic link creates a device session.
- **Expiry:** 12 hours idle, 30 days absolute.
- **Ending sessions:** logout, owner revocation, or "log out all devices".
- **Shared kitchen tablets:** a kitchen-device role with limited rights. Confirming a payment needs a personal staff PIN, so the audit log shows who confirmed it.

### AD-24: Security baseline
- **OpenWA:**
  - reachable only on a private network;
  - its dashboard and API never exposed;
  - session state stored on an encrypted volume;
  - one API key per session, stored encrypted.
- **Webhooks:**
  - a **separate HMAC secret per session**, verified on the raw body;
  - the session named in the payload must match the webhook registration.
- **Secrets:** none in logs, and no real merchant credentials in the repository.
- **Messaging behaviour:**
  - reactive replies only;
  - sending is rate-capped;
  - STOP opts a customer out;
  - every first reply discloses that it's an automated assistant.
- **No detection-evasion features:** no proxy rotation, simulated typing or fingerprint tricks.
- **Data minimisation:** group, status and broadcast events are ignored and not stored.
- **Offboarding:** log the session out, then confirm the device has been removed from the phone.

### AD-26: P2 implementation choices (database and tenant isolation)
- **Roles:**
  - `velora_owner` is the migrator and owns everything.
  - `app_rw` is the runtime role: NOBYPASSRLS, NOINHERIT, and owns nothing.
  - `velora_resolver` is NOLOGIN and owns only the bootstrap resolver, the single SECURITY DEFINER function.
- **Migrations:** plain SQL files with a checksummed runner. Editing an applied migration is refused. `down` is refused outside development and test.
- **Schema:**
  - Child rows use composite `(tenant_id, id)` foreign keys, so a row can't point at another tenant's row.
  - Money is `bigint` paisa.
  - Every foreign key is `ON DELETE RESTRICT`.
  - Customers are soft-deleted and anonymised.
  - History tables are append-only for the runtime role.
- **Findings for later phases:**
  - **P5:** under FORCE RLS, a worker can't claim queue jobs across tenants with `SKIP LOCKED`. This needs a design that keeps the resolver as the only SECURITY DEFINER function. *Resolved by AD-29 (the `velora_dispatcher` role).*
  - **P11:** `pg_dump` needs a role that bypasses RLS, and database-level grants are not in a plain `pg_dump`.
- **Evidence:** PR #3. CI runs 39590467933 (PR) and 39590889951 (`main` after the merge).

### AD-27: P3 implementation choices (onboarding and staff identity)
- **B7, magic-link delivery: manual for the pilot.**
  - VELORA does not send login links. The founder's tool prints the owner's link, and the dashboard returns staff links to the owner. Each link is passed on privately.
  - The token sits in the URL fragment, so it never reaches server or proxy logs.
  - *Trade-off:* whoever issues a link can see it. Links are single-use, expire after 15 minutes and are audited. The payment PIN is set by the person after they sign in.
  - Email delivery can replace this later without changing the data model.
- **Admin onboarding is a command-line tool (`python -m velora.admin`), not web screens.** This means there is no internet-facing admin surface during the pilot. Web screens can come with P9.
- **Platform role:** `velora_platform` runs the founder tool.
  - It is NOBYPASSRLS and owns nothing.
  - It works one tenant at a time, under the same tenant expression as the runtime (`platform_scope` policies).
  - Its only cross-tenant right is listing tenants (`platform_directory`).
  - No new SECURITY DEFINER function was added.
- **Tokens:** magic-link and session tokens contain the tenant id as an **unverified lookup hint**.
  - A token is accepted only if the SHA-256 of its 256-bit secret matches a row inside that tenant's RLS scope.
  - A forged tenant id therefore finds nothing.
  - This avoids a second cross-tenant lookup function.
- **Roles:** owner, manager, kitchen and kitchen_device. The P2 value `staff` was renamed `kitchen`.
  - Only the owner manages staff (AD-12).
  - Managers can do everything else.
  - Kitchen staff can view and update orders and confirm payments with their PIN.
  - A kitchen device can only view and update orders. To confirm a payment on a kitchen device, a named person enters their own PIN.
- **Payment PIN:**
  - 6 digits, with repeated and consecutive digits refused.
  - Stored as a salted scrypt hash.
  - 5 wrong tries lock it for 15 minutes, and the owner can reset it.
  - Every rejection is audited.
- **Sessions and CSRF:**
  - The session cookie is `__Host-`, HttpOnly, Secure and SameSite=Strict.
  - State-changing requests need a CSRF header derived from the session.
  - A browser `Origin` header must match the dashboard.
- **Login rate limit:** in-process, 10 attempts per 5 minutes per client address, which fits one API container (AD-19). **P12 must configure trusted proxy headers.** Otherwise every client shares the proxy's address and the limit becomes global.
- **Going live:**
  - A tenant needs an effective, verified owner verification and an effective `separate_number_ban_risk` consent. The database enforces this with a trigger.
  - Terms and data-processing consents are not required until the legal review (B2).
- **Records:**
  - Consents, owner verifications and the audit log can't be updated, deleted or truncated, even by a superuser (database triggers).
  - A correction is a new record that supersedes the old one.

### AD-28: P4 implementation choices (menu, availability and settings)
- **Menu versions:**
  - A menu version is a complete relational catalogue: categories, items, modifier groups and options, all with integer paisa prices.
  - Each tenant has at most one draft.
  - Publishing freezes a version. Database triggers then refuse any change to it or its rows, even by a superuser.
  - `menu_publications` is the append-only history, and the newest publication is the live menu.
  - **Rollback** means re-publishing an older version.
- **One write path:**
  - CSV import and form entry (`PUT /api/menu/draft`) both replace the whole draft, after the same validator.
  - Draft content is never updated row by row (no UPDATE grant).
- **No accidental go-live:**
  - Publishing needs the revision the reviewer saw. If the draft changed since, the request gets 409.
  - The database only accepts a publication of a frozen version.
  - Customer-facing reads use only the newest publication.
- **Founder import:**
  - The founder's command-line tool (`menu-import`) fills the draft as `velora_platform`.
  - That role can't freeze or publish. The owner reviews the draft, including its price changes against the live menu, and publishes it.
  - This is how we read "form entry by the founder" while AD-27 keeps admin web screens out of the pilot.
- **Price audit:** every draft save, import and publication is audited in the same transaction, with the SKUs added and removed and every price change (from and to).
- **Permissions:**
  - Menu editing, publishing and availability need `menu.edit` (owner, manager).
  - Settings, pause and resume need `settings.edit` (owner, manager).
  - The pause status view needs only `orders.view`, so kitchen roles can see it.
  - *Trade-off:* kitchen staff can't mark an item sold out or pause orders themselves during a rush. Granting that is a one-line change to the role matrix if the owner wants it.
- **Availability:**
  - Availability is per SKU and outside the versions, so a toggle applies at once and carries into later versions.
  - It doesn't reset automatically each day.
  - There is no per-option availability yet.
- **Pause:**
  - Pause sets an end time (1 minute to 12 hours) and ends by itself.
  - Every cart check reads the database with no cache, so a pause applies to the next cart.
- **Capacity:**
  - An atomic per-tenant counter for each 15-minute slot, counted at order time. Slots are aligned to the epoch, which gives quarter hours in Pakistan time.
  - P7 claims a place inside the order transaction, so concurrent orders can't overshoot the cap.
  - Releasing a place on cancellation is left to P7.
- **Pricing:**
  - Unit price = item + chosen options.
  - Delivery adds the flat fee, and the delivery minimum applies to the item subtotal.
  - Tax is the payment method's rate in basis points of the item subtotal, rounded half up.
  - A method with no rate set **can't be quoted**, rather than being silently untaxed.
  - Whether tax also applies to the delivery fee, and the rates themselves, stay **PENDING EXTERNAL** (B3).
- **Settings row:** one per tenant, created with database defaults on first use. Defaults: delivery and takeaway on, fee 0, pickup 20–240 minutes, COD and transfer on, transfer window 30 minutes, no COD limit, no capacity cap, tax not set. Per-merchant values are set with each merchant (PENDING EXTERNAL).
- **Not gated:** going live doesn't require a published menu. The ordering gate refuses carts without one.
- **Evidence:** PR #5. CI run 39598901486 (491 tests).

### AD-29: P5 implementation choices (provider-neutral messaging core)
- **Scope:** only the provider-neutral track that AD-25 allows. The OpenWA adapter, its webhook endpoint, OpenWA-based detection and the reconciliation job (P5-T2, the OpenWA parts of T3 and T5, and T6) are **not built**. They stay blocked until the Go (V-01).
- **Cross-tenant work discovery (resolves the AD-26 P5 finding):**
  - A new login role, `velora_dispatcher`, is NOBYPASSRLS, NOINHERIT and owns nothing.
  - Through column grants and SELECT-only `dispatch_lookup` policies, it can read only `tenant_id` and the queue-timing columns of `inbox_events`, `outbox_messages` and `channels`. It can't read payloads, session references or any other table, and it can't write.
  - It answers one question: which tenants have work due? The worker then does the work as `app_rw` inside that tenant's RLS scope.
  - No SECURITY DEFINER function was added. The resolver is still the only one.
  - *Trade-off:* one more database login to manage (`VELORA_DISPATCHER_DATABASE_URL`).
- **Provider interface:** `WhatsAppProvider` (an abstract class) has `send_text`, `send_media`, `get_session_status`, `start_session`, `get_qr`, `logout`, `capabilities` and `parse_webhook`.
  - `parse_webhook` verifies the raw body and maps it to the canonical events `InboundMessage`, `DeliveryStatus` and `SessionState`.
  - The adapter computes each event's dedup key, which keeps V-05 open: a later decision changes only the adapter.
  - `capabilities().idempotent_send` says whether a send whose outcome is unknown may be retried with the same key (V-06, PC-6).
- **Inbox:**
  - Ingress stores verified events and returns only after the commit. The endpoint will acknowledge only then.
  - Group, status and broadcast messages are dropped before any write.
  - Processing is leased (2 minutes) with a claim token. The handler's writes and "done" commit in one transaction, guarded by the token, so a worker that lost its lease can't commit.
  - Events from one customer on one channel are processed one at a time, in arrival order (AD-20).
  - Backoff is 10 s, 30 s, 2 m, 5 m and 15 m, then `failed`.
  - Only event types with a handler are claimed. Until P6, inbound customer messages stay stored and pending.
- **Outbox:**
  - New states are `waiting_for_channel`, `delivered`, `read` and `unknown`.
  - Messages to one recipient on one channel go out in order. A later message never overtakes one waiting to retry.
  - Conversational replies go stale 30 minutes after they are queued. Each affected recipient gets one placeholder "we're back" message, whose wording is for P6. Status updates never go stale.
  - After a timeout, or a lease that ended mid-send, a message is retried only if it is not critical **and** the provider guarantees idempotent sends. Otherwise it becomes `unknown` and is never re-sent automatically.
  - The rate cap defaults to 20 sends per channel per minute. It is a safety limit only, with no pacing or warm-up behaviour (AD-24; PC-5 is still the owner's decision).
- **Channel states:** pending, connected, reconnecting, needs_qr, logged_out, failed, suspected_restricted and retired.
  - The old P2 values were mapped: connecting and disconnected became reconnecting, paused became failed, and banned became suspected_restricted.
  - The last observation wins, but a report older than the current state is ignored and a retired channel never changes.
  - An unreachable provider counts as `failed`.
  - *Trade-off:* `suspected_restricted` isn't sticky. If the provider later reports `connected`, orders reopen without a human review. Making it sticky would need an operator action to clear it.
- **Circuit breaker:** the ordering gate needs at least one `connected` channel, and reads it with no cache. The health poll runs every 60 seconds. Session events and send errors trip the breaker immediately.
- **Global pause:** an append-only global table, `global_pause_events`, where the newest row wins. Only the founder's tool can write it (`global-pause --reason`, `global-resume`), and the runtime can only read it. It is the first check in the ordering gate.
- **Log redaction:**
  - The redacting formatter scrubs secret key/value pairs, bearer tokens, URL passwords, magic-link fragments and registered secrets.
  - It also masks phone numbers and WhatsApp ids down to their last three digits.
  - Error text stored in the queues is redacted and capped at 200 characters.
- **Provider boundary:** a static import-graph check in CI. Only `velora.messaging.registry` and `velora.workers.__main__` may reach `velora.messaging.providers`. `fake` is registered only in development and test, and `meta_cloud` is never registered.
- **Evidence:** PR #6. CI run 39604566511 (580 tests).

### AD-30: P6 implementation choices (conversation workflows) and Groq as the AI adapter
- **Status:** ADOPTED (delegated), awaiting owner review. **One part needs an owner decision:** whether Groq's *free* tier may receive real customer messages (see "Groq and AD-04").
- **Engine:**
  - The conversation engine is the inbox handler for `message` events. The state change, the opt-out and the queued replies commit with the event, exactly once.
  - Each message takes a transaction-scoped advisory lock on (channel, customer) and locks the conversation row. The customer upsert also locks the customer row.
  - States: `idle`, `browsing`, `category`, `choosing`, `proposal`, `fulfilment`, `address`, `delivery_note`, `pickup_time`, `payment`, `summary`, `handoff`. A CHECK constraint guards them (migration 0008).
  - The cart in `conversations.context` holds SKUs, quantities and option names only, never amounts. Every amount shown is re-priced at that moment.
- **Commands** (case-insensitive): `0` / agent / insaan / staff (handoff), CANCEL, status, MENU (or a short greeting), CART, CHECKOUT, YES, `remove N`.
  - **Change from the Appendix A draft:** `0` always means staff, so "0. Back to menu" became MENU. Cart and checkout are words (CART, CHECKOUT), not 8 and 9, so they can't collide with category numbers.
- **STOP (AD-24):** only a message that is exactly "STOP" (any case, surrounding spaces ignored) opts out. It is honoured in every state, including handoff.
  - Conversational replies still queued for that customer are cancelled.
  - While opted out, only START is answered. START replies with the disclosure and the menu.
  - CANCEL clears the cart and never opts out.
- **Disclosure:** prefixed to the first reply of a conversation (no outbound message yet), and again after START.
- **Handoff:** triggered by 0 / agent / insaan / staff / "insaan se baat…", or by two messages in a row the bot can't understand. Media and voice notes don't count as not understood.
  - While handed over, the bot sends nothing except the STOP confirmation.
  - Resume bot needs `CHATS_HANDLE` and is audited (`conversation.bot_resumed`). It keeps the cart and drops any half-done checkout or proposal.
  - The service and API exist (`GET /api/conversations/handoffs`, `POST /api/conversations/{id}/resume`). The inbox screen is P9.
- **Menus:**
  - Numbered text for every provider. The provider interface has no list or button messages.
  - A reply is split on line breaks at `min(capabilities().max_text_length, 1000)` characters. **The 1000 is provisional until V-09.**
- **Gate:** checked when the first item goes into an empty cart, and again at checkout. A closed gate gives the A4 wording.
- **Checkout:**
  - fulfilment (asked only if both delivery and takeaway are on);
  - for delivery: an address of at least 10 characters or a location pin, then an optional note;
  - for takeaway: up to five pickup slots from the first whole 5 minutes after the lead time, or a typed time ("8:30 pm", "9 baje", "30 min"), checked with `check_pickup_time`;
  - payment: only methods `price_cart` can quote (an unset tax rate removes that method, B3) and that pass the COD first-order limit;
  - then the templated summary.
  - The quote (menu version, total, expiry in 10 minutes) is stored for P7.
  - YES re-prices first. An expired or changed summary is shown again (A7). Otherwise the customer is told ordering isn't switched on yet: **order creation is P7.**
- **AI proposals (P6-T4):**
  - Behind a `Proposer` interface (`velora.ai.proposer`). Only `velora.ai.registry`, `velora.ai.evaluate` and the worker entry point may import the adapter; a static check enforces this.
  - The model sees only the minimised text (long digit runs, emails and links masked; at most 500 characters) and the offerable menu's SKUs, names, categories and option names. It never sees prices, tenant, channel, customer, phone or address, and it has no tools.
  - Output is parsed strictly: intent from a fixed set, at most 10 lines, quantity 1–50, everything else dropped.
  - Each line is resolved against the published menu and availability. Unknown SKUs are dropped and unavailable ones reported. A line with a missing or invented option gets a fixed note with the database name only, so model text never reaches the customer.
  - The customer must reply YES to add a proposal to the cart.
  - A timeout (`VELORA_AI_TIMEOUT_SECONDS`, default 8) or any error shows the numbered menu.
  - Switchable per tenant (`ordering_settings.ai_enabled`, default on). With no `VELORA_GROQ_API_KEY`, there is no AI at all.
  - *Trade-off:* the AI call runs inside the inbox transaction, holding the chat's lock for up to the timeout. That is acceptable at pilot volume.
- **Groq and AD-04** (owner asked on 2026-10-21 to "consider GROQ as free llm"):
  - Groq is the first adapter. It uses the OpenAI-compatible chat completions API, JSON mode, temperature 0, default model `openai/gpt-oss-20b` (`VELORA_AI_MODEL`), and reasoning effort low for gpt-oss.
  - It makes HTTP calls through the standard library, so there is no new dependency.
  - **What we checked (2026-10-21):** Groq's "Your data" page says inference inputs and outputs are not retained by default, except reliability and abuse logs kept up to 30 days. Zero Data Retention can be switched on by any customer in Data Controls, and the page draws no free/paid distinction. **The page does not say whether Groq trains on customer data, and Groq's terms were not reviewed.** The Developer-plan base limit for gpt-oss-20b is 30 requests/min, 1K/day and 8K tokens/min. Free-plan limits are shown only in the account console and were not verified.
  - **Conflict:** AD-04 (APPROVED) requires a *paid* tier whose terms exclude training. The free tier fails the "paid" part, and the training part is unverified for any tier.
  - **Adopted for now:** the free tier may be used for development and for the offline evaluation with synthetic data (the A15 set). **Real customer messages must not go to Groq** until the owner amends AD-04 or a tier with verified no-training terms and ZDR is used.
  - Before the pilot: turn on ZDR, verify the training terms, and check that the free limits cover the pilot (1K requests/day is roughly 1K free-text messages).
- **Offline evaluation (P6-T7):** `python -m velora.ai.evaluate evaluation/synthetic-a15.json`. It scores intent plus exact lines, regardless of order or case. The 90% target is an estimate, not a CI gate, and the real P0-F6 dataset is still needed.
- **Findings for later phases:**
  - **P5-T2:** the canonical `InboundMessage` has no location coordinates. A pin is stored as "📍 Shared location" plus its provider message id.
  - **P7:**
    - Replace the "not switched on yet" reply with the nonce and the order transaction.
    - Compare against `context.quote` (menu version and total).
    - Implement CANCEL for placed orders.
    - Decide whether order status updates go to opted-out customers.
  - **P9:** show the handoff inbox and Resume bot, and the `ai_enabled` switch (already accepted by `PATCH /api/settings/ordering`).
  - **P11:** retention must also cover `conversations.context`, which holds delivery addresses.
- **Evidence:** PR #7. CI run 39613089570 (652 tests). Migration 0008; tests in `backend/tests/conversation/`.

### AD-31: P7 implementation choices (order lifecycle)
- **Status:** ADOPTED (delegated), awaiting owner review. Built against the `FakeProvider` under AD-25.
- **The YES nonce:**
  - Showing the templated summary issues a `pending_actions` row: a random nonce, of which only the SHA-256 is stored.
  - It is bound to the tenant (RLS), the conversation, the customer, and a hash of the exact checkout: lines, options, fulfilment, payment method, address, note and pickup time. It expires with the summary (10 minutes).
  - A new summary voids the previous nonce (`superseded`), so at most one is open per chat. A unique index enforces this.
  - Nonce expiry needs no timer: an expired nonce is refused when it is used.
- **The order transaction:** YES runs these steps in a savepoint of the inbox transaction:
  1. the nonce check;
  2. the gate (global pause, restaurant status, channel, pause, menu, capacity);
  3. re-pricing with `price_cart`, compared field by field with the summary's snapshot (menu version, every line, every amount);
  4. single-use consumption of the nonce;
  5. the capacity claim, the order number, the order with its snapshots, its lines (with chosen options) and its first transition;
  6. the confirmation, queued in the outbox as a critical status update.
  - If any step refuses or fails, nothing is kept.
    - An expired or changed summary gives the customer the new summary to confirm (A7).
    - A finished item gives the cart without it, then CHECKOUT again.
    - A closed restaurant gives the A4 wording.
    - An unexpected error gives an apology, and the same YES can be retried.
  - For takeaway, the pickup lead time is counted from when the summary was shown, so a slot that was valid then is still accepted. The time must still be in the future.
- **States and the transition table** (`velora.orders.lifecycle.TRANSITIONS`; migration 0009 enforces the same pairs in a trigger, and a test checks that the two agree):
  - The customer's YES creates `cod_confirmed` (cash) or `awaiting_transfer`.
    - `draft` and `awaiting_confirmation` stay in the schema but are unused: the summary and its nonce are the confirmation step.
  - Staff: `cod_confirmed` → `accepted` / `rejected` / `cancelled`; `paid_staff_verified` → `accepted` / `refund_pending`; `accepted` → `preparing`; `preparing` → `out_for_delivery` (delivery) or `ready` (takeaway); either → `completed`; `refund_pending` → `cancelled`.
  - After acceptance, a cash order can be `cancelled`, but a transfer order goes to `refund_pending`.
  - Customer: `cancelled` from `cod_confirmed` or `awaiting_transfer` only, which is before acceptance and before verified payment.
  - System: `awaiting_transfer` → `expired` when the transfer window passes.
  - `awaiting_transfer` → `paid_staff_verified` is in the table, but the generic staff action refuses it. P8 sets it, with the transaction reference and the PIN.
  - `expired` and `refund_pending` have no further customer moves. A late payment after expiry is P8's staff review.
- **Guards:**
  - Every move is `UPDATE … WHERE status = <the status read>`. Staff also send the status they saw, so a double tap or a second device gets 409.
  - Every move writes an `order_transitions` row (now with a `seq` for ordering) and an `audit_log` row (`order.status_changed`). Creation writes `order.placed`.
  - An order's amounts and snapshot columns can't change after creation (trigger). `order_items` are append-only.
  - Staff must give a reason (200 characters at most) to reject, cancel or refund. The customer sees it.
- **CANCEL:** while a cart, proposal or option choice is being built, CANCEL clears it, and the customer is told if an order is still open. With nothing being built, CANCEL cancels the latest open order, if the customer may cancel it. Otherwise the customer is told why and pointed to staff. CANCEL never opts out (AD-24).
- **Capacity:** an order that ends unserved (rejected, cancelled, expired) before the kitchen starts gives its place back, but only if its slot is still the current one. Past slots keep their counts as history.
- **Possible duplicates:** the same customer and cart hash within 10 minutes sets `possible_duplicate` for staff. Nothing is cancelled automatically.
- **Status messages:**
  - They are queued in the same transaction as the move, with purpose `status_update`, so they never go stale (A8, A9 wording). `preparing` has no message, because "accepted — being prepared" already says it.
  - **Opted-out customers get no status messages.** STOP means no more messages from the assistant. The order is unaffected, and the customer can send START, then "status".
- **Timers:** `OrderTimers` expires due transfer orders in the worker round, before the outbox, so the notice goes out in the same round.
  - `velora_dispatcher` may now also read `orders(tenant_id, status, payment_due_at)` to find tenants with a window due.
  - `payment_due_at` is a snapshot taken at placement (the transfer window then). P11 recovery may extend it.
- **Recovery hook (P7-T7):** `velora.orders.recovery.expire_pre_outage` voids every open nonce created before the outage. Customers whose summary was still valid when the outage began get the A13 "confirm again" message. P11 calls it from recovery mode.
- **Staff API:** `GET /api/orders` (active orders, or all), `GET /api/orders/{id}` (lines, history, allowed next moves) and `POST /api/orders/{id}/status` (`{from, to, reason}`). Viewing needs `ORDERS_VIEW`, moving needs `ORDERS_UPDATE`, which includes the shared kitchen device. The screens are P9.
- **Transfer orders before P8:** the confirmation says the restaurant's account details "will follow here". Sending them is P8-T3, so transfer orders can't be used for real until P8.
- **Findings for later phases:**
  - **P8:**
    - Verification sets `paid_staff_verified` (through `lifecycle.transition` with a staff actor, after the reference and PIN checks).
    - Send the account details right after an `awaiting_transfer` order is placed.
    - Decide late payments after `expired`.
    - Decide whether `refund_pending` → `cancelled` should become a `refunded` state.
    - The COD first-order limit counts any earlier order, including cancelled ones.
  - **P9:** show `possible_duplicate`, the allowed next moves and the history. Show orders created during recovery (P11 step 9).
  - **P11:**
    - Recovery mode calls `expire_pre_outage`, and extends `payment_due_at` by the outage length (step 5).
    - Retention must also cover `pending_actions.payload`, which holds the address.
- **Evidence:** PR (P7). Migration 0009; tests in `backend/tests/orders/` and `backend/tests/api/test_orders.py`.

---

## 3. VALIDATION-DEPENDENT decisions (decided only after the OpenWA trial)

| ID | Open decision | Trial evidence |
|---|---|---|
| V-01 | OpenWA Go / No-Go | Gates G1–G5 plus zero restrictions |
| V-02 | Engine: whatsapp-web.js or Baileys | G4, G5 |
| V-03 | VPS size and final cost | G5 (RAM per session) |
| V-04 | Inbound recovery: (a) OpenWA retries plus history; (b) mandatory reconciliation job; (c) highly available ingress plus accepted loss, or reconsider the provider | G1, G2 |
| V-05 | Deduplication key: provider message ID, or a fallback composite key | T1.5, T2.1 |
| V-06 | How to look up the outcome of an outbound send after a timeout | T4.2, T4.3 |
| V-07 | Session encryption approach, and whether session state is backed up (default: not backed up off-site) | T3.1, T3.3 |
| V-08 | Webhook replay protection | T1.4 |
| V-09 | Menu formatting and length limits | T4.4 |
| V-10 | Disconnect threshold for the stop rule | T5.2 |

---

## 4. PENDING EXTERNAL

| Item | Needed before | Who |
|---|---|---|
| Legal opinion on WhatsApp Terms-of-Service exposure, merchant consent and indemnity wording, data handling, and controller/processor role | Connecting any real merchant | Lawyer |
| Tax per payment method (PRA/SRB) and invoice requirements | Paid launch (and correct totals in the pilot) | Accountant |
| Merchant acceptance of a separate number; preference for a kitchen screen; pricing | Pilot | Merchant interviews |
| AI model choice, from a Roman-Urdu accuracy evaluation | P6 build | Engineering |
| Vendor prices: VPS, Postgres, storage, LLM, monitoring | Committing to infrastructure | Engineering |
| COD limit and transfer window for each restaurant | Onboarding each restaurant | Owner and merchant |
| Online payment provider (signed webhooks, status API, merchant KYC, fees after the subsidy) | Adding online payments (post-MVP) | Sandbox tests |

---

## 5. NOT APPROVED / OPTIONAL

- **Customer order-tracking web link:** a page showing an order's status and receipt, expiring about 24 hours after completion. Proposed but **not approved**. Not in MVP scope.

## 6. Cost estimates (not verified; recheck before committing)

**Vendor price sheet, checked 2026-10-21 (P0-E4).**
- Nothing has been purchased.
- **VERIFIED** = read on the vendor's official pricing page, documentation or API that day. **UNVERIFIED** = third-party source only.
- Prices exclude tax.
- Hetzner prices are in EUR. USD figures for Hetzner are rough conversions, and the exchange rate itself wasn't verified.
- Prices change often: **recheck before buying.**

### 6.1 VPS (pilot needs 4–8 GB RAM; final size depends on V-03)
| Option | Price/mo | Specs | Nearest region to PK | Caveats | Status |
|---|---|---|---|---|---|
| Hetzner CX23 / CX33 | €5.99 / €8.99 | 2 vCPU/4 GB; 4 vCPU/8 GB | **EU only** | All plans were marked **"currently unavailable"** on 2026-10-21. Prices rose 15 Jun 2026. Its Singapore plans (CPX) cost about €26.99–49.49. | VERIFIED ([pricing](https://www.hetzner.com/cloud/cost-optimized), [changelog](https://docs.hetzner.cloud/whats-new)) |
| Vultr vc2 4 GB / 8 GB | $20 / $40 | 2 vCPU/80 GB; 4 vCPU/160 GB | Mumbai, Bangalore, Singapore | Older CPU generation | VERIFIED via official API |
| DigitalOcean Basic 4 GiB / 8 GiB | $24 / $48 | 2 vCPU; 4 vCPU | Bangalore, Singapore | Daily backups cost +30% | VERIFIED ([pricing](https://www.digitalocean.com/pricing/droplets)) |
| Akamai/Linode 4 GB / 8 GB | $24 / $48 (base price) | 2 vCPU; 4 vCPU | Mumbai, Chennai, Singapore | Regional prices not shown | VERIFIED (base price only) |
| Contabo Cloud VPS 4 | from €4.40 (24-month term) | 4 vCPU/8 GB | Singapore, India | Monthly price, setup fee and region surcharge not confirmed | Partially verified |

### 6.2 Managed PostgreSQL with backups (AD-04)
| Option | Price/mo | Backups | Region | Caveats | Status |
|---|---|---|---|---|---|
| Supabase Pro | from $25 (Micro compute included; Small is +$15) | Daily, kept 7 days. PITR costs +$100/mo | Mumbai, Singapore | Micro (1 GB, shared CPU) may be too small | VERIFIED ([pricing](https://supabase.com/pricing)) |
| Supabase Free | $0 | **None** | — | **Pauses after 1 week** of inactivity, so unusable for production (F8) | VERIFIED |
| DigitalOcean Managed PG | $15.15 (1 GiB) / $30.45 (2 GiB) | Daily + 7-day PITR listed as features. The pricing page doesn't say whether backups cost extra | Bangalore, Singapore | Limits on new clusters take effect 15 Oct / 30 Nov 2026 | VERIFIED ([pricing](https://www.digitalocean.com/pricing/managed-databases)) |
| Neon Launch | Usage-based. My estimate: about $19 always-on at 0.25 CU | Up to 7 days of history | Singapore (no Mumbai) | Scales to zero unless that's disabled | VERIFIED (estimate is ours) |
| Aiven Hobbyist | from $12 | Single disaster-recovery backup only | Singapore, Bangalore | Retention too weak for AD-21 | VERIFIED |

### 6.3 Off-site storage for hourly encrypted dumps (AD-21)
| Option | Price | Free tier | Caveats | Status |
|---|---|---|---|---|
| Cloudflare R2 | $0.015/GB-month | 10 GB-month | No egress fees | VERIFIED ([pricing](https://developers.cloudflare.com/r2/pricing/)) |
| Backblaze B2 | $6.95/TB-month | First 10 GB | Free egress up to 3× stored data | VERIFIED ([pricing](https://www.backblaze.com/cloud-storage/pricing)) |
| DigitalOcean Spaces | $5/mo (250 GiB) | — | — | VERIFIED |
| Wasabi | $7.99/TB-month | — | **Billed for at least 1 TB, 90-day minimum.** Poor fit | VERIFIED |

A few GB of dumps is probably **$0** on R2 or B2, depending on how long dumps are kept.

### 6.4 Monitoring and alerting (P10)
| Option | Free tier | Paid | Caveats | Status |
|---|---|---|---|---|
| UptimeRobot | 50 monitors, checks every 5 minutes | Solo $10/mo (checks every 60 seconds) | The free tier is described as for **"hobby and non-profit"** use, so commercial use likely needs a paid plan | VERIFIED ([pricing](https://uptimerobot.com/pricing/)) |
| Healthchecks.io | 20 checks | Business $20/mo (includes SMS/WhatsApp credits) | Good for monitoring backup and cron jobs | VERIFIED |
| Sentry | 5k errors/mo, 1 user | Team $29/mo | Free tier has no integrations | VERIFIED |
| Better Stack | 10 monitors, 3 GB logs | Responder $34/mo | Free check interval not stated | VERIFIED |

### 6.5 LLM APIs (paid tiers, no training on API data; AD-04)
**Estimated usage:** 5k–15k calls a month, at 1.5k–3k input tokens and 200–400 output tokens per call. That's 7.5–45M input tokens and 1–6M output tokens a month.

| Model | Price per 1M tokens (in / out) | Est. $/mo | Trains on paid API data? | Status |
|---|---|---|---|---|
| Claude Haiku 5.5 | $0.10 / $0.50 | ~$1.3–10 | No by default | VERIFIED ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) |
| OpenAI gpt-4o-mini | $0.15 / $0.60 | ~$1.7–10.4 | No by default (30-day abuse logs) | VERIFIED ([pricing](https://developers.openai.com/api/docs/pricing)) |
| Gemini 3.1 Flash-Lite | $0.25 / $1.50 | ~$3.4–20.3 | Paid: no. **Free: yes** | VERIFIED ([pricing](https://ai.google.dev/gemini-api/docs/pricing)) |
| Groq gpt-oss-120b / -20b | $0.15 / $0.60; $0.075 / $0.30 | ~$0.9–10.4 | **Training policy not stated** (retention is documented) | Price VERIFIED; policy not found |
| Groq llama-3.3-70b-versatile | Officially "Contact sales (Enterprise)" | — | — | **No longer self-serve**, which affects the research's stack ([`06`](../research/06-technology-stack-and-cost-analysis.md)) |

**Notes:**
- Reasoning models bill their thinking tokens as output, so costs can rise unless reasoning effort is kept minimal.
- The final model is chosen by the Roman-Urdu accuracy evaluation (P0-E5), not by price alone.

### 6.6 Pilot monthly totals (estimates, not commitments)
| Scenario | Composition (example) | Estimate |
|---|---|---|
| Low | Small EU VPS (if Hetzner is available) + DO PG 1 GiB or Supabase Pro + R2/B2 free + free monitoring + cheap LLM | **~$27–45** |
| Typical | 8 GB VPS in Mumbai/Bangalore/Singapore (~$40) + Supabase Pro ($25) + R2/B2 (~$0) + UptimeRobot Solo ($10) + LLM ($3–20) | **~$50–80** |
| High | DO 8 GB + backups + DO PG 2 GiB + Spaces + paid monitoring + larger LLM | **~$190–200** |

**Other costs:**
- **Test SIM:** small one-off. Price not verified.
- **Lawyer and accountant:** unknown. Quotes needed (AD-08).

### 6.7 Uncertainties and recommendations from the price check (proposals, not decisions)
1. **Hosting region (proposal):** keep the VPS and database **in the same region**, preferably Mumbai/Bangalore or Singapore. That's likely closer to Pakistan than the EU, though **latency hasn't been measured**. Hetzner (EU-only for the cheap plans, and currently unavailable) is not the cheap default the research assumed.
2. **UptimeRobot:** budget for its paid tier, since the free tier's terms suggest it isn't for commercial use.
3. **Groq Llama 3.3 70B** is enterprise-only now. **Gemini 2.5 Flash-Lite** is closed to new users. **Gemini 3.8 Flash** doubles in price on 1 Jan 2027. Model choice must be rechecked when the evaluation runs.
4. **Unverified:**
   - Groq's training policy;
   - whether DigitalOcean charges extra for database backups;
   - Contabo's monthly pricing;
   - Akamai's regional prices;
   - AWS RDS prices;
   - Pakistan tax on foreign digital services.
5. **Budget estimate:** the earlier ~$40–75/mo figure still sits inside the new "typical" range (~$50–80). Plan around **~$50–80/mo** until V-03 sets the VPS size.

---

## 7. REJECTED

| Rejected item | Research source | Reason |
|---|---|---|
| OpenWA detection-evasion features (proxy per session, simulated typing, warm-up to avoid detection) | [`01`](../research/01-executive-summary-and-feasibility.md) §5; [`05`](../research/05-open-wa-gateway-evaluation.md) §4.1 | Platform-evasion; not supported |
| Marking an order paid from AI output, customer text or a screenshot (incl. "screenshot OCR, then staff approval sets Paid" without a recorded reference) | [`02`](../research/02-prd-requirements-analysis.md) §12.1; [`12`](../research/12-pakistan-payments-and-localization-v2.md) §2.1.5 | Fraud risk; replaced by AD-03 |
| Free tiers for production data; "$0 MVP" | [`06`](../research/06-technology-stack-and-cost-analysis.md); [`17`](../research/17-testing-deployment-and-operations.md) §8 | No backups (F8) |
| Gemini 1.5 Flash | 06, 07, 09, 17 | Retired (F9) |
| Redis SETNX before processing with a 24h TTL; `BackgroundTasks` for durable work | [`07`](../research/07-system-architecture.md) §2; 17 §2.1 | Messages can be lost; shorter than the 7-day retry window (F3) |
| LLM write tools; tenant, phone or confirmation token supplied by the LLM | 07 §4.1; [`09`](../research/09-ai-workflows-and-reliability.md) §2.1 | Prompt injection leads to cross-tenant or privileged writes |
| Global unique SKU / order number; SECURITY DEFINER functions with caller tenant; `service_role` at runtime | [`08`](../research/08-database-and-multi-tenancy.md) §4–6 | Breaks tenant isolation |
| Reservation SQL that resets `reserved_quantity = 0`; holds at add-to-cart | 08 §5; 09 §2.1 | Wipes other customers' holds; no ledger |
| Hotel room service / PIN in the MVP | 02 §7; 10 §5 | No data model (deferred, see roadmap) |
| Multi-PSP failover; "SBP webhook"; `X-Hub-Signature-256` for payment providers | 12 §2, §5 | False or unverified |
| Kitchen WhatsApp groups with buttons | 02 FR7.2; 10 §1 | Not supported (F2, F7) |
| OCR auto-commit at ≥85% confidence; sentiment < 0.65; "CANCEL" as the opt-out keyword | 17 §2.3; 09 §6; [`15`](../research/15-legal-risks-and-whatsapp-policy-compliance.md) §1.1 | Contradictions or undefined; collides with order cancellation |
| Fake `AggregateRating`, "the only" claims, Reddit "authority seeding" | [`13`](../research/13-seo-geo-customer-acquisition.md); [`04`](../research/04-competitor-analysis.md) §5 | Deceptive marketing |
| Stock Flow | Multiple | AD-09 |

---

## 8. Items for owner review

1. **Every ADOPTED (delegated) decision (AD-11 to AD-24, AD-26 to AD-31).** They stand unless you reverse them.
   - **AD-30 needs a decision:** may Groq's free tier receive real customer messages? That would amend AD-04 (paid tier, no training), and Groq's training terms are unverified.
   - **AD-31 asks you to confirm:** opted-out customers get no order status messages; a customer can cancel only before acceptance and before a verified payment.
2. **The optional customer order-tracking link (§5).** Not approved.
3. **The validation-dependent decisions V-01 to V-10.** These come back to you with the trial evidence.
4. **The cost commitment.** Approve only after vendor checks and V-03.
5. **The proposed changes in §9.**

---

## 9. Proposed changes from P0 desk research (2026-10-21), for owner review

These come from the OpenWA desk review ([validation plan §10](../planning/openwa-validation-plan.md)) and the price sheet (§6).

**Status: PROPOSED.**
- None of the existing decisions above has been changed.
- Desk evidence does **not** settle any V-decision. Each one stays VALIDATION-DEPENDENT until live tests confirm it.

| ID | Affects | Proposal | Basis | Status |
|---|---|---|---|---|
| PC-1 | V-04 (recovering inbound messages) | Treat option **(b)** as the likely design: rely on OpenWA's durable webhook outbox and retries, plus a **scheduled VELORA reconciliation job** that reads `GET /api/sessions/:id/messages?since=` after any receiver outage and periodically. | OpenWA stores inbound messages and offers a `since` query on both engines (§10.2). Default retries end within about 15 s (§10.1). | PROPOSED; live test G1/G2 |
| PC-2 | V-05 (deduplication key) | Deduplicate on **(session, `waMessageId`)**, and also record `X-OpenWA-Idempotency-Key`. | Unique index in OpenWA; idempotency header stable across retries (§10.1–10.2) | PROPOSED; live test T1.5 |
| PC-3 | V-08 (webhook replay protection) | Use HMAC on the raw body + private network + idempotent processing. **No strict timestamp window**, because it would reject legitimate retries. | No timestamp header; timestamp only inside the body (§10.1) | PROPOSED |
| PC-4 | AD-24 (security baseline) | Add OpenWA hardening: encrypted volume (mandatory, since OpenWA stores everything in plaintext); `API_KEY_PEPPER` set before first boot; `SERVE_DASHBOARD=false`; one scoped operator key per session (never the admin key in the app); first-boot logs scrubbed; `WEBHOOK_SSRF_PROTECT` left on, with only the specific internal receiver allowed. | §10.3 | PROPOSED (strengthens AD-24) |
| PC-5 | AD-24 (sending behaviour) | Keep VELORA's own outbox rate cap as the sending control. **Leave OpenWA's `SEND_PACING` (which includes a "warm-up schedule") off unless the owner decides otherwise.** Its warm-up behaviour sits close to the ban-avoidance tactics AD-24 rules out, so it needs an explicit owner decision. | §10.4 | OWNER DECISION |
| PC-6 | V-06 (uncertain send outcomes) | Pass the outbox idempotency key as OpenWA's `Idempotency-Key` on every send. Treat 201 as "accepted" and track `message.ack` / `message.failed` for delivery. | §10.4 | PROPOSED; live test T4.3 (key lifetime unknown) |
| PC-7 | AD-24 data minimisation; retention | Set OpenWA's `MESSAGE_RETENTION_DAYS` to a finite value (suggested 30, final value after the legal brief) and filter group, status and broadcast events at the webhook. **OpenWA's own database stays in security scope**, because it holds message content. | Default is to keep everything forever (§10.2) | PROPOSED; legal input |
| PC-8 | V-07 (session backups) | Confirms the default: **don't back up the OpenWA volume off-site** (plaintext auth). Accept re-linking every session after a host loss, and document it in the P11 runbook. | §10.3 | PROPOSED |
| PC-9 | AD-19 (hosting) | Choose a VPS and database **in the same region**, preferably Mumbai/Bangalore or Singapore. Hetzner's cheap plans are EU-only and were unavailable on 2026-10-21. | §6.1, §6.7 | PROPOSED; latency not measured |
| PC-10 | AD-04 (AI model) | Candidates for the P0-E5 evaluation: Claude Haiku 5.5, gpt-4o-mini, Gemini 3.1 Flash-Lite, and Groq gpt-oss (training policy to verify). Drop Groq Llama 3.3 70B, which is now enterprise-only. | §6.5 | PROPOSED. Groq gpt-oss is the first adapter built (AD-30); the selection still needs the evaluation |
| PC-11 | P10 (monitoring) | Budget for a paid uptime-monitoring tier (e.g. UptimeRobot Solo, $10/mo). The free tier is described as for hobby/non-profit use. | §6.4 | PROPOSED |
| PC-12 | Validation plan | Add test T3.9 (passkey linking block, #560), the reconciliation, ID and send-idempotency tests, and the trial configuration baseline. | Validation plan §10.6 | PROPOSED |
| PC-13 | Risk register | Record #560 (a passkey can block all device linking, no workaround) and #830 (first message to a new contact can silently fail) as **pilot risks**. #560 could also block re-linking after a disconnect. | §10.4–10.5 | PROPOSED |
