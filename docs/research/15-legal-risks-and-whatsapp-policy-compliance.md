# Legal Risks & WhatsApp Policy Compliance Report

**Document Title:** `15-legal-risks-and-whatsapp-policy-compliance.md`  
**Version:** 1.0  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD], `OpenWA` Technical Documentation [OpenWA], `11-security-privacy-and-compliance.md` [Doc 11], `05-open-wa-gateway-evaluation.md` [Doc 05]  
**Target Market Context:** Pakistan Launch (Karachi, Lahore, Islamabad) & International Expansion (EU/UK, US, Brazil, MENA) [PRD 2.1]  
**Status:** Approved Legal Risk Assessment, Policy Compliance & Mitigation Specification  

---

## Executive Summary

This document provides a legal risk assessment, regulatory compliance blueprint, and Meta platform policy analysis for the **WhatsApp-First AI Hospitality SaaS** [PRD 1.1]. The platform enables independent restaurants, cloud kitchens, and boutique hotels to automate food ordering, delivery, and hotel room service natively within WhatsApp [PRD 1.1, 2.1].

Operating a commercial SaaS built on WhatsApp messaging infrastructure introduces legal, regulatory, and technical continuity considerations. These factors stem from **Meta’s Business Messaging Policies**, global data privacy legislation (**Pakistan Data Regulations / Draft PDPB**, **EU/UK GDPR**, **US TCPA / CAN-SPAM**), and the structural liabilities associated with using unofficial browser-automation frameworks (such as **OpenWA Gateway**) versus the **Official Meta WhatsApp Business Cloud API** [PRD 1.1, 3.1; OpenWA 1, 4; Doc 05, 11].

### Key Strategic Conclusions
1. **Mandatory Production Architecture**: The SaaS MUST deploy exclusively on **Meta’s Official WhatsApp Business Cloud API** via Embedded Signup OAuth for commercial production [PRD 1.1, 3.1; Doc 05]. Deploying client-facing commercial operations on unofficial scraping libraries or headless browser gateways (e.g., OpenWA Gateway, Puppeteer, Baileys) creates an unacceptably high risk of **permanent WhatsApp number restrictions**, breach of Meta’s Terms of Service, and platform operational disruption [OpenWA FAQ; Doc 05].
2. **Zero-Trust Financial & Privacy Isolation**: To ensure compliance with payment regulations (SBP Raast, PCI-DSS) and privacy frameworks, the backend enforces a **zero-trust state machine** where AI LLMs possess zero write permissions to financial/inventory database tables, and tenant data is isolated via PostgreSQL **Row Level Security (RLS)** [PRD 1.2, 4.9, 5.1; Doc 08, 11].
3. **Explicit Dual-Opt-In & Audit Logging**: Both B2C end-customers (diners/guests) and B2B merchant operators must go through explicit consent flows, with timestamped consent records and audit logs preserved to shield the SaaS platform from third-party liabilities [PRD 1.2; Doc 11].
---

## 1. Meta Official Policies & Compliance Requirements

Operating on the WhatsApp Business Platform requires strict adherence to three primary Meta governance frameworks: the **WhatsApp Business Terms**, the **WhatsApp Business Messaging Policy**, and the **WhatsApp Commerce Policy**.

```
+---------------------------------------------------------------------------------------------------+
|                               META POLICY COMPLIANCE MATRIX                                       |
+--------------------------+--------------------------+-----------------------+---------------------+
| POLICY DOMAIN            | META MANDATE             | SAAS IMPLEMENTATION   | POLICY ENFORCEMENT  |
+--------------------------+--------------------------+-----------------------+---------------------+
| Customer Opt-In          | Explicit, unambiguous    | In-chat opt-in on     | Tiered messaging    |
|                          | customer consent prior   | first message; clear  | velocity caps or    |
|                          | to outbound templates    | trigger keywords      | number restriction  |
+--------------------------+--------------------------+-----------------------+---------------------+
| 24-Hour Messaging        | Business replies within  | Session messages free | Outbound blocked    |
| Window                   | 24h of customer message  | within 24h; pre-appr. | unless using paid   |
|                          | are unconstrained        | templates thereafter  | Meta Template API   |
+--------------------------+--------------------------+-----------------------+---------------------+
| Template Message         | Outbound messages outside| Pre-vetted structured | Template rejection  |
| Quality                  | 24h window require Meta  | templates; zero promotional| or WBA account  |
|                          | approval & categorization| content in utility alerts| restriction       |
+--------------------------+--------------------------+-----------------------+---------------------+
| Commerce Policy          | Requires approved items; | Strict restriction to | Immediate business  |
| Compliance               | verified seller entity   | verified food/beverage| account review      |
|                          | required                 | & lodging services    | & API access revoke |
+--------------------------+--------------------------+-----------------------+---------------------+
```

