# PRD & Requirements Analysis: WhatsApp-First AI Hospitality SaaS

**Document Title:** 02-prd-requirements-analysis.md  
**Version:** 1.0  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD] & `OpenWA` Technical Documentation [OpenWA]  
**Status:** Approved Engineering Requirements Analysis & Specification Baseline  

---

## 1. Executive Goals & Product Scope

### 1.1 Product Vision & Guiding Principles
The **WhatsApp-First AI Hospitality SaaS** is a multi-tenant platform designed to automate food ordering, takeaway, delivery, and hotel room service entirely through WhatsApp [24]. The platform integrates natively with **Stock Flow** (a headless inventory engine) to empower independent restaurants, cloud kitchens, and boutique hotels to manage operations, catalog pricing, and inventory without web dashboard apps or third-party marketplace commissions [24].

The architecture is governed by four unyielding operational principles [25]:
1. **WhatsApp as Primary Operational Interface**: End-customers perform 100% of discovery, cart building, room verification, payment, and tracking natively in chat with zero web/app redirects [25]. Business owners handle onboarding, menu setup, pricing, stock toggles, and sales reports strictly via conversational WhatsApp commands [25].
2. **Zero-Trust Financial State Machine**: No order is marked `Paid` or sent to the kitchen based on AI inference, customer statements, or screenshot images. Status transitions occur exclusively via cryptographically verified payment gateway webhooks [25].
3. **Deterministic Inventory & Pricing Integrity**: All financial subtotals, taxes, delivery fees, and inventory counts are computed and locked by database SQL logic. The AI LLM layer handles natural language intent extraction only, with **zero write permissions** to database financial or stock tables [25].
4. **Strict Multi-Tenant Isolation**: Data across all tenants is strictly isolated at the database level using PostgreSQL **Row Level Security (RLS)** policies [25].

### 1.2 Target Markets & Geographic Scope
* **Phase 1 Launch Market (Pakistan)**: Focuses on independent cloud kitchens, fast-food outlets, bakeries, and casual dining venues in Karachi, Lahore, and Islamabad/Rawalpindi [26]. Commercial drivers include bypassing aggregator commission fees (25%–35%) using local payments: State Bank of Pakistan's **Raast P2M (0% transaction fee)**, JazzCash, EasyPaisa, and Cash on Delivery (COD) [26].
* **Phase 2 Expansion Markets**: International rollout covering Brazil (Pix QR), Mexico (MercadoPago, SAT 16% IVA), Saudi Arabia & UAE (Mada, Tap Payments, hotel room service), and UK/EU (Stripe, GDPR, EU AI Act Article 50 disclosures) [26].

### 1.3 MVP Scope vs. Deferred Capabilities

| ESSENTIAL MVP SCOPE (PHASE 1) | DEFERRED CAPABILITIES (POST-MVP PHASE 2+) |
| :--- | :--- |
| • WhatsApp Conversational Owner Onboarding [40] | • Full Hotel Property Management System (PMS) / Room Booking [40] |
| • PDF & Image OCR Menu Parsing & Verification [40] | • Outbound Marketing Broadcast Campaigns [40] |
| • Meta Embedded Signup OAuth / Gateway Connection [40] | • Multi-Warehouse Supply Chain & PO Syncing [40] |
| • 100% Native Customer Checkout (Lists & Buttons) [40] | • Native Mobile Staff iOS / Android Apps [40] |
| • Real-Time Stock Check & 5-Min Temp Holds [40] | • Voice Note AI Conversational Audio Synthesis [40] |
| • Automated Raast / NayaPay / Card Webhooks [40] | • Complex Loyalty Program Engine [40] |
| • Itemized WhatsApp Receipts & Kitchen Alerts [40] | |
| • Conversational Owner Price & Stock Edits [40] | |
| • Human Agent Escalation Handoff [40] | |

---

## 2. Target Users, Roles, and Permissions

