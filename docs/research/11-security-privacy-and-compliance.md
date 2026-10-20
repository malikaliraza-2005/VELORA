# Security, Privacy, & Compliance Specification: WhatsApp-First AI Hospitality SaaS

**Document Title:** `11-security-privacy-and-compliance.md`  
**Version:** 1.0  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD], `OpenWA` Technical Documentation [OpenWA], `02-prd-requirements-analysis.md`, `05-open-wa-gateway-evaluation.md`, `07-system-architecture.md`, `08-database-and-multi-tenancy.md`  
**Target Legal & Platform Jurisdictions:** Pakistan (Prevention of Electronic Crimes Act PECA 2016, SBP Raast/Payment Regulations, Draft PDPB), Meta/WhatsApp Business Terms & Commerce Policies  
**Status:** Approved Security, Privacy & Regulatory Compliance Specification  

---

## Executive Summary

This document establishes the security architecture, data privacy framework, and regulatory compliance roadmap for the **WhatsApp-First AI Hospitality SaaS** operating in Pakistan and expanding internationally [PRD 1.1, 2.1]. 

Because the platform processes end-customer personal data (names, phone numbers, delivery addresses, hotel room numbers), business financial records (catalogs, order histories, sales revenue), and real-time payment transactions (Raast P2M, NayaPay, Safepay, JazzCash) entirely within WhatsApp [PRD 1.1, 1.2, 4.6], maintaining zero-trust cryptographic security and strict regulatory compliance is paramount.

The security framework enforces six non-negotiable operational principles [PRD 1.2]:
1. **Zero-Trust Financial State Machine**: Payment status transitions occur strictly via HMAC SHA-256 signed webhooks from authorized payment gateways [PRD 1.2, 4.6]. The AI LLM layer has **zero write permissions** to database order, price, or inventory tables [PRD 1.2, 4.9].
2. **PostgreSQL Row Level Security (RLS)**: Strict database-level multi-tenant isolation ensures tenant data cannot leak across tenant boundaries under any circumstances [PRD 1.2, 5.1].
3. **Meta Official API Integrity**: Production operations run exclusively on Meta's Official WhatsApp Cloud API to eliminate account-ban risks and guarantee end-to-end transport security [PRD 1.1, 3.1; Doc 05].
4. **Data Minimization & Encryption**: PII (Personally Identifiable Information) and sensitive payment tokens are encrypted at rest using AES-256-GCM and in transit via TLS 1.3 [PRD 5.1].
5. **Hotel Guest Identity Verification**: Room-service ordering enforces two-factor guest verification (Room Number + Guest Check-in PIN / Last Name) prior to folio charging [PRD 4.4.3, 7.0].
6. **Explicit Regulatory Boundary**: Matters requiring legal counsel from a qualified Pakistani advocate (e.g., FBR POS invoicing integration and final SBP data localization rules) are explicitly flagged for formal legal review.

---

## 1. Applicable Pakistani Laws and Regulations

Operating a commercial B2C conversational commerce SaaS in Pakistan requires compliance with national cybercrime legislation, financial regulations, and evolving data protection frameworks [PRD 2.1].