### 1.1 Customer Opt-In Requirements
* **Inbound Customer Initiation**: When a customer messages a restaurant or hotel WhatsApp number (e.g., asking for a menu or room service), the customer initiates the conversation [PRD 1.2, 4.4]. Under Meta policies, this constitutes implicit consent for **Service Conversations** within a 24-hour window [PRD 2.1].
* **Outbound Promotional & Utility Consent**: Outbound alerts sent outside the 24-hour window (e.g., order tracking updates, order completion receipts) require explicit prior opt-in [PRD 4.7]. The platform enforces an initial greeting opt-in prompt:  
  > *"Welcome to [Restaurant Name]! By continuing, you agree to receive order receipts and status updates via WhatsApp. Reply STOP to opt out at any time."*
* **Opt-Out Mechanics**: Customers sending `STOP`, `UNSUBSCRIBE`, or `CANCEL` are immediately flagged in the database (`customers.opted_out = TRUE`), blocking all automated outbound message attempts [Doc 08, 10].

### 1.2 The 24-Hour Messaging Window & Template Governance
* **Session Messages**: Any message sent by the business within 24 hours of the customer’s last incoming message is classified as a Session Message [PRD 2.1]. The AI assistant can send free-form text, interactive list messages (up to 10 items), and reply buttons [PRD 4.1].
* **Template Messages**: Outbound messages sent outside the 24-hour window MUST use pre-approved Meta Message Templates categorized as **Utility** (e.g., order confirmation, delivery status) or **Marketing** [PRD 2.1, 4.7].
* **Template Strictness**: Templates cannot contain variable parameters with promotional text or links in Utility categories. Submitting mismatched templates triggers Meta quality downgrades [Doc 11].
---

## 2. Technical Evaluation & Legal Risks: OpenWA vs. Meta Cloud API

A core architectural evaluation compares self-hosted WhatsApp Web automation engines (**OpenWA Gateway**) against **Meta’s Official WhatsApp Business Cloud API** [PRD 3.1; OpenWA 1, 4; Doc 05].

```
+---------------------------------------------------------------------------------------------------+
|                        OPENWA GATEWAY VS. META CLOUD API LEGAL & OPERATIONAL EVALUATION           |
+------------------------------+------------------------------------+-------------------------------+
| EVALUATION DIMENSION         | OPENWA GATEWAY (UNOFFICIAL)        | META OFFICIAL CLOUD API       |
+------------------------------+------------------------------------+-------------------------------+
| **Legal Status**             | Unofficial protocol emulation;     | Official enterprise API;      |
|                              | Potential Meta ToS review          | 100% compliant with Meta ToS  |
+------------------------------+------------------------------------+-------------------------------+
| **Account Restriction Risk** | **HIGH / UNPREDICTABLE**:          | **ZERO BAN RISK** for         |
|                              | Detection triggers restriction     | policy-compliant messaging    |
+------------------------------+------------------------------------+-------------------------------+
| **Operational Stability**    | Vulnerable to Meta Web protocol    | 99.9% SLA backed by Meta      |
|                              | updates; requires engine patches   | global infrastructure         |
+------------------------------+------------------------------------+-------------------------------+
| **Merchant Onboarding**      | Manual QR code scan; phone must    | **Embedded Signup OAuth**:    |
|                              | remain online & powered            | 2-minute web authorization    |
+------------------------------+------------------------------------+-------------------------------+
| **Interactive UI Support**   | Simulated text/buttons; rendered   | **Native Interactive Lists**  |
|                              | inconsistently on native apps      | & Reply Buttons (Native UX)   |
+------------------------------+------------------------------------+-------------------------------+
| **Scaling Limits**           | 1 session per chromium process;    | Scalable to millions of       |
|                              | Heavy RAM overhead (512MB-1GB)     | messages/day via webhooks     |
+------------------------------+------------------------------------+-------------------------------+
```