```
+-----------------------------------------------------------------------------------+
|                            THE FOUR OPERATIONAL ROLES                             |
+--------------------------+--------------------------+-----------------------------+
| 1. CUSTOMERS & GUESTS    | 2. BUSINESS OWNERS       | 3. KITCHEN & FRONT DESK     |
| - Menu Browsing & Cart   | - Conversational Setup   | - Order Acceptance Alerts   |
| - Pre-Order Stock Check  | - OCR Menu Import        | - Delivery Status Toggles   |
| - Payment & Receipts     | - Price & Stock Toggles  | - Human Agent Takeover      |
+--------------------------+--------------------------+-----------------------------+
                           | 4. SYSTEM ADMINISTRATOR  |
                           | - Tenant Provisioning    |
                           | - Meta OAuth Credentials |
                           | - Global Billing Rules   |
                           +--------------------------+
```

### 2.1 Persona Specifications & Permissions

1. **End-Customer / Hotel Guest** [28, 29]:
   * *Capabilities*: Message business number, view interactive menus, select item modifiers, provide delivery location or room number, complete payments, track order status, and request human assistance [29, 33].
   * *Permissions*: Read-only access to published active catalog items; write access restricted to creating pending cart sessions and submitting payments.
2. **Business Owner / Operator** [28, 29]:
   * *Capabilities*: Perform onboarding via WhatsApp, upload PDF/image menus for OCR, update prices, toggle stock availability (in-stock/out-of-stock), pause order intake, and query daily sales [29, 31, 37].
   * *Permissions*: Read/write access restricted strictly to their own tenant record and catalog through WhatsApp phone number authentication.
3. **Kitchen / Staff / Reception** [28, 29]:
   * *Capabilities*: Receive interactive order alerts in staff WhatsApp groups, tap action buttons (`[Accept Order]`, `[Mark Out for Delivery]`), and take over threads when escalated [29, 36, 39].
   * *Permissions*: Write access to order state transitions (`Accepted`, `Preparing`, `Out for Delivery`, `Completed`) and toggling conversation human-handled flags.
4. **SaaS Administrator** [28, 29]:
   * *Capabilities*: Provision tenants, configure Meta App OAuth settings, monitor gateway connections, manage global gateway API keys, and oversee platform billing [29].
   * *Permissions*: System-wide administrative write access via administrative tools/dashboard.

---

## 3. Functional Requirements & Workflows

### FR1: Conversational Business Onboarding (WhatsApp-Native)
* **FR1.1**: Onboarding initiates when an owner messages the shared SaaS onboarding number [30].
* **FR1.2**: Bot prompts selection of business type: (1) Restaurant / Food Ordering, (2) Hotel / Room Services, or (3) Both [30].
* **FR1.3**: System collects metadata: Business Name, City/Location, Operating Hours, Delivery Rules (Delivery/Takeaway/Dine-in), Minimum Order Amount, and Accepted Payment Methods [30].
* **FR1.4**: Onboarding state is transactionally persisted after every message, allowing owners to pause and resume setup seamlessly [30].

### FR2: Menu & Service Catalog OCR Import
* **FR2.1**: System accepts catalog inputs as PDF documents, images (JPEG/PNG), CSV files, or plain text [31].
* **FR2.2**: Asynchronous OCR pipeline (Pillow + EasyOCR / Vision LLM) parses categories, items, descriptions, sizes, and pricing [31].
* **FR2.3**: If extraction confidence for any price or name is **< 85%**, the bot issues explicit WhatsApp verification prompts requiring owner confirmation [31].
* **FR2.4**: Upon owner verification, the catalog commits to PostgreSQL and syncs with Stock Flow [31].

### FR3: WhatsApp Gateway Connection & Multi-Tenant Routing
* **FR3.1**: Primary architecture connects tenant WhatsApp Business Accounts (WBAs) via Meta's official **Embedded Signup OAuth Flow** [32]. Alternatively, self-hosted deployments can utilize **OpenWA Gateway**, running multi-session connections over PostgreSQL/Redis [2, 3, 32].
* **FR3.2**: Backend routes incoming webhooks (`phone_number_id` or `session_id`) to the specific `tenant_id` context [32].
* **FR3.3**: Webhook payloads require HMAC signature verification (`X-Hub-Signature-256`) using app secrets [32].
* **FR3.4**: In-memory Redis deduplication cache inspects message IDs with a 24-hour TTL to eliminate duplicate webhooks [32].

