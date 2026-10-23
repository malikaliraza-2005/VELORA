# VELORA architecture decisions

- **Last updated:** 2026-10-23
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
- **Amended by AD-35 (owner, 2026-10-22): zero cost.** A free VM with PostgreSQL on it and our own off-site backups replaces the paid managed database. Groq's free tier replaces the paid AI tier.

### AD-05: OpenWA is the intended MVP WhatsApp provider, behind a replaceable interface
- **Decision:**
  - OpenWA is the *intended* integration for the MVP. The app reaches it only through a `WhatsAppProvider` interface. Orders, payments, the database and the AI must not depend on OpenWA.
  - **The five validation gates G1–G5 are mandatory** ([`../planning/openwa-validation-plan.md`](../planning/openwa-validation-plan.md)).
  - Until they pass, webhook retries, history recovery, session encryption, first-message delivery and resource needs are **assumed not to work**.
  - **OpenWA is not claimed to be compliant, reliable or production-ready.**
- **Approved:** owner decision on A7, plus the conditional approval (items 1, 3 and 7).
- **Rationale (owner):** no Meta per-message fees (F1) and fast onboarding by QR scan.
- **Risks the owner accepted** (the legal brief that was to re-check them was withdrawn by AD-35):
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
- **WITHDRAWN by the owner (AD-35, 2026-10-22):** no lawyer or accountant. The owner accepts the risks listed in AD-35.

### AD-09: VELORA has its own database; Stock Flow is excluded
- **Decision:** inventory and availability are an internal module of VELORA's own database.
- **Approved:** owner constraint.
- **Conflicts with research:** every "Stock Flow" reference in research docs 01, 02, 04, 06, 07, 08, 10, 12, 13 and 14 is superseded.