### 2.1 OpenWA Gateway Risk Analysis
* **Protocol Emulation & Scraping Liabilities**: OpenWA operates by driving headless Chrome instances via Puppeteer (`whatsapp-web.js`) or intercepting WebSockets (`baileys`) [OpenWA 4; Doc 05]. Meta’s Data Terms restrict unauthorized browser automation and protocol emulation [Doc 05, 11].
* **Detection & Automated System Mechanisms**: Meta employs automated filters that monitor behavioral patterns: connection handshake signatures, typing delays, message velocity, and socket behaviors [OpenWA FAQ; Doc 05]. Unofficial sessions exhibit non-human fingerprints that risk automated phone number restrictions [OpenWA FAQ; Doc 05].
* **Business Continuity Impact**: If a client restaurant or hotel's primary WhatsApp number is restricted due to unofficial gateway usage, the merchant suffers immediate operational delay, reputation damage, and financial loss [Doc 05].

### 2.2 Production Architectural Mandate
* **Official API Deployment**: The SaaS MUST deploy on Meta's official WhatsApp Business Cloud API for all live merchant environments [PRD 1.1, 3.1; Doc 05].
* **OpenWA Sandbox Role**: OpenWA Gateway is approved solely for internal developer testing, local offline sandbox integration, and CI/CD automated test pipelines (`docker compose -f docker-compose.dev.yml up -d`), keeping local development isolated from Meta API quotas [OpenWA 5; Doc 05].

---

## 3. Account Restrictions, Quality Ratings & Risk Mitigation

When operating on Meta's official Cloud API, phone numbers are assigned a **Quality Rating** (Green / High, Yellow / Medium, Red / Low) and **Messaging Tier Limits** (1,000, 10,000, 100,000, or Unlimited business-initiated conversations / 24 hours).

```
+---------------------------------------------------------------------------------------------------+
|                        WHATSAPP NUMBER QUALITY RATING & RISK PIPELINE                             |
+-----------------------+----------------------------------+----------------------------------------+
| QUALITY STATE         | CAUSE / TRIGGER                  | ENFORCEMENT ACTION                     |
+-----------------------+----------------------------------+----------------------------------------+
| **Green (High)**      | Low user block/report rate;      | Full tier capacity allowed;            |
|                       | High template engagement         | Normal messaging operations            |
+-----------------------+----------------------------------+----------------------------------------+
| **Yellow (Medium)**   | Rising user blocks; feedback     | Warning issued in Meta Business Manager|
|                       | on broadcast templates           | Quality rating monitored for 7 days    |
+-----------------------+----------------------------------+----------------------------------------+
| **Red (Low)**         | Higher user block rate (> 1-2%); | Messaging tier downgraded;             |
|                       | Unfavorable template engagement  | Number flagged for review              |
+-----------------------+----------------------------------+----------------------------------------+
| **Restricted**        | Policy non-compliance;           | Outbound messaging blocked;            |
|                       | Unofficial gateway detection     | Appeal required via Business Manager   |
+-----------------------+----------------------------------+----------------------------------------+
```

### 3.1 Common Causes of Quality Drops
1. **Unsolicited Messaging**: Sending promotional offers or menus to phone numbers without prior opt-in [PRD 4.7; Doc 11].
2. **High User Block Rates**: If > 1% of recipients tap "Block" on a template message, Meta automatically downgrades the number's Quality Rating [Doc 11].
3. **Mismatched Template Messaging**: Using Utility templates to deliver promotional content to bypass higher template fees [Doc 11, 14].
4. **Sudden Message Volume Spikes**: Sending thousands of outbound messages from a brand-new WhatsApp Business Account without following Meta's number warming protocol [Doc 11].