```
+---------------------------------------------------------------------------------------------------+
|                               PAKISTANI REGULATORY COMPLIANCE MATRIX                              |
+--------------------------+-------------------------------------+----------------------------------+
| Regulation / Authority   | Scope & Core Requirement            | SaaS System Enforcement          |
+--------------------------+-------------------------------------+----------------------------------+
| 1. PECA 2016             | Cybercrime, unauthorized data access| Audit logging, encrypted storage,|
|    (Prevention of        | & identity theft penalties          | role-based access control (RBAC) |
|    Electronic Crimes)    | (Sections 3, 4, 6, 10).             | [PRD 5.1; Doc 08].               |
+--------------------------+-------------------------------------+----------------------------------+
| 2. SBP Payment System    | State Bank of Pakistan rules on     | Zero storage of raw card/bank    |
|    Regulations (Raast)   | electronic funds transfer (EFT) and | credentials; HMAC webhook        |
|                          | P2M merchant payments.              | verification [PRD 1.2, 4.6].     |
+--------------------------+-------------------------------------+----------------------------------+
| 3. Draft Personal Data   | Modeled after EU GDPR; requires     | Explicit user opt-in, right to   |
|    Protection Bill (PDPB)| consent, data minimization, and local| deletion (`/forget_me`), 72-hr   |
|                          | storage of critical personal data.  | breach notification [PRD 2.1].   |
+--------------------------+-------------------------------------+----------------------------------+
| 4. FBR Sales Tax & POS   | Federal Board of Revenue rules for  | Itemized WhatsApp receipts;      |
|    Integration Rules     | tax invoicing (PRA/SRA/FBR tax      | tax calculation locked via DB SQL|
|                          | rates: 13%-16% provincial sales tax)| logic [PRD 2.1, 4.6].            |
+--------------------------+-------------------------------------+----------------------------------+
```

### 1.1 Prevention of Electronic Crimes Act (PECA 2016)
* **Section 3 (Unauthorized Access)**: Requires strict authentication and permission controls to prevent unauthorized access to customer and tenant databases [PRD 1.2, 5.1].
* **Section 4 (Unauthorized Data Transmission)**: Mandates secure transmission channels (TLS 1.3) for all customer conversations and financial orders [PRD 5.1].
* **Section 10 (Cyber Terrorism & Critical Infrastructure)**: Outlines penalties for system compromise. Enforced in our platform through rate limiting, DDoS mitigation (Cloudflare), and automated IP banning [PRD 5.1].

### 1.2 State Bank of Pakistan (SBP) Payment Regulations
* **Zero Credential Storage**: The SaaS backend **never prompts for, receives, or stores** customer credit card numbers, bank PINs, or wallet passwords [PRD 1.2, 4.6].
* **Raast P2M & Wallet Integration**: Payment processing relies on single-use dynamic payment links or QR codes generated by SBP-licensed gateways (NayaPay Arc, Safepay, JazzCash, EasyPaisa) [PRD 2.1, 4.6]. Order confirmation occurs exclusively via asynchronous HMAC SHA-256 webhooks [PRD 1.2, 4.6].

### 1.3 Draft Personal Data Protection Bill (PDPB / GDPR Alignment)
* **Consent & Transparency**: Customers initiating chat receive a clear privacy disclosure statement regarding data usage for order fulfillment [PRD 2.1].
* **Data Localization**: Customer phone numbers and order histories for Pakistani merchants must be hosted in compliance with local cloud hosting regulations (e.g., local AWS me-south-1 / Karachi/Lahore data center nodes or regional compliant infrastructure) [PRD 2.1].

---

## 2. Meta and WhatsApp Business Policies

To protect merchant WhatsApp numbers from account suspension, the SaaS architecture strictly adheres to Meta's Business Messaging and Commerce Policies [PRD 1.1, 3.1; Doc 05].

```
+---------------------------------------------------------------------------------------------------+
|                                 META / WHATSAPP POLICY COMPLIANCE                                 |
+--------------------------+-------------------------------------+----------------------------------+
| Policy Dimension         | Meta Requirement Specification      | Platform Implementation          |
+--------------------------+-------------------------------------+----------------------------------+
| 1. Opt-In & Customer     | Conversations must initiate via     | Inbound customer message acts    |
|    Initiation            | customer action or explicit opt-in. | as user-initiated Service window |
|                          |                                     | (1,000 free/month) [PRD 2.1].    |
+--------------------------+-------------------------------------+----------------------------------+
| 2. 24-Hour Messaging     | Free-form messages allowed only     | Outbound status updates after    |
|    Customer Window       | within 24 hours of last user message| 24h require official Meta        |
|                          | [PRD 3.1].                          | Approved Message Templates [3.1].|
+--------------------------+-------------------------------------+----------------------------------+
| 3. AI Transparency &     | Users must know they are interacting| Bot introduces itself as an AI   |
|    Disclosure            | with an automated AI assistant.     | assistant in the opening greeting|
|                          |                                     | [PRD 4.4, 4.9].                  |
+--------------------------+-------------------------------------+----------------------------------+
| 4. Prohibited Commerce   | Prohibition on weapons, drugs, or   | Automated catalog screening      |
|    Categories            | adult services.                     | flags illegal menu items during   |
|                          |                                     | OCR onboarding [PRD 4.2].        |
+--------------------------+-------------------------------------+----------------------------------+
```