### FR4: Native In-Chat Ordering & Guest Requests
* **FR4.1**: AI assistant formats menus using Meta's official **Interactive List Messages** (up to 10 items) and **Reply Buttons** (up to 3 options) [33].
* **FR4.2**: Customer builds cart, selects modifiers, and provides delivery location pin/text or Hotel Room Number [33].
* **FR4.3**: Hotel room service enforces **Guest Verification** (Room Number matched against Guest Last Name or Check-in PIN) prior to folio charging [33].

### FR5: Real-Time Inventory Checks & 5-Minute Stock Holds
* **FR5.1**: Before rendering a payment link or confirming an order, system executes an atomic database update against Stock Flow [34]:
  ```sql
  UPDATE stock 
  SET reserved_quantity = reserved_quantity + 1 
  WHERE sku = :sku AND available_quantity > reserved_quantity;
  ```
* **FR5.2**: The reservation applies a **5-minute temporary hold** on requested items [34].
* **FR5.3**: If payment succeeds within 5 minutes, `reserved_quantity` decrements and `actual_quantity` permanently decrements [34].
* **FR5.4**: If payment fails or expires after 5 minutes, a background queue (Upstash QStash) releases the hold [34].
* **FR5.5**: If sold out, the AI suggests same-category alternatives [34].

### FR6: Automated Payment Processing & Webhook Verification
* **FR6.1**: Backend generates dynamic payment links or QR codes bound to `order_id` for supported gateways (NayaPay Arc, Safepay, Raast P2M; Stripe, MercadoPago) [35].
* **FR6.2**: Order status updates to `Paid` **strictly** upon receiving an HMAC-verified HTTP POST webhook from the payment gateway [35].
* **FR6.3**: Cash on Delivery (COD) requires explicit interactive button confirmation (`[Confirm COD Order]`), updating status to `Confirmed (COD)` [35].

### FR7: Receipts, Kitchen Alerts & Order Tracking
* **FR7.1**: Upon payment confirmation, system generates `#ORD-XXXX` and sends an itemized WhatsApp receipt to customer [36].
* **FR7.2**: Instant alert sent to kitchen/staff WhatsApp group with interactive buttons: `[Accept Order]` and `[Mark Out for Delivery]` [36].
* **FR7.3**: Customer receives automated status updates (`Preparing` -> `Out for Delivery` -> `Completed`) [36].

### FR8: Conversational Owner Management via WhatsApp
* **FR8.1**: Authorized owner phone numbers execute operational management via text or voice notes [37]:
  * *"Set Pepperoni Pizza price to 1400"* -> Updates database price [37].
  * *"Mark Large BBQ Chicken out of stock"* -> Sets stock availability to 0 [37].
  * *"Pause incoming orders for 45 minutes"* -> Toggles pause state and auto-replies to customers [37].
  * *"Show today's sales"* -> Returns sales summary report [37].
* **FR8.2**: High-impact actions (price changes, pausing orders) require interactive button confirmation (`Confirm updating Pepperoni Pizza to Rs. 1,400? [Yes] [No]`) [37].

### FR9: AI Assistant Safety & Natural Language Extraction
* **FR9.1**: AI LLM (Groq Llama 3.3 / Gemini 1.5 Flash) performs NLU intent extraction and entity parsing [38].
* **FR9.2**: AI LLM possesses **zero write permissions** to database order, price, or inventory tables [38].
* **FR9.3**: System prompts strictly forbid the AI from inventing prices, offering unauthorized discounts, or declaring payments confirmed without DB verification [38].

### FR10: Human Escalation & Staff Handoff
* **FR10.1**: Customer triggers (*"talk to agent"*, *"human support"*) or sentiment score **< 0.65** flags `conversations.is_human_handled = TRUE` [38].
* **FR10.2**: Automated AI responses immediately pause for that customer thread [39].
* **FR10.3**: Urgent escalation alert with chat link dispatches to staff/reception WhatsApp group [39].

---

## 4. Non-Functional Requirements (NFRs)