### 3.2 Risk-Reduction Measures
* **Automated Quality Monitoring**: The FastAPI backend subscribes to Meta's `phone_number_quality_update` webhooks [PRD 3.2]. If a tenant's Quality Rating drops to Yellow or Red, the system automatically pauses promotional broadcasts and alerts staff [PRD 4.10; Doc 11].
* **Gradual Number Warming Protocol**: New tenant phone numbers start at Tier 1 (1,000 business-initiated conversations/24h) and scale organically as high template delivery and low block rates are maintained [Doc 05, 11].
* **In-Chat Unsubscribe Option**: Every broadcast template includes a quick-reply button `[Unsubscribe]`, allowing users to opt out cleanly without blocking the business WhatsApp number [Doc 10, 11].
---

## 4. Legal Responsibilities & Regulatory Compliance Across Jurisdictions

The SaaS operates as a multi-tenant platform handling customer personal data, phone numbers, delivery addresses, hotel room numbers, and financial transactions [PRD 1.1, 2.1, 4.2].

```
========================================================================================================
INTERNATIONAL DATA PRIVACY & COMPLIANCE MATRIX
========================================================================================================
Jurisdiction    Primary Data Framework            Core Legal Requirements               SaaS Compliance Mechanism
--------------------------------------------------------------------------------------------------------
Pakistan        Digital Privacy Rules & Draft PDPB Data confidentiality; User consent;   AES-256 encryption at rest;
                SBP Payment Rules [PRD 2.1]       No raw financial data storage         Zero raw card storage; RLS isolation
--------------------------------------------------------------------------------------------------------
EU & UK         GDPR & UK GDPR                    Article 6 Lawful basis; Art. 17       Strict opt-in logs; `/forget_me`
                EU AI Act (Article 50)            Right to Erasure; AI Disclosure       automated deletion; AI bot disclosure
--------------------------------------------------------------------------------------------------------
United States   TCPA Messaging Guidelines         Prior express consent;                Strict opt-in tracking; Quiet hours
                CAN-SPAM Standards                Opt-out honoring; Easy opt-out        enforcement (21:00-08:00 local time)
========================================================================================================
```

### 4.1 Pakistani Law & State Bank Regulations
* **Electronic Data Regulations**: Mandates data confidentiality, unauthorized access prevention, and secure data storage [Doc 11].
* **Draft Personal Data Protection Bill (PDPB)**: Requires explicit user consent for personal data processing, data minimization, and local data hosting compliance [Doc 11].
* **State Bank of Pakistan (SBP) Payment Directives**: B2C order payment flows must utilize SBP-licensed gateways (Raast P2M, NayaPay Arc, Safepay, JazzCash, EasyPaisa) [PRD 2.1, 4.6]. The SaaS backend stores ZERO raw debit/credit card numbers or bank PINs, relying entirely on HMAC SHA-256 signed payment webhooks [PRD 1.2, 4.6].

### 4.2 EU/UK GDPR & EU AI Act (Article 50) Compliance
* **Lawful Basis for Processing (Article 6)**: Customer chat processing relies on *Contract Performance* (fulfilling a food order or room service request) and *Explicit Consent* for promotional alerts [PRD 1.1, 4.4].
* **Right to Erasure / "Right to be Forgotten" (Article 17)**: If a customer sends `/forget_me` or requests data deletion, an automated queue purges customer personal identifiers (`customers.phone_number`, `customers.name`, `delivery_address`) from the PostgreSQL database while preserving anonymized financial ledger entries for tax audit compliance [Doc 08, 11].
* **EU AI Act Article 50 (AI Transparency)**: The assistant explicitly identifies itself as an automated AI in its initial greeting:  
  > *"Hello! I am [Restaurant Name]'s AI Ordering Assistant. I can help you browse our menu and place orders."* [PRD 4.4; Doc 09].

### 4.3 US TCPA & CAN-SPAM Compliance
* **Prior Express Consent**: Required before dispatching automated SMS/WhatsApp marketing alerts to US phone numbers [Doc 11].
* **Quiet Hours Enforcement**: Outbound automated marketing alerts are blocked between 21:00 and 08:00 in the recipient's local timezone (`Asia/Karachi`, `America/New_York`, `Europe/London`) [PRD 2.2; Doc 12].

---

## 5. Allocation of Responsibility: SaaS Platform vs. Merchant Customer