### 2.1 Opt-In and Conversation Management
* **Customer-Initiated Service Conversations**: When a customer messages a restaurant or hotel WhatsApp number, a 24-hour customer service window opens [PRD 2.1, 3.1].
* **Free Tier Utilization**: The platform leverages Meta's **1,000 free monthly Service conversations per phone number**, allowing small restaurants to operate at zero messaging cost [PRD 2.1].
* **Template Messages for Proactive Notifications**: Any order status update sent after 24 hours (e.g., delayed delivery or hotel check-out reminders) uses pre-approved Meta Message Templates [PRD 3.1, 4.7].

### 2.2 AI Disclosure & Transparency
* In compliance with Meta AI guidelines and international standards (e.g., EU AI Act Article 50), the system explicitly identifies itself in the initial message:  
  * *"Hello! I am [Restaurant Name]'s AI assistant. I can help you browse the menu, place an order, and answer questions."* [PRD 4.4, 4.9].

---

## 3. Gateway Security: OpenWA vs. Meta Official Cloud API

A critical security and operational decision is selecting the WhatsApp messaging transport gateway [Doc 05].

| Security & Risk Dimension | OpenWA Gateway (Self-Hosted Unofficial) | Meta Official WhatsApp Cloud API | Security Winner & Impact |
| :--- | :--- | :--- | :--- |
| **Transport Encryption** | Terminates E2EE on custom server; relies on headless Chrome Puppeteer memory [OpenWA FAQ]. | Official Meta Cloud infrastructure with end-to-end transport TLS 1.3 [PRD 3.1]. | **Meta Cloud API**: Zero risk of browser memory inspection. |
| **Credential & Session Risks** | Local storage of WhatsApp Web session keys (`Baileys` / `Puppeteer` profiles) susceptible to theft [OpenWA FAQ]. | OAuth 2.0 System User Access Tokens encrypted via AES-256-GCM [PRD 3.1, 5.1]. | **Meta Cloud API**: Cryptographically secured token rotation. |
| **Account Ban & Suspension Risk** | **HIGH**: Automated pattern detection triggers Meta anti-bot blocking [OpenWA FAQ]. | **ZERO**: 100% compliant official API channel [PRD 1.1]. | **Meta Cloud API**: Guarantees merchant business continuity [PRD 1.1]. |
| **Webhook Validation** | Custom HTTP headers or shared secrets [OpenWA 2]. | **HMAC SHA-256 (`X-Hub-Signature-256`)** signed payload validation [PRD 3.2, 5.1]. | **Meta Cloud API**: Prevents webhook spoofing attacks [PRD 3.3]. |

**Final Gateway Decision**: Production environments strictly utilize **Meta's Official WhatsApp Cloud API** [PRD 1.1, 3.1; Doc 05]. OpenWA is restricted to local development and offline mock testing (`docker-compose.dev.yml`) [OpenWA 5; Doc 05].

---

## 4. Protecting Customer Information & Hotel Data