| Dimension | Requirement Specification | Enforcement Mechanism |
| :--- | :--- | :--- |
| **NFR1: Ingestion Speed** | Inbound Meta / OpenWA webhook validation < 3.0 seconds [39] | Async FastAPI `async def` + Background Workers [39] |
| **NFR2: DB Performance** | Database inventory availability queries < 10ms [39] | Indexed PostgreSQL queries on shared Stock Flow DB [39] |
| **NFR3: Multi-Tenancy** | Absolute data isolation across tenants [39] | PostgreSQL Row Level Security (RLS) policies [39] |
| **NFR4: Cryptography** | HMAC SHA-256 signature validation on webhooks [39] | API secrets encrypted via AES-256-GCM [39] |
| **NFR5: Uptime & Ping** | Zero cold-start webhook timeout drops in Phase 1 [39] | UptimeRobot ping warmers / $7 persistent host [39] |
| **NFR6: AI Safety** | Zero financial or stock hallucination [39] | Strict DB state lock; LLM has zero DB write access [39] |

---

## 5. Business Rules & Mandatory Unsafe Workflows

To eliminate financial risk, overselling, and operational disputes, five workflows are explicitly prohibited from automated AI execution [41]:

```
+---------------------------------------------------------------------------------------------------+
|                                UNSAFE WORKFLOWS (HUMAN / DB MANDATORY)                            |
+------------------------------+--------------------------------------------------------------------+
| Unsafe Workflow              | Architectural Safety Enforcement                                   |
+------------------------------+--------------------------------------------------------------------+
| 1. Financial Order           | AI LLM has ZERO DB write access. Orders update to `Paid` ONLY via  |
|    Confirmation              | HMAC-verified payment gateway webhooks.                            |
+------------------------------+--------------------------------------------------------------------+
| 2. Price or Stock            | Prices and subtotals are locked strictly by database SQL queries.  |
|    Overrides                 | The AI cannot alter menu prices or delivery charges.                |
+------------------------------+--------------------------------------------------------------------+
| 3. Unverified Room           | Room service billing requires explicit Room Number + Guest PIN     |
|    Folio Charging            | matching property registration records.                            |
+------------------------------+--------------------------------------------------------------------+
| 4. Post-Preparation Order    | Orders in `Preparing` state cannot be canceled automatically via   |
|    Cancellations             | chat; staff approval is required to prevent food waste.            |
+------------------------------+--------------------------------------------------------------------+
| 5. Severe Customer           | Complaints involving billing disputes or angry sentiment trigger   |
|    Complaints                | immediate AI pause and Human Agent Handoff to staff.               |
+------------------------------+--------------------------------------------------------------------+
```

---

## 6. Restaurant-Specific Requirements

1. **Catalog Structure**: Support for hierarchical categories, base item prices, size variants (e.g., Small/Medium/Large), and optional/required add-ons/toppings [31, 33].
2. **Dynamic Menu Availability**: Ability for owners to instantly mark SKUs in/out of stock via WhatsApp [37].
3. **Kitchen Group Alerts**: Real-time dispatch of itemized order summaries to dedicated kitchen WhatsApp groups with status update action buttons [36].
4. **Pause/Resume Intake**: Owner capacity to pause order intake for specified durations (e.g., "Pause for 45 minutes"), triggering automated hold messages to ordering customers [37].

---

## 7. Hotel-Specific Requirements

1. **Room Service Catalog**: In-room dining menus, amenity requests (towels, extra pillows), and laundry service listings [30].
2. **Two-Factor Guest Verification**: Verification protocol matching `Room Number` + `Guest Last Name` or `Check-in PIN` prior to room folio billing [33, 41].
3. **Front Desk Escalation**: Immediate routing of non-standard guest inquiries or room folio verification failures to front desk staff WhatsApp groups [39, 41].

---

## 8. WhatsApp Integration Requirements: Meta Cloud API vs. OpenWA Gateway

The architecture evaluates two WhatsApp gateway options:

```
+---------------------------------------------------------------------------------------------------+
|                               WHATSAPP GATEWAY CAPABILITY MATRIX                                  |
+-----------------------------------+--------------------------------+------------------------------+
| Feature / Requirement             | Meta Cloud API (Official)      | OpenWA Gateway (Self-Hosted) |
+-----------------------------------+--------------------------------+------------------------------+
| Licensing & Cost                  | Meta per-conversation rates    | 100% Free MIT Open Source    |
| Tech Stack                        | Hosted HTTP REST API           | Node.js 22, NestJS 11, TS    |
| WhatsApp Engine Options           | Meta Infrastructure            | whatsapp-web.js or baileys   |
| Multi-Session Architecture        | Embedded Signup OAuth          | Built-in Session Manager     |
| Storage & Cache Adapters          | Meta Infrastructure            | PostgreSQL / Redis / S3      |
| Interactive UI Support            | Native Lists, Buttons, Flows   | Text, Buttons, Media, Groups |
| Built-in Spam & Ban Protections   | Official Business Account      | Typing Simulation, Proxy IP  |
+-----------------------------------+--------------------------------+------------------------------+
```