To shield the SaaS from merchant misuse (e.g., a restaurant uploading an unauthorized phone number list to send spam), a robust **Data Processing Agreement (DPA)** and **Terms of Service (ToS)** delineate legal boundaries.

```
+---------------------------------------------------------------------------------------------------+
|                               RESPONSIBILITY ALLOCATION MATRIX                                    |
+-----------------------------------+-----------------------------------+---------------------------+
| COMPLIANCE / OPERATIONAL TASK     | RESPONSIBLE PARTY                 | SAAS ENFORCEMENT METHOD   |
+-----------------------------------+-----------------------------------+---------------------------+
| Obtaining Customer Opt-In Consent | Merchant (Restaurant/Hotel)       | ToS clause + Opt-In       |
|                                   |                                   | confirmation prompts      |
+-----------------------------------+-----------------------------------+---------------------------+
| Meta Business Account Verification| Merchant / SaaS Onboarding        | Meta Embedded Signup OAuth|
|                                   |                                   | Flow [PRD 1.2, 3.1]       |
+-----------------------------------+-----------------------------------+---------------------------+
| Data Storage & Security Isolation | SaaS Platform                     | PostgreSQL Row Level      |
|                                   |                                   | Security (RLS) [PRD 1.2]  |
+-----------------------------------+-----------------------------------+---------------------------+
| Payment Processing Security       | SaaS + Licensed Gateway           | HMAC SHA-256 Webhooks;    |
|                                   |                                   | Zero card data storage    |
+-----------------------------------+-----------------------------------+---------------------------+
| Content Policy Adherence (Goods)  | Merchant                          | ToS terms banning         |
|                                   |                                   | unverified product sales  |
+-----------------------------------+-----------------------------------+---------------------------+
```

### 5.1 SaaS Customer Terms of Service (ToS) Clauses
* **Anti-Spam Warranty**: Merchants warrant that all customer phone numbers ingested into the SaaS have been collected in compliance with applicable messaging and privacy rules.
* **Indemnification Clause**: Merchants agree to defend, indemnify, and hold harmless the SaaS platform against legal claims or Meta API suspensions resulting from the merchant's unauthorized messaging or policy violations.
* **Immediate Suspension Right**: The SaaS reserves the right to immediately suspend any tenant account that triggers Meta quality downgrades (Red Rating) or sends unsolicited promotional spam [PRD 4.10; Doc 11].
---

## 6. Comprehensive Legal & Operational Risk Assessment

Risks are categorized and ranked by Likelihood, Impact, and Mitigation Cost.

| Risk ID | Risk Description | Likelihood | Operational Impact | Mitigation Cost | Core Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R1** | Number Status Change via Unofficial Gateway | HIGH (if OpenWA) | High (Service Pause) | Low | Deploy 100% on Meta Official Cloud API; Prohibit OpenWA for live merchant numbers [PRD 1.1, 3.1; Doc 05]. |
| **R2** | Meta Policy Update / Template Review | Medium | Moderate | Low | Implement automated quality monitoring webhooks & auto-pause on Red rating [Doc 11]. |
| **R3** | Cross-Tenant Data Access | Low | High | Low | Enforce PostgreSQL Row Level Security (RLS) on all tenant queries [PRD 1.2, 5.1]. |
| **R4** | Unverified Payment Link Discrepancies | High | Moderate | Low | Zero-Trust State Machine: Orders mark Paid ONLY via HMAC payment webhooks [PRD 1.2, 4.6]. |
| **R5** | Regional Data Directives / Privacy Rules | Low | Moderate | Medium | AES-256 encryption at rest; TLS 1.3; Automated `/forget_me` data purging [Doc 11]. |
| **R6** | Unverified Hotel Folio Charges | Medium | Moderate | Low | Enforce Guest Verification (Room Number + Guest PIN / Last Name) [PRD 4.4.3]. |

---

## 7. Business Continuity & System Resilience

To safeguard platform operations against API outages, tenant number disconnections, or gateway failures, the SaaS implements a resilient business continuity framework.