```
+---------------------------------------------------------------------------------------------------+
|                                  DATA PROTECTION & PRIVACY ARCHITECTURE                           |
+--------------------------+-------------------------------------+----------------------------------+
| Data Category            | Sensitivity Level & Threat          | Protection & Safeguard           |
+--------------------------+-------------------------------------+----------------------------------+
| Customer PII (Phone,     | **HIGH**: Identity theft, harassment,| AES-256-GCM encryption at rest;  |
| Address, Name)           | spam targeting.                     | RLS tenant isolation [PRD 5.1].  |
+--------------------------+-------------------------------------+----------------------------------+
| Hotel Guest Records      | **CRITICAL**: Unauthorized room     | 2-Factor Verification (Room + PIN|
| (Room #, Guest Name, PIN)| access, privacy violations.         | / Last Name); auto-expire tokens |
|                          |                                     | [PRD 4.4.3, 7.0].                |
+--------------------------+-------------------------------------+----------------------------------+
| Order & Financial Logs   | **HIGH**: Revenue manipulation,     | Immutable append-only audit log; |
| (Subtotals, Payments)    | payment fraud.                      | HMAC SHA-256 webhooks [PRD 1.2]. |
+--------------------------+-------------------------------------+----------------------------------+
| Meta API Tokens &        | **CRITICAL**: Full phone takeover,  | Encrypted in Supabase Vault /    |
| Payment Gateway Keys     | unauthorized message dispatch.      | Environment Variables [PRD 5.1]. |
+--------------------------+-------------------------------------+----------------------------------+
```

### 4.1 Encryption Standards
* **Data in Transit**: All external API communications (Meta webhooks, payment links, LLM API calls) enforce **TLS 1.3 encryption** [PRD 5.1].
* **Data at Rest**: PostgreSQL tables containing PII (`customers.phone_number`, `orders.delivery_address`) are encrypted using **AES-256-GCM** [PRD 5.1]. API keys and secrets are managed via Supabase Vault or AWS Secrets Manager [PRD 5.1; Doc 08].

### 4.2 Hotel Guest Verification & Privacy Protocol
* To prevent unauthorized room-service billing or fake housekeeping requests, hotel guest workflows enforce strict verification [PRD 4.4.3, 7.0]:
  1. Guest enters Room Number in WhatsApp chat [PRD 4.4.2].
  2. System prompts for **Guest Last Name** or **Check-in PIN** (issued during hotel check-in) [PRD 4.4.3].
  3. System matches input against `hotel_guests` table via atomic database query [PRD 4.4.3; Doc 08].
  4. Only upon 100% match is order/request billed to the room folio [PRD 4.4.3, 7.0].

---

## 5. Security Controls: Isolation, Webhooks & Access Logs

### 5.1 PostgreSQL Row Level Security (RLS) Isolation
Every database query in the SaaS platform executes under a strict multi-tenant context [PRD 1.2, 5.1; Doc 08]:

```sql
-- PostgreSQL RLS Enforcement Policy Example
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_orders ON orders
    FOR ALL
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
```
* Even if an attacker attempts SQL injection or manipulates API payloads, the database engine drops any record matching a different `tenant_id` [PRD 1.2; Doc 08].

### 5.2 HMAC SHA-256 Webhook Signature Verification
All incoming webhooks from Meta and payment gateways undergo cryptographic validation prior to processing [PRD 3.3, 5.1; Doc 07]:

```python
# HMAC Verification Logic in FastAPI Middleware
import hmac
import hashlib

def verify_meta_webhook(payload: bytes, signature: str, app_secret: str) -> bool:
    expected_hash = hmac.new(app_secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected_hash}", signature)
```

### 5.3 Audit Logging & Monitoring
* Every high-impact administrative event (e.g., owner menu price change, stock availability toggle, staff handoff, payment verification) writes an append-only entry to `security_audit_logs` containing `tenant_id`, `actor_phone`, `action`, `ip_address`, and `timestamp` [PRD 4.8, 5.1; Doc 08].

---

## 6. Incident Response & Data Retention Policies

