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

1. **Every ADOPTED (delegated) decision (AD-11 to AD-24).** They stand unless you reverse them.
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
| PC-10 | AD-04 (AI model) | Candidates for the P0-E5 evaluation: Claude Haiku 5.5, gpt-4o-mini, Gemini 3.1 Flash-Lite, and Groq gpt-oss (training policy to verify). Drop Groq Llama 3.3 70B, which is now enterprise-only. | §6.5 | PROPOSED |
| PC-11 | P10 (monitoring) | Budget for a paid uptime-monitoring tier (e.g. UptimeRobot Solo, $10/mo). The free tier is described as for hobby/non-profit use. | §6.4 | PROPOSED |
| PC-12 | Validation plan | Add test T3.9 (passkey linking block, #560), the reconciliation, ID and send-idempotency tests, and the trial configuration baseline. | Validation plan §10.6 | PROPOSED |
| PC-13 | Risk register | Record #560 (a passkey can block all device linking, no workaround) and #830 (first message to a new contact can silently fail) as **pilot risks**. #560 could also block re-linking after a disconnect. | §10.4–10.5 | PROPOSED |