| Incident Trigger | Automated Backend Action | Recovery Time Target (RTO) |
| :--- | :--- | :--- |
| **Meta Webhook Outage** | Redis Queue holds incoming events (24h TTL) for retry execution | RTO < 5 minutes; Zero message loss via exponential backoff queue |
| **Tenant Quality Change (Red Rating)** | Outbound broadcasts paused; System reverts to 1:1 service msgs | Immediate automated alert sent to staff WhatsApp group [PRD 4.10] |
| **Primary Payment Gateway Timeout** | Failover to secondary gateway link generation (e.g., SadaBiz) | Seamless switch: NayaPay Arc -> Safepay / Raast P2M [PRD 4.6; Doc 12] |
| **Database Failure** | Automated daily Supabase point-in-time recovery (PITR) restore | RTO < 1 hour; RPO < 5 minutes via PostgreSQL write-ahead log (WAL) sync |

---

## 8. Prioritized Compliance & Launch Readiness Checklist

### Phase 1: MVP Launch Readiness (Mandatory Before First Live Merchant)
- [x] **Meta Cloud API Infrastructure**: Connect tenant accounts exclusively via Meta's official **Embedded Signup OAuth Flow** [PRD 1.2, 3.1; Doc 05].
- [x] **HMAC Signature Verification**: Enforce `X-Hub-Signature-256` SHA-256 verification on all incoming Meta webhooks [PRD 3.3, 4.3; Doc 07].
- [x] **Zero-Trust Payment Validation**: Configure payment state updates strictly through HMAC-verified gateway webhooks (Raast, NayaPay, Safepay, SadaBiz) [PRD 1.2, 4.6; Doc 12].
- [x] **Database Multi-Tenancy**: Apply PostgreSQL **Row Level Security (RLS)** policies across all application tables [PRD 1.2, 5.1; Doc 08].
- [x] **AI Transparency & Bot Disclosure**: Program AI assistant initial greetings to explicitly declare automated bot identity [PRD 4.4; Doc 09].
- [x] **Opt-In / Opt-Out Mechanics**: Implement automated opt-in logging and immediate `STOP` opt-out suppression [PRD 4.4; Doc 10].
- [x] **Hotel Guest Verification**: Enforce Room Number + Guest PIN / Last Name validation prior to room service folio posting [PRD 4.4.3].

### Phase 2: International Scale & Enterprise Safeguards
- [ ] **Automated Data Purging (`/forget_me`)**: Deploy automated GDPR right-to-be-forgotten data anonymization queues [Doc 11].
- [ ] **Quality Rating Monitoring**: Enable automated webhook listeners for `phone_number_quality_update` with auto-pausing on Yellow/Red ratings [Doc 11].
- [ ] **Merchant Legal Agreement**: Deploy formal SaaS Terms of Service (ToS) and Data Processing Agreement (DPA) containing messaging warranties and merchant indemnification clauses.
- [ ] **Pakistani Legal Counsel Review**: Engage a qualified Pakistani technology/corporate attorney to review provincial sales tax invoicing compliance (PRA/SRB/FBR) and State Bank data localization rules.

---

## Sources & Reference Documentation

1. **`PRODUCT_REQUIREMENTS.md`**: Core product specification, operational principles, target markets, user roles, functional requirements (FR1-FR10), and safety guardrails [PRD 1.1-4.10].
2. **OpenWA Technical Documentation**: Open-source WhatsApp API gateway documentation, architecture, and FAQ [OpenWA 1-7].
3. **Meta WhatsApp Business Platform Terms & Policies**: Official Meta Business Messaging Policy, Commerce Policy, and 24-hour window rules.
4. **Meta Developer Documentation (Cloud API)**: Webhook signatures (`X-Hub-Signature-256`), Embedded Signup OAuth, and Interactive Message specifications.
5. **Electronic Data Directives (Pakistan)**: Official electronic data protection framework.
6. **EU General Data Protection Regulation (GDPR)**: Official Regulation on data protection, privacy, and right to erasure.
7. **`11-security-privacy-and-compliance.md`**: SaaS security architecture, encryption, HMAC validation, and compliance baseline [Doc 11].
8. **`05-open-wa-gateway-evaluation.md`**: OpenWA Gateway vs. Meta Cloud API technical and legal comparative evaluation [Doc 05].
9. **`12-pakistan-payments-and-localization-v2.md`**: Payment gateway webhook verification, Raast P2M, NayaPay Arc, Safepay, and SadaBiz integration blueprint [Doc 12].