```
+---------------------------------------------------------------------------------------------------+
|                                 INCIDENT RESPONSE & RETENTION SCHEDULE                            |
+--------------------------+-------------------------------------+----------------------------------+
| Data / Incident Type     | Retention / SLA Policy              | Operational Action               |
+--------------------------+-------------------------------------+----------------------------------+
| Customer Chat Logs       | **90 Days Active Storage**          | Auto-archived to encrypted S3    |
|                          |                                     | storage; purged after 365 days.  |
+--------------------------+-------------------------------------+----------------------------------+
| Payment & Order Audit    | **7 Years (Tax Compliance)**        | Immutable PostgreSQL storage for |
| Records                  |                                     | FBR/PRA tax audits [PRD 2.1].    |
+--------------------------+-------------------------------------+----------------------------------+
| Critical Data Breach     | **< 1 Hour Containment**            | Revoke Meta tokens; isolate      |
| Incident SLA             | **< 72 Hour Regulatory Disclosure** | affected tenant RLS context.     |
+--------------------------+-------------------------------------+----------------------------------+
| Customer Deletion Request| **Immediate (`/forget_me`)**        | Anonymize PII in `customers` DB  |
|                          |                                     | while retaining anonymized sales.|
+--------------------------+-------------------------------------+----------------------------------+
```

---

## 7. Security Checklist for MVP vs. Production

```
========================================================================================================================
SECURITY & COMPLIANCE IMPLEMENTATION CHECKLIST
========================================================================================================================
Security Domain            MVP Stage Requirement (Phase 1 Launch)       Production Scale Requirement (Phase 2)
------------------------------------------------------------------------------------------------------------------------
1. Webhook Protection      HMAC SHA-256 signature verification [3.3]     Automated IP CIDR Whitelisting (Cloudflare)
2. Multi-Tenancy           PostgreSQL Row Level Security (RLS) [1.2]     Dedicated tenant database schemas / clusters
3. Gateway Security        Meta Official Cloud API via OAuth [3.1]       Multi-region Meta App failover redundancy
4. AI Safety Guardrails    Zero DB write permissions for LLM [1.2, 4.9]   Real-time prompt-injection filter models
5. Data Encryption         TLS 1.3 in transit + AES-256 at rest [5.1]     Automated key rotation via Supabase Vault
6. Staff Permissions       Role-Based Access (Owner/Staff) via WhatsApp  Multi-factor Auth for Web SaaS Admin [3.0]
7. Audit Logging           Database event logging for price/stock edits   Automated SIEM logging (Datadog / Logtail)
========================================================================================================================
```

---

## 8. Specific Issues Flagged for Qualified Legal Counsel

The following legal and regulatory questions require formal review by a qualified Pakistani corporate/tech advocate prior to commercial launch:

1. **FBR POS Integration Mandate**: Confirming provincial sales tax collection rules (Punjab Revenue Authority PRA vs. Sindh Revenue Board SRB vs. FBR) for WhatsApp-generated electronic invoices [PRD 2.1].
2. **Data Localization Requirements**: Verifying whether SBP or the draft PDPB requires customer order databases to be physically hosted on servers located within Pakistan [PRD 2.1].
3. **Electronic Signature & Contract Law**: Validating customer agreement enforceability for WhatsApp interactive button order confirmations under Pakistan's Electronic Transactions Ordinance (ETO 2002) [PRD 1.2, 4.6].

---

## 9. References & Verification Sources

1. **PRD & Product Requirements**: `PRODUCT_REQUIREMENTS.md` [PRD 1.1, 1.2, 2.1, 3.1, 4.2, 4.6, 4.8, 4.9, 5.1].
2. **OpenWA Technical Documentation**: `OpenWA` Gateway Reference [OpenWA 1, 2, 5, FAQ].
3. **System Architecture Specification**: `07-system-architecture.md` [Doc 07].
4. **Database & Multi-Tenancy Specification**: `08-database-and-multi-tenancy.md` [Doc 08].
5. **Legislation**: Pakistan Prevention of Electronic Crimes Act (PECA 2016); Draft Personal Data Protection Bill (PDPB); State Bank of Pakistan Payment Systems Regulations.