### AD-10: Research documents are inputs, not requirements
- **Approved:** owner constraint. See [`../README.md`](../README.md#precedence).

### AD-35: Zero-cost operation (amends AD-04, withdraws AD-08)
- **Decision (owner, 2026-10-22):** "I want total zero cost approach." In the follow-up questions the owner chose a free cloud VM, kept the AI, and dropped the lawyer and accountant, accepting the risk.
- **Approved:** owner, 2026-10-22.
- **What it means, part by part:**
  - **Server:** one always-on free cloud VM. Oracle Cloud "Always Free" is the candidate. *To verify before relying on it:* region (Mumbai or Hyderabad preferred), free capacity, the card check at signup (not charged), the terms for commercial use, and Oracle's policy of reclaiming idle free VMs.
  - **Database:** PostgreSQL on that VM, not a managed service. VELORA owns the backups: **hourly encrypted dumps to free off-site storage** (Backblaze B2's free 10 GB is the candidate), restore drills in P11, and backup alerts from the P10 monitor. The §7 rejection of free *managed* database tiers still stands (no backups, pauses), and this design doesn't use them.
  - **AI: Groq's free tier, for real customer messages.** This settles the open question in AD-30 in Groq's favour.
    - *Risks the owner accepts:* Groq's training terms are unverified; free-tier rate limits are unverified.
    - *Kept:* the minimised customer text; the numbered menu whenever the AI fails, times out or hits a limit; and the per-restaurant switch to turn the AI off.
    - *Recommended, free:* switch on Zero Data Retention in Groq's Data Controls.
    - *Private zero-cost alternative, if wanted later:* a small open model on the VM itself, so no customer text leaves our server. It is slower, and its Roman-Urdu accuracy is untested.
  - **Monitoring (P10):** alerts by email through a free mailbox (e.g. Gmail with an app password), and a free external heartbeat or uptime check. **PC-11 (a paid uptime tier) is rejected.** The free tiers' terms for commercial use must be checked when choosing one.
  - **Domain and HTTPS (P12):** a free subdomain with Let's Encrypt, or a free Cloudflare Tunnel.
  - **WhatsApp:** OpenWA, unchanged (AD-05).
- **AD-08 withdrawn.** No lawyer or accountant before connecting restaurants. **The owner accepts, without a professional opinion:**
  - the WhatsApp Terms-of-Service exposure of OpenWA (F6): numbers may be banned, and the owner agreed not to create replacement accounts without permission;
  - consent wording, merchant indemnity, data handling, and VELORA's role as data controller or processor;
  - tax per payment method and invoice rules. **The owner sets each restaurant's tax rate from public sources** (F10: Punjab 8% card/digital, 16% cash, as of 1 Jul 2026). Until a rate is set, VELORA refuses to quote (B3).
  - Engineering will draft the consent form from AD-18 and AD-24. **The draft is not legal advice.**
- **Engineering lead's note:** zero cost moves cost into risk:
  - a free VM has no service guarantee and may be reclaimed;
  - backups and recovery are ours alone;
  - customer messages go to a service whose training terms are unverified;
  - nobody has checked the legal position.

  The mitigations are the backups and restore drills (P11), the monitoring (P10), the stop rules (AD-22) and the global pause. **Not zero:** a one-off prepaid test SIM, and the owner's phone.
- **Budget:** $0 a month. Nothing has been purchased.

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
- **Evidence:** PR #9. CI run 39636189526 (702 tests). Migration 0009; tests in `backend/tests/orders/` and `backend/tests/api/test_orders.py`.

### AD-32: P8 implementation choices (payment verification)
- **Status:** ADOPTED (delegated), awaiting owner review. Built against the `FakeProvider` under AD-25. It implements AD-03 (approved) and AD-23.
- **The payments ledger (migration 0010):**
  - `payments` is append-only: `app_rw` has no UPDATE or DELETE, and a trigger refuses changes for everyone.
  - An entry is `verified` (staff saw the money arrive in the restaurant's own account) or `reversed` (the owner withdrew a verification). A reversal is a new row that names the entry it corrects and why. Each entry can be reversed once.
  - A verified entry records the amount (the order total), the reference as typed and its comparison key, the confirmer (the PIN holder), the session it was done on (e.g. the kitchen tablet), and whether it was late.
  - The old statuses `pending`, `customer_claimed`, `rejected`, `refund_pending` and `refunded` are dropped from the ledger. A customer's claim is evidence, not a payment. Refunds are tracked by the order status.
- **Only staff mark an order paid:**
  - **The database refuses `paid_staff_verified`** (and `expired` → `refund_pending`) unless the ledger holds a verified, unreversed entry for that order, even for a direct UPDATE.
  - The paid moves are made only by `velora.payments.service.PaymentService`, after `verify_payment_pin`. The generic staff move refuses them.
  - A writer-search test (P8-T7) checks that only reviewed code names the paid status, writes the ledger, or writes an order's status, and that every paid move checks a PIN first.
- **Verification (P8-T4):** `POST /api/orders/{id}/payment/verify` with `{reference, pin, confirmer_id?}`.
  - The session needs `ORDERS_UPDATE`, and the confirmer needs `PAYMENTS_CONFIRM` and a personal PIN. A shared kitchen device names the confirmer (AD-23).
  - Only an `awaiting_transfer` transfer order can be verified. These are refused **before** the PIN is checked, so no PIN attempt is used up: a bad reference, a wrong status, or another restaurant's order (404).
  - **References:** 1 to 64 characters, with at least 4 letters or digits. They are compared by a key of upper-case letters and digits only, so "ft 2026-0001" and "FT20260001" are the same.
  - **A duplicate reference is refused and flagged.** The reference must be unique among this restaurant's verified, unreversed entries. A duplicate gets 409, naming the order that already has it, and is audited (`payment.duplicate_reference`). Two simultaneous confirmations with one reference are serialised by an advisory lock.
  - **Amount:** the order total. Staff confirm that the full amount arrived. Partial payments are not modelled.
  - **Audit:** `payment.verified`, and the order transition names the confirmer.
- **Late payments (P8-T5):** `POST /api/orders/{id}/payment/late-review` with `decision` = `accept` or `refund`, the reference and a PIN. No other decision exists.
  - **accept:** the payment is verified (marked late), then `expired` → `paid_staff_verified` → `accepted`. The order does not re-take a capacity place; staff chose to accept it.
  - **refund:** the payment is verified, because the money did arrive, then `expired` → `refund_pending`, with the reason the customer sees. Staff refund manually from the restaurant's own account, then move `refund_pending` → `cancelled` with a note.
  - **No time limit for staff.** Customer evidence attaches to an expired order only for 24 hours.
  - **No `refunded` state** (P7 finding): `refund_pending` → `cancelled`, with the refund note as the reason, stays as is.
- **Owner controls (P8-T6):** two new owner-only permissions, `PAYMENTS_RECONCILE` and `PAYMENTS_REVERSE`. Managers don't have them; they are the check on everyone else.
  - **Reconciliation:** `GET /api/payments/reconciliation?day=YYYY-MM-DD` (a Pakistan date, today by default). It lists every transfer verified that day, with the confirmer, whether it was late, the order's current status and any reversal, plus totals (verified, reversed, net).
  - **Reversal:** `POST /api/payments/{id}/reverse` with the owner's PIN and a reason. It writes a `reversed` entry and is audited (`payment.reversed`).
    - An order still `paid_staff_verified` goes back to `awaiting_transfer` (a new transfer-only move), and the customer is told that the payment couldn't be matched yet. If its window has passed, the timer then expires it.
    - An order already accepted or further on keeps its status. The owner decides (cancel, or refund).
    - After a reversal, the reference may be verified again.
- **Cash on delivery (P8-T2):**
  - **Blocklist:** `customers.cod_blocked_at` and `cod_block_reason`. `PUT` / `DELETE /api/customers/{id}/cod-block` needs the new `CUSTOMERS_BLOCK_COD` permission (owner and manager) and is audited. A blocked customer is not offered COD and is told why. A block set after the summary is refused at YES.
  - **First-order limit (changed from P7):** a customer is on their first order until one of their orders is **completed**. Cancelled, rejected or expired orders don't count. Before this, any earlier order lifted the limit.
- **Transfer instructions and evidence (P8-T3):**
  - The restaurant's account details are a setting (`transfer_account_details`, 500 characters, `SETTINGS_EDIT`, audited). **Without them, transfer isn't offered** (the quote refuses `payment_unavailable`).
  - The details are part of the order confirmation itself (critical, queued with the order). "status" repeats them while a transfer is awaited.
  - **Evidence:** a screenshot or document, or a message such as "I have paid" (a fixed phrase list, or the AI's `payment_claim` intent), is stored in `payment_evidence` against the customer's open transfer order, and audited. **Nothing else happens.** The customer is told that staff will check and that a screenshot alone doesn't confirm payment.
    - Evidence is kept even while the chat is handed over or the customer has opted out, but no reply is sent then.
    - Only the provider's media handle is stored. Fetching the image for staff needs the real adapter (P5-T2) and the screen (P9).
- **Staff view:** `GET /api/orders/{id}/payment` returns the ledger, the evidence, the customer's COD block, and the allowed actions (`verify` or `late_review`).
- **Errors:** the API now maps error subclasses to their parent's HTTP status (a duplicate reference is a 409 conflict).
- **Findings for later phases:**
  - **P9:** the payment panel (verify with reference and PIN, the confirmer picker on the kitchen tablet, the late review, the evidence list, the owner's report and reversal). Expired orders with evidence aren't in the "active" list, so the dashboard needs a "late payments" view.
  - **P5-T2:** fetching evidence media for staff.
  - **P11:** retention must cover `payment_evidence` (it links to the customer's media).
  - **P13:** each restaurant enters its account details before transfer is offered.
- **Evidence:** PR #10. CI run 39641355254 (761 tests). Migration 0010; tests in `backend/tests/api/test_payments.py`, `backend/tests/payments/`, `backend/tests/db/test_payments_ledger.py` and `backend/tests/test_paid_writers.py`.

### AD-33: P9 implementation choices (staff dashboard)
- **Status:** ADOPTED (delegated), awaiting owner review. Built against the `FakeProvider` under AD-25. It implements AD-02 (approved), AD-03 (approved), AD-20 and AD-23.
- **Frontend stack: plain JavaScript modules, no build step, served by the API from the same origin.**
  - The dashboard is the files in `web/`, with no framework, bundler or npm packages. FastAPI serves them (`/`, `/login`, `/assets/*`, `/sw.js`, `/manifest.webmanifest`).
  - *Why:* no JavaScript supply chain to audit or patch, nothing to build in CI or on the server, and a same-origin app needs no CORS or cookie exceptions (the session cookie stays SameSite=Strict and HttpOnly).
  - *Trade-off:* hand-written rendering instead of a framework's. It is fine at this size (about ten screens). If the dashboard grows a lot, a framework such as Preact can be added later behind the same API.
  - *Alternative:* React or Preact with Vite and TypeScript. It needs Node in CI and a build step, and adds dependencies.
- **Installable PWA:** a web manifest with icons, and a service worker that caches **only the app's own files**. It never touches `/api/`: no order, chat or customer data is cached, and no action is stored to send later.
- **Security headers on every response:**
  - a Content Security Policy that allows only this origin, with no inline script or style;
  - **Trusted Types required**, so script can't write HTML strings into the page. One policy (`velora`) allows exactly one script URL, the service worker;
  - no framing, no referrer;
  - `Cache-Control: no-store` on the API.
  - The code builds every element with DOM calls; nothing a customer typed can become markup. A test fails if the code uses `innerHTML`, `eval`, `localStorage` and the like.
  - *Not done here:* HSTS and TLS belong to the reverse proxy (P12).
- **No secrets in the browser:**
  - the session is the HttpOnly cookie;
  - the CSRF token lives only in memory, fetched from `/api/me`;
  - the login token is read from the URL fragment and removed from the address bar and history before it is posted.
  - A browser test checks that local and session storage, IndexedDB and the service-worker cache hold no token and no API response.
- **Idempotency keys on every action (AD-20), migration 0011:**
  - Every state-changing request from a signed-in session must carry `Idempotency-Key` (16 to 100 characters); without it, 428.
  - The first request with a key runs. A repeat gets the first response's status with `Idempotent-Replayed: true`, and changes nothing. While the first is still running a repeat gets 409; a key reused for a different request gets 422.
  - A failed action (not 2xx) frees its key, because nothing was done.
  - The dashboard makes one key per action button and keeps it while the button is on screen, so a double tap or a retry after a lost answer makes one change.
  - Keys are per session, kept 24 hours, and store **only the status, never a response body**, so no customer data or login link is copied into the table.
  - *Trade-off:* the key and the action commit in separate transactions. If recording success fails after the action committed, a retry after 2 minutes could run the action again. Every action that matters has its own guard as well: order moves name the `from` status, payments need `awaiting_transfer` and a unique reference, and outbox messages have their own key.
- **Live updates by polling** every 5 seconds: the active orders, today's finished orders, the ordering status, and (by role) the handoff count and channel state.
  - *Why:* simple and robust behind any proxy, and it needs no long-lived connections from the database pool. The load is small at pilot scale (a few devices per restaurant).
  - *Alternative:* server-sent events. They can be added later if polling load matters.
- **Offline behaviour (P9-T7):** when the browser goes offline or a poll fails, a "Connection lost" banner appears, and every action is refused at once with a visible message. Nothing is queued, and the service worker never replays requests.
- **New-order alert:** a banner naming the new orders, plus a two-tone beep once "Sound on" has been tapped (browsers allow audio only after a tap).
- **Two new permissions:**
  - **`channels.manage`, owner only:** WhatsApp status details and QR re-link. A pairing QR code gives control of the ordering number. Other staff see only whether orders are being taken, and why not (`/api/ordering/status`, e.g. "WhatsApp is disconnected — tell the owner").
  - **`reports.view`, owner and manager:** the today summary (takings). Kitchen roles see only the order board.
- **QR re-link (P9-T6):**
  - `POST /api/channels/{id}/relink` asks the provider to start pairing and records the state it reports (`source = relink`). It is audited.
  - `GET /api/channels/{id}/qr.svg` renders the provider's QR code as an SVG image on the server, sent `no-store`. The code never passes through the page's script and is never logged, audited or stored. The renderer is `segno`, a small pure-Python QR library with no dependencies of its own.
  - Once the phone has scanned it, the provider reports `connected` (webhook or health poll), orders reopen (P5 circuit breaker), and the banner clears at the next poll.
  - The API is given its provider adapters by a new composition root, `velora.asgi` (`uvicorn velora.asgi:app`). A channel whose adapter isn't running (OpenWA until the Go) can't be re-linked from the dashboard, and says so.
- **Chats (P9-T4):** the handoff inbox, a chat's last 50 messages both ways (from the inbox and outbox), and staff replies.
  - A reply goes through the outbox like any other message, only while the chat is handed over and the customer hasn't opted out (409 otherwise). It is audited with its length, not its words.
  - The same screen lists outbound messages from the last day that `failed` or whose delivery is `unknown` (P5 finding). They are never re-sent automatically.
- **Payments (P9-T3):**
  - The panel asks for the reference and the PIN, and refuses to send without either. On a shared kitchen tablet the confirmer picks their name from `GET /api/payments/confirmers` (people who can confirm and have a PIN; names only).
  - Customer evidence is listed. Viewing images waits for the real adapter (P5-T2).
  - A "Payments" screen lists expired transfer orders from the last 7 days (`GET /api/orders/late-payments`, with their evidence count) for the late review, and shows the owner's daily report with reversal.
- **Today summary (P9-T5):** `GET /api/summary/today?day=` counts the orders placed that Pakistan day by outcome, the completed sales by payment method, the net transfers verified, and the chats waiting.
- **Menu, controls, settings and staff (P9-T5):** the existing P3 and P4 endpoints and permissions, unchanged. A test checks that every API path the dashboard calls exists. The Menu screen toggles availability, shows the draft's changes against the live menu, imports a CSV into the draft (listing every problem), publishes it, and republishes an earlier version (rollback). Editing items one by one stays with the CSV (P4).
- **Local testing tool (`python -m velora.dev`, development only):** `setup` makes a local database and a demo restaurant through the founder's onboarding tools, with the sample menu in the draft; `api` serves the dashboard; `customer` plays a WhatsApp customer through the real ingress path and runs the worker with the `FakeProvider`. It writes `.env.dev` (git-ignored) and never touches `.env`. It refuses to run outside development, and `setup` only works on a database on the same computer. Guide: [`../planning/local-testing-guide.md`](../planning/local-testing-guide.md).
- **Browser tests:** Playwright drives headless Chromium against the real app (uvicorn) and the test database, with the `FakeProvider` as WhatsApp. CI installs Chromium and fails, rather than skips, if it can't run them (`VELORA_REQUIRE_BROWSER_TESTS=1`). `uvicorn` and `playwright` are development dependencies only; the production server is P12.
- **Findings for later phases:**
  - **P5-T2:** fetching evidence images for staff; the OpenWA adapter's `get_qr` returns the pairing code as text for the API to render (or the adapter decodes an image the gateway gives).
  - **P10:** alert on `failed` and `unknown` messages (the dashboard only lists them).
  - **P12:** run `velora.asgi:app` behind TLS with HSTS, and trust proxy headers so the login rate limit is per client (P3 finding).
  - **P13:** the usability check with real kitchen staff (P9-T8), and the end-to-end staging run with a test number.
- **Evidence:** PR #12. CI run 39775329133 (810 tests). Migration 0011; tests in `backend/tests/api/test_dashboard.py` and `backend/tests/web/`.

### AD-34: P10 implementation choices (monitoring and alerting)
- **Status:** ADOPTED (delegated), awaiting owner review. It implements AD-19 (alerts), AD-22 (stop rules fed by metrics) and AD-24 (no secrets in logs).
- **Scope of the authorisation (needs the owner's confirmation):** AD-25 lists P10 as gated by the Go. The owner said "Start P10" on 2026-10-22, and this was read the same way as P5–P9: **build the provider-neutral part against the `FakeProvider`**. Nothing here connects to WhatsApp, buys a service or deploys anything.
- **Structured logs (P10-T1):**
  - One JSON object per line: time (UTC), level, logger, message, `tenant_id`, `correlation_id`, and the traceback if any. **Every string is redacted before it is written.** `VELORA_LOG_FORMAT=text` gives a readable line for local use, still redacted.
  - Each request gets a correlation id: the caller's `X-Request-ID` if it is 8–64 safe characters, otherwise a new one. It is returned in `X-Request-ID`, so a staff member's report can be matched to the log. The tenant is added once the session is known. Each worker round gets its own id, and the tenant being worked on.
  - Uvicorn's own log lines go through the same formatter.
  - Redaction now also covers **cookies and session cookies**. A logged `Cookie` header would otherwise have leaked the dashboard session. This was found while writing the T1 test.
  - A server error is logged once, with the method, path and status, never the query or body.
- **Readiness:** `/readyz` answers 200 only if the database answers within 5 seconds (503 otherwise), and says nothing else. `/healthz` stays a plain liveness check.
- **A new role, `velora_monitor` (migration 0012):**
  - Like `velora_dispatcher`: login, NOBYPASSRLS, owns nothing, and reads across tenants through SELECT-only policies.
  - Column grants only: statuses, timestamps, ids and tenant names. **No payload, message, address, amount, reference or customer**, and no audit log. A test lists what it may and may not read.
  - It can append to two global, append-only tables and nothing else: `backup_runs` (the P11 backup job reports each run) and `alert_events` (each alert sent or cleared). The runtime role can't see either.
  - *Alternative:* run the monitor as a superuser or with BYPASSRLS. Rejected: one leaked monitor password would expose every restaurant's data.
- **Two new per-tenant tables (RLS, append-only):**
  - `handoff_events`, written by a trigger when a chat is handed to staff. Conversations keep only the current handoff, and P13 needs a daily count.
  - `ingress_rejections`: webhooks refused for a bad signature, a bad payload, another session's events, or an error while storing. No body or header is kept. `Ingress.reject` records one; **the OpenWA webhook endpoint that calls it is still P5-T3, gated by the Go.**
- **Metrics (P10-T2) are read from the database on demand**, not kept in process memory. The API, worker and monitor are separate processes, and the database already holds the truth. There is no metrics server (such as Prometheus) to run or secure.
  - Inbox and outbox: items due, and how long the oldest has waited. A stopped worker shows up as a growing age.
  - Dead letters: inbox events that ended `failed`, and outbox messages that ended `failed` or `unknown`, in 24 hours (the P5 and P9 finding).
  - Channels: state and since when; state changes in 24 hours; minutes in `needs_qr` per day.
  - Refused webhooks in 15 minutes, by reason. The last backup run, and the last good one.
  - Today's order funnel (placed, awaiting, in progress, completed, cancelled or rejected, expired, refund pending), and the global pause.
- **Alerts (P10-T3):**
  - **Rules:**
    - the API not ready twice in a row;
    - the monitor can't read the database;
    - a channel out of `connected` for 5 minutes;
    - a channel `suspected_restricted`, at once (stop rule, AD-22);
    - a queue's oldest item waiting 5 minutes;
    - any dead letter;
    - 5 refused webhooks in 15 minutes;
    - a failed backup;
    - no good backup within 2 hours (once backups are expected, from staging on).
  - **No noise, nothing missed:** each problem is sent once, again when it grows (e.g. more dead letters) or every 4 hours while it lasts, and once when it clears. An alert that no notifier accepted is tried again after 5 minutes. `alert_events` records every notification, so a restarted monitor doesn't repeat open alerts.
  - **Delivery:** email (SMTP with TLS) and/or a JSON webhook to a push or SMS gateway. **Never WhatsApp**, which may be the thing that is down. Outside development, the monitor refuses to start without one, and only over https/TLS.
  - **External uptime:** the monitor pings a heartbeat URL every minute (a "dead man's switch"), so an external service notices when the monitor or the whole host stops. The same service, or an uptime checker on `/readyz`, also covers "kill the ingress" from outside.
  - **Which service:** a free one (AD-35, zero cost). Email through a free mailbox, and a free heartbeat or uptime tier. Any service that takes a JSON POST or an HTTP ping works.
- **Founder's dashboard (P10-T4): a command line, not a web screen**, as with the onboarding tools (AD-27).
  - `python -m velora.monitoring status` shows every metric and the alerts that would fire now. `daily` shows the pilot metrics per restaurant.
  - The loop also emails the previous day's metrics at 09:00 Pakistan time.
  - **Daily metrics:** orders, completed, **completed without a human** (no handoff in that chat that day before the order), cancelled, expired, handoffs, disconnects, minutes needing a QR scan, payment verifications and reversals, dead letters, and problems alerted. Incidents are logged by hand (P13).
  - *Alternative:* a founder web page. It needs a founder login and a cross-tenant API, which is a larger attack surface. It can be added later on the same metrics.
- **Drills (exit criterion):** automated tests run all four drills through the real monitor loop and database:
  1. the API stops answering;
  2. a session is unlinked (`needs_qr`, through the worker's health poll);
  3. inbox events fail until they are dead letters;
  4. the backup job reports a failure.

  Each alert fires once, is recorded, and clears. **Real delivery to the founder's phone or email can only be checked once a vendor is set up** (`python -m velora.monitoring test-alert`, in staging).
- **Findings for later phases:**
  - **P5-T3:** the OpenWA webhook endpoint must call `Ingress.reject` for each refused webhook, or the HMAC-failure alert never fires.
  - **P11:** the backup job must call `python -m velora.monitoring record-backup` after each run. Set `VELORA_MONITOR_EXPECT_BACKUPS=true` once it runs.
  - **P12:** run the monitor as its own container, with `VELORA_MONITOR_PROBE_URL` on the private network. Set up the heartbeat and an external `/readyz` check, and set up the free alert mailbox and heartbeat (AD-35).
- **Evidence:** see "P10 task evidence" in the status tracker. Migration 0012; tests in `backend/tests/monitoring/` and `backend/tests/test_structured_logs.py`.

### AD-36: P11 implementation choices (backups, restore and recovery mode)
- **Status:** ADOPTED (delegated), awaiting owner review. It implements AD-21 (backups and restore, recovery mode on every restart) under AD-35 (zero cost: our own backups to free storage).
- **Scope of the authorisation:** the owner said "Start P11" on 2026-10-23. As with P5–P10, only what works without the OpenWA Go was built:
  - **Built:** backups, restore and restore verification; recovery mode; the runbooks.
  - **Not built:** inbound reconciliation (recovery step 3), which waits for V-04. It is a hook, recorded as `skipped` until then.
  - **Waiting for staging (P12):** the 24-hour backup run, the staging drills, and the runbook walk-throughs.
  - Nothing was bought, deployed or connected to WhatsApp.
- **A backup role, `velora_backup`:**
  - LOGIN, **BYPASSRLS**, a member of `pg_read_all_data`, owns nothing, writes nothing.
  - FORCE RLS applies even to the table owner, so a dump as the migrator would miss rows or fail; this resolves the AD-26 P11 finding.
  - *Trade-off:* its password can read every tenant's data. It is kept only on the database host, for the backup job.
  - *Alternative:* a superuser for backups. Rejected: it can also write and change roles.
- **What a backup is:** three files under a UTC timestamp, each encrypted:
  - `roles.sql`: `pg_dumpall --roles-only --no-role-passwords`, so the restore can check every VELORA role's attributes;
  - `database.dump`: `pg_dump --format=custom`, with grants, RLS settings and policies;
  - `manifest.json`: row counts per table, the schema version, the tables under FORCE RLS with their policies, the database's own grants (CONNECT, which a plain `pg_dump` leaves out: the P2 finding), and each file's SHA-256.

  The manifest is uploaded last, so a backup without one is incomplete and is never restored. The counts and the dump come from **one exported snapshot**, so a restore is checked row for row.
- **Role passwords aren't backed up.** They are set again after a restore (runbook R4). This keeps the dumps free of credentials, at the cost of one step in the restore.
- **Encryption: to a public key.**
  - The server holds only the public key. The founder keeps the secret key offline (a password manager plus a paper copy), so neither a stolen server nor the bucket can read a backup.
  - Format: the design of `age` (X25519, HKDF-SHA256, AES-256-GCM in 64 KiB chunks, with a counter and a last-chunk flag), from the standard `cryptography` library: a **new dependency**. Tests show a changed byte, a dropped chunk, reordered chunks and the wrong key are all refused.
  - *Alternative:* the `age` tool itself. Rejected for now: one more binary on the server and in CI. Our files can't be read by `age`; restore uses `python -m velora.backup`.
  - **Risk:** losing the secret key means having no backups. The runbook says so in bold.
- **No plaintext on disk:** pg_dump's output is encrypted from its pipe, in memory; restore decrypts straight into pg_restore's input.
- **Storage:** a directory (`file://`) or any S3-compatible bucket (`s3://`), so Backblaze B2 or Cloudflare R2 free tiers both work (B5 still has to pick one).
  - The S3 client signs with AWS Signature V4 using only the standard library. It is checked against AWS's published example.
  - The job never deletes or overwrites. Old backups go by the **bucket's lifecycle rule** (e.g. 30 days), and the server's storage key should be one **without delete permission**, so a stolen server can't erase the history.
- **OpenWA session state is never backed up** (P11-T2; the PC-8 default). Only the database is dumped. After a host loss, every restaurant re-links by QR.
- **Restore** always goes into a **new** database, never over one. It drops that database again if the restore itself fails. It then checks:
  - every VELORA role, after `setup-roles` from the code, against the backed-up attributes; none may be a superuser, and only the backup role may bypass RLS;
  - the schema version;
  - every row count;
  - FORCE RLS and the policy names on every table that had them;
  - the database grants;
  - **tenant isolation as `app_rw`:** with no tenant set it sees nothing, and scoped to each tenant it sees exactly that tenant's rows, in every tenant table.

  Any failed check fails the restore. RPO and RTO are reported for each restore (data age, duration).
- **Recovery mode on startup** (all nine P11 steps, in order):
  - **When the outage began:** the worker writes a heartbeat (`worker_heartbeats`, global, no tenant data) every 15 seconds and when it stops. The outage is taken to start one interval after the last heartbeat. After a database restore, that is the backup's last heartbeat.
  - **Health check:** the database answers, and has the tables this code needs. The runtime role still can't read the migration ledger: the P2 rule was kept rather than loosened.
  - **One worker at a time:** a session advisory lock, held for the worker's life. Steps 2 and 6 take over every lease and in-flight send, which is safe only with no other worker (AD-19).
  - **Each tenant in one transaction**, with its `recovery_runs` row, unique per tenant and outage. An interrupted recovery runs again on the next start and skips tenants already done, so a transfer window is never extended twice. If recovery fails, the worker exits without working, and its supervisor restarts it.
  - **Transfer windows** still open when the outage began are extended by the outage's length. One that had already closed expires as usual.
  - **In-flight sends** become `unknown`, critical or not: the plan's rule, stricter than the P5 lease rule (which retries non-critical messages if the provider's sends are idempotent).
  - **One apology per customer:** a customer asked to confirm again (step 4) doesn't also get "we're back" (step 7).
  - **Rate-limited flush:** recovery sends nothing. The normal sender flushes under its cap (20 per channel per minute).
  - **Orders placed during recovery:** from the restart until the last customer message received before it has been processed. The dashboard shows "⚠ Placed after a system outage — confirm the order with the customer". Messages that arrive after the restart, e.g. redelivered by the provider, don't count; V-04 may change that.
  - *Trade-off:* every restart, even a 10-second deploy, voids open summaries, and those customers are asked to confirm again. That is the plan's rule ("every restart runs a recovery mode"). A minimum outage length could be added if it annoys customers in the pilot.
- **Runbooks:** [`../operations/runbooks.md`](../operations/runbooks.md), with R1 to R6 and the manual fallback, each with detect, act now, fallback, recover and tell. None has been walked through in staging yet.
- **Findings for later phases:**
  - **P12:**
    - run `python -m velora.backup run` hourly (systemd timer or cron);
    - set `VELORA_MONITOR_EXPECT_BACKUPS=true`;
    - create the bucket with a lifecycle rule and a no-delete key;
    - install PostgreSQL 17 client tools, or use `VELORA_BACKUP_PG_TOOLS`;
    - set the `velora_backup` password;
    - run the worker under a supervisor that restarts it (recovery exits non-zero on failure);
    - run the restore drill and the recovery drill in staging, and walk through each runbook.
  - **V-04:** fill the inbound reconciliation hook (step 3), and decide whether provider redeliveries after a restart count as "during recovery".
  - **P10 (optional):** alert when the worker's heartbeat is older than a few minutes. Today a stopped worker shows up as a growing queue age, but only when there is work.
  - **P13:** retention for `outbox_messages`, `inbox_events`, `conversations.context`, `pending_actions.payload` and `payment_evidence` (the P5–P8 findings) is **not** done here. Backups keep whatever the database holds, for as long as the bucket keeps them.
- **Evidence:** see "P11 task evidence" in the status tracker. Migration 0013; tests in `backend/tests/backup/` and `backend/tests/orders/test_recovery_mode.py`.

### AD-37: P12 implementation choices (deployment and staging)
- **Status:** ADOPTED (delegated), awaiting owner review. It implements AD-19 (one server), AD-24 (security baseline) and AD-35 (zero cost: a free VM with PostgreSQL on it).
- **Scope of the authorisation:** the owner said "Start P12" on 2026-10-23. As with P5–P11, only what works without a server or the OpenWA Go was built:
  - **Built:** the application image, the container layout, HTTPS with Caddy, the settings files and their check, server provisioning, the host-hardening script, the release and rollback tool, the hourly backup timer, the external port scan, and a CI job that rehearses all of it on every push.
  - **Not done, needs the owner:** the VM (V-03, B5), the hostname (B12), the alert mailbox and heartbeat (B11), the backup bucket (B5). So these are still to do:
    - the timed from-scratch run (T6);
    - the external scan of a real server (T1);
    - staging (T4);
    - a release and rollback in staging (T5);
    - the staging drills (T7).
  - **Not done, needs the Go (V-01):** the OpenWA container. Its compose file is written but not used, and the "kill OpenWA" and "force a disconnect" drills wait for it.
  - Nothing was bought, deployed or connected to WhatsApp.
- **One image for every VELORA container** (`infra/docker/app.Dockerfile`): the API, worker, monitor, backup job and the founder's tools differ only by command.
  - It's built from the lockfile, runs as a non-root user (uid 10001), and includes the PostgreSQL 17 client tools from the PostgreSQL project's repository, with the signing key checked against its published fingerprint (the P11 finding).
  - Base images are pinned by digest, multi-arch, so the same files build on the free arm64 VMs.
- **The dashboard is served by the API container, not a separate web container.** The plan listed "the web app" as its own container. Serving it from the API keeps one origin, one set of security headers (CSP with Trusted Types) and the strict session cookie (AD-33). Caddy is the separate edge container.
  - *Alternative:* Caddy serving `web/` directly. It would need the CSP and caching rules duplicated.
- **Networks** (`infra/compose.yaml`). Only Caddy publishes a port (443). The others:
  - `edge`, internal: Caddy and the API;
  - `data`, internal: the database and the containers that use it;
  - `egress`, outbound only: Groq, alerts, the backup bucket;
  - the database is reachable from nothing else;
  - OpenWA (after the Go) gets its own internal network, shared only with the API and worker, plus its own outbound network to reach WhatsApp.

  `tests/deploy/test_compose_policy.py` checks these rules on the configuration compose actually runs: published ports, networks, capabilities, read-only filesystems, `no-new-privileges`, image pinning, restart policies, log rotation, and which settings file each container gets.
- **Every container is locked down:** read-only filesystem, all Linux capabilities dropped, `no-new-privileges`, log rotation, a memory limit, and `init` so signals reach Python. The two exceptions are written down:
  - Caddy keeps the right to bind port 443;
  - PostgreSQL's entrypoint keeps the five rights it needs to prepare its data directory before dropping to its own user.
- **HTTPS: Caddy**, with a free automatic Let's Encrypt certificate.
  - **Port 80 stays closed**, as the plan says (443 only). Certificates are issued over 443 (the TLS-ALPN challenge), and there is no http-to-https redirect.
  - *Trade-off:* typing `http://` into a browser fails instead of redirecting. Every link VELORA gives out is `https://`.
  - HSTS for one year, **not preloaded**: a preload is hard to undo, and the hostname isn't settled (B12).
  - HTTP/3 is off (UDP 443 stays closed). Caddy's admin endpoint is off, and no `Server` or `Via` header names the software.
- **Proxy headers** (P3 and P9 findings):
  - Caddy has a fixed address on the edge network, and the API trusts `X-Forwarded-For` from that address only (`python -m velora.serve`, `VELORA_FORWARDED_ALLOW_IPS`; `*` is refused).
  - Caddy replaces any `X-Forwarded-For` a client sends.
  - The login rate limit is therefore per real client: not global, and not something a client can dodge. The CI rehearsal shows that 11 attempts with 11 spoofed addresses are limited after 10.
  - uvicorn's own log lines now go through the redacting JSON formatter (P5 finding).
- **Secrets:** settings files in `/etc/velora`, one per container, root-only (0600, directory 0700). The plan allows "a secret store or env files with restricted permissions"; this is the env-file option.
  - `velora env-init` writes them with a fresh random password for every database role and the administrator.
  - `velora db provision` sets each role's password from those files. There is one source of truth, and no hand-typed `\password`.
  - `velora env-check` runs before every release. It checks:
    - permissions and ownership;
    - required values;
    - each service's own settings validation;
    - the same password for a role in every file;
    - https.
  - *Trade-off:* the values are environment variables inside the containers, visible to `docker inspect`, so to root on the server. Root can read the files anyway on a one-person server.
  - *Alternative:* an external secret store. Rejected for now: it costs money or adds a service, for one administrator.
- **PostgreSQL in a container** on the same VM (AD-35):
  - its data is in a Docker volume;
  - it's never published;
  - passwords use scram-sha-256;
  - default tuning for now; V-03 sets the VM's size.
- **Releases are built on the server**, from `git archive` of the exact commit, and tagged with the commit's first 12 characters.
  - There is no image registry: nothing to pay for, and no registry credentials on the server.
  - *Trade-off:* a build takes a few minutes on a free VM.
- **`velora release REF`:**
  - builds the image;
  - checks the settings;
  - **backs up first**, and stops if that fails;
  - provisions and migrates;
  - switches every container (`docker compose up --wait`);
  - waits until `/readyz` answers through Caddy with a trusted certificate and HSTS;
  - records the release in `/etc/velora/release.env` and `releases.log`.
- **`velora rollback`** switches the images and compose files back to the previous release. It **never reverts migrations** (fix forward):
  - the release checklist therefore requires each release's migrations to work with the previous release's code: add first, remove a release later;
  - when that can't hold, the way back is a restore (R4);
  - running `rollback` again rolls forward.
- **Docker is the supervisor** (`restart: unless-stopped`). A crashed worker, or one whose recovery failed, is restarted, and recovery mode runs again (the P11 finding). The CI rehearsal kills the worker with SIGKILL and checks both.
- **Hourly backups** run from a systemd timer (`velora-backup.timer`, persistent across reboots). `env-init` sets `VELORA_MONITOR_EXPECT_BACKUPS=true`.
- **Restores read the secret key on standard input** (`--secret-key -`, a new option on `python -m velora.backup restore`), so it's never copied into a container or a volume.
- **The host** (`infra/host/install.sh`, Ubuntu 24.04):
  - **SSH:** keys only, no root login, only the admin user.
  - **Firewall:** ufw, with 443 from anywhere and 22 from listed addresses. The script refuses to run if the current SSH session would be locked out.
  - **Cloud-image rules:** some images (Oracle's) ship their own iptables rules; the script removes them, so ufw decides.
  - **Updates:** automatic security updates. When one needs a reboot, it happens at 00:00 UTC (05:00 PKT), and the worker's recovery mode covers it.
  - **Docker** comes from Ubuntu's own packages, so the security updates cover it too. It gets log rotation, `live-restore`, and 2 GB of swap on small machines.
  - **Docker publishes ports past ufw.** That's why the compose test allows exactly one published port, and why the external scan (T1) is the proof.
- **The external port scan** (`python -m velora.deploy scan`) is a TCP connect scan in the standard library, run from the founder's computer.
  - It fails on any open port other than the expected ones, and on an expected port that is closed.
  - UDP isn't scanned: nothing listens on it, and `nmap -sU` can confirm that.
- **Staging:**
  - a separate stack with `VELORA_ENV=staging`, its own hostname, settings and data;
  - in staging, `python -m velora.admin tenant-create` refuses a slug without `test-`, so merchant data can't be onboarded there by mistake;
  - a production backup is never restored into staging;
  - **zero-cost proposal:** if the free tier gives one usable VM, staging runs on it first. Once the drills pass, it is rebuilt as production (new settings and volumes), and later rehearsals happen in CI.
- **The CI rehearsal** ("Deployment rehearsal" job, `infra/tests/stack-drill.sh`) runs the real compose stack through the same `velora` tool on every push:
  - a first release from nothing;
  - only 443 published, and a scan of the stack's ports;
  - the TLS chain verifies, with HSTS and CSP sent;
  - settings are owner-only, and `env-check` refuses a readable one;
  - no database password appears in any container log;
  - the rate limit holds through the proxy, and the staging guard works;
  - the worker-crash drill;
  - the API-down drill: the alert reaches an HTTPS webhook stand-in, then clears;
  - a backup, a verified restore, and the R4 swap into service;
  - a second release, a rollback, and a roll forward.

  The record goes into the run's summary. **It is a rehearsal, not staging:** a private certificate authority, a stand-in for the founder's webhook, no WhatsApp, one CI machine.
- **New runtime dependency:** `uvicorn` moves from the development tools to the runtime (the production server).
- **Findings for later phases:**
  - **P5-T2 (after the Go):**
    - set `VELORA_OPENWA_IMAGE` to the exact validated version;
    - lock its container down like the others (read-only, capabilities) once its needs are known (PC-4);
    - restrict its outbound network to what WhatsApp needs, if possible;
    - add the two OpenWA drills to the CI rehearsal, with a stand-in if one exists.
  - **P10 (optional):** a worker-heartbeat alert (the AD-36 finding still stands).
  - **P13:**
    - data retention (still open);
    - the usability check;
    - the staging end-to-end run.
  - **Once the hostname is chosen (B12):** keep it. The dashboard is an installed app (PWA) tied to its origin, and HSTS is cached for a year.
- **Evidence:** see "P12 task evidence" in the status tracker. Files are in `infra/`; tests are in `backend/tests/deploy/` and `backend/tests/db/test_provision.py`; the guide is [`../operations/deployment.md`](../operations/deployment.md).

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

> **Owner decision AD-35 (2026-10-22): zero cost.** The plan is now **$0 a month**: a free VM, PostgreSQL on it, free off-site backup storage, Groq's free tier, and free monitoring. The scenarios below are kept for reference if paid services are ever reconsidered.
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

1. **Every ADOPTED (delegated) decision (AD-11 to AD-24, AD-26 to AD-34, AD-36, AD-37).** They stand unless you reverse them.
   - ~~**AD-30 needs a decision:** may Groq's free tier receive real customer messages?~~ **Decided by AD-35:** yes (zero cost). Switching on Zero Data Retention in Groq's Data Controls is recommended.
   - **AD-31 asks you to confirm:** opted-out customers get no order status messages; a customer can cancel only before acceptance and before a verified payment.
   - **AD-32 asks you to confirm:** the reconciliation report and payment reversal are owner-only (managers can't); a duplicate reference is refused rather than allowed with a warning; the COD first-order limit applies until a customer has a *completed* order; staff confirm the full order total (no partial payments).
   - **AD-33 asks you to confirm:** the dashboard is plain JavaScript with no build step; only the owner sees WhatsApp details and the QR re-link (managers see whether orders are being taken, not the QR); kitchen roles don't see the today summary; updates by polling every 5 seconds.
   - **AD-34 asks you to confirm:** that "Start P10" meant the provider-neutral part before the Go (AD-25 listed P10 as gated); that the founder's dashboard is a command line plus a daily email, not a web page; the alert vendor is now a free tier (AD-35).
   - **AD-36 asks you to confirm:**
     - backups are encrypted to a key only you hold offline: **lose it and the backups are unreadable**;
     - role passwords are set again after a restore;
     - every restart, even a short one, asks customers with an open summary to confirm again;
     - one worker at a time.
   - **AD-37 asks you to confirm:**
     - port 80 stays closed, so `http://` addresses don't redirect;
     - the dashboard is served by the API container, not a separate web container;
     - releases are built on the server (no image registry);
     - a rollback never reverts migrations, so each release's migrations must work with the previous code;
     - automatic security updates may reboot the server at 05:00 PKT;
     - if the free tier gives one usable VM, staging runs there first and is then rebuilt as production.
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
| PC-11 | P10 (monitoring) | Budget for a paid uptime-monitoring tier (e.g. UptimeRobot Solo, $10/mo). The free tier is described as for hobby/non-profit use. | §6.4 | **REJECTED** by the owner (AD-35, zero cost) |
| PC-12 | Validation plan | Add test T3.9 (passkey linking block, #560), the reconciliation, ID and send-idempotency tests, and the trial configuration baseline. | Validation plan §10.6 | PROPOSED |
| PC-13 | Risk register | Record #560 (a passkey can block all device linking, no workaround) and #830 (first message to a new contact can silently fail) as **pilot risks**. #560 could also block re-linking after a disconnect. | §10.4–10.5 | PROPOSED |