* **Meta Cloud API Requirements**: Uses Meta's Embedded Signup OAuth flow, supports official Interactive Lists/Buttons, and leverages 1,000 free monthly Service conversations per WBA number [26, 32].
* **OpenWA Gateway Requirements**: Free self-hosted gateway options using Docker Compose (`docker compose --profile postgres --profile redis up -d`) [5, 6]. Provides official TypeScript (`@rmyndharis/openwa`) and Python (`rmyndharis-openwa`) SDKs, HMAC webhooks, session proxies, simulated typing indicators, and rate limiting to prevent account flags [2, 8, 19].

---

## 9. AI Requirements & Guardrails

1. **NLU Intent Recognition**: Multi-lingual parsing supporting English, Roman Urdu (*"1 Medium Pepperoni Pizza chahiye with extra cheese"*), Spanish, Portuguese, and Arabic [27, 38].
2. **Strict Guardrail Matrix**:
   * LLM has zero database write credentials [38].
   * All prices, subtotals, and delivery fees injected into prompt context must be retrieved via database SQL queries [25].
   * System prompts strictly forbid the LLM from confirming orders or declaring payments received without backend status verification [38].
3. **OCR Verification Threshold**: Optical Character Recognition imports enforce an 85% confidence score floor; scores below 85% trigger mandatory owner verification in chat [31].

---

## 10. Gap Analysis: Missing, Ambiguous & Incomplete Requirements

1. **Dual Gateway Selection Coexistence**: The PRD outlines Meta Cloud API as primary, while OpenWA is specified as an open-source gateway option. The mechanism for switching between Meta Cloud API and OpenWA per tenant requires formal backend abstraction interfaces.
2. **Hotel Guest Database Source**: The PRD specifies matching Room Number + PIN, but does not define whether check-in PINs are generated internally by Stock Flow or synced via a Property Management System (PMS) API.
3. **Manual Bank Transfer (IBFT) Screenshot OCR**: In Pakistan, manual IBFT/JazzCash screenshot transfers are common. The PRD currently delegates order payment exclusively to automated webhooks (NayaPay/Raast), leaving manual receipt screenshot validation unaddressed in Phase 1 MVP.

---

## 11. Unanswered Questions Before Implementation

1. **Gateway Abstraction**: Should the backend implement a unified `WhatsAppGatewayAdapter` pattern enabling tenants to run on either Meta Cloud API or OpenWA without changing application code?
2. **Raast P2M vs IBFT**: Will Phase 1 support static Raast IBFT account numbers with OCR receipt validation, or strictly dynamic Raast P2M QR codes with automated HTTP webhooks?
3. **Driver Allocation**: For restaurant self-delivery in Pakistan, how are delivery staff alerted when an order reaches `Out for Delivery` state?

---

## 12. Recommendations & Assumptions

### 12.1 Recommendations (Labeled)
* **[RECOMMENDATION - Architecture]**: Implement a polymorphic `WhatsAppAdapter` interface in FastAPI to abstract Meta Cloud API and OpenWA SDK calls seamlessly.
* **[RECOMMENDATION - Payments]**: Include an AI Vision OCR queue for manual bank transfer (IBFT/JazzCash) payment screenshot verification with mandatory staff button approval before setting order state to `Paid`.
* **[RECOMMENDATION - Operations]**: Use OpenWA's `baileys` lightweight WebSocket engine for self-hosted instances to minimize RAM consumption compared to Puppeteer headless browsers [4, 20].

### 12.2 Explicit Assumptions (Labeled)
* **[ASSUMPTION - Infrastructure]**: Small restaurant tenants in Pakistan will start with Meta's 1,000 free monthly Service conversations or run on a shared OpenWA gateway instance.
* **[ASSUMPTION - Inventory]**: Stock Flow serves as the single source of truth for stock quantities across both restaurant food items and hotel room service amenities.

---
