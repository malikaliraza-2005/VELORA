# Business Onboarding & User Workflows Specification: WhatsApp-First Hospitality SaaS

**Document Title:** `10-business-onboarding-and-user-flows.md`  
**Version:** 1.0  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD], `OpenWA` Documentation [OpenWA], `02-prd-requirements-analysis.md`, `07-system-architecture.md`, `08-database-and-multi-tenancy.md`, `09-ai-workflows-and-reliability.md`  
**Target Market Context:** Pakistan Launch (Karachi, Lahore, Islamabad) & International Expansion  
**Status:** Approved Product Operations, User Experience & Engineering Workflow Baseline  

---

## Executive Summary

This document specifies the end-to-end user journeys, business onboarding workflows, conversational administration flows, and staff escalation protocols for the **WhatsApp-First AI Hospitality SaaS** [PRD 1.1]. 

The platform is engineered around a radical operational principle: **WhatsApp is the sole operational interface for all user personas** [PRD 1.2]. Business owners onboard, upload menus via OCR, update prices, and toggle stock without touching a web dashboard [PRD 1.2, 4.1, 4.8]. Customers browse menus, customize dishes, verify room numbers, pay, and track orders natively in chat with zero web redirects or app downloads [PRD 1.2, 4.4]. Kitchen and front-desk staff manage order fulfillment and human escalations via interactive buttons in dedicated staff WhatsApp groups [PRD 1.2, 4.7, 4.10].

---

## 1. User Roles & Permission Matrix

The system governs operational access across four distinct user roles, enforced by database **Row Level Security (RLS)**, phone number E.164 verification, and contextual role attributes [PRD 3, 4.3; Doc 08].

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

### 1.1 Role Capabilities & Verification Protocols

| User Role | Interface | Verification Protocol | Capabilities & Access Boundaries |
| :--- | :--- | :--- | :--- |
| **End-Customer / Hotel Guest** | WhatsApp Chat | E.164 Phone Number + Room PIN (Hotels) [PRD 4.4] | Browses menus, builds cart, inputs delivery location/room, executes payment, tracks order status, requests human support [PRD 4.4, 4.10]. Read-only access to active tenant catalog. |
| **Business Owner / Operator** | WhatsApp Chat | E.164 Whitelist + Session OTP [PRD 4.8] | Onboards business, uploads PDF/image menus for OCR, updates pricing, toggles item availability, pauses order intake, views daily sales [PRD 4.1, 4.2, 4.8]. Admin write access to tenant catalog. |
| **Kitchen / Front Desk / Housekeeping** | WhatsApp Group | Staff E.164 Mapping (`staff_members.role`) [Doc 08] | Receives interactive order alerts, taps `[Accept]` / `[Dispatch]` buttons, takes over escalated human customer threads [PRD 4.7, 4.10]. Operational write access to order state transitions. |
| **SaaS System Administrator** | Admin Portal | OAuth2 + WebAuthn 2FA [Doc 07] | Meta App OAuth settings, gateway health monitoring, global tenant provisioning, system billing [PRD 3]. Full multi-tenant administrative access. |

---

## 2. Business Owner Onboarding Workflow (100% Conversational)

Onboarding a new restaurant, cloud kitchen, or boutique hotel requires **zero software installation and zero manual web data entry** [PRD 1.2, 4.1]. The entire sequence operates via a conversational state machine on the shared SaaS Onboarding WhatsApp number [PRD 4.1].

```
+-----------------------------------------------------------------------------------+
|                   CONVERSATIONAL OWNER ONBOARDING SEQUENCE                        |
+-----------------------------------------------------------------------------------+
|  1. Owner messages SaaS Onboarding WhatsApp Number ("Start Onboarding")          |
|  2. Bot presents Interactive List: [1. Restaurant] [2. Hotel] [3. Both]          |
|  3. System prompts for Metadata: Business Name, City, Operating Hours, Delivery   |
|  4. Meta Embedded Signup OAuth Link generated -> Owner authorizes WBA Phone ID   |
|  5. Owner sends Menu PDF / JPEG Images via WhatsApp Chat                         |
|  6. Async OCR Pipeline (EasyOCR/Vision LLM) extracts categories, items, prices   |
|  7. Verification Prompts issued for low-confidence items (<85% score)             |
|  8. Catalog committed to PostgreSQL & synced with Stock Flow engine -> LIVE!      |
+-----------------------------------------------------------------------------------+
```

### 2.1 Step-by-Step Onboarding Execution

1. **Initiation**: The business owner sends a greeting (e.g., *"Hi, I want to register my restaurant"*) to the SaaS onboarding number [PRD 4.1.1].
2. **Business Classification**: The bot replies with a Meta Interactive List Message [PRD 4.1.2]:
   * Option 1: *Restaurant / Cloud Kitchen / Bakery*
   * Option 2: *Hotel / Guest House / Room Service*
   * Option 3: *Hybrid (Restaurant & Hotel)*
3. **Metadata Collection**: The bot sequentially prompts for required operational metadata, saving state transactionally after every user message [PRD 4.1.3, 4.1.4]:
   * *Business Name* (e.g., "Karachi Biryani House")
   * *City & Area* (e.g., "Lahore, Gulberg III")
   * *Operating Hours* (e.g., "12:00 PM - 01:00 AM")
   * *Service Modes* (Delivery, Takeaway, Dine-in / Room Service)
   * *Minimum Order Amount & Accepted Payments* (Raast, JazzCash, EasyPaisa, COD)
4. **Meta WhatsApp Cloud API Connection**: The bot sends a secure, single-use Meta Embedded Signup OAuth URL [PRD 4.3.1]. The owner taps the link on their mobile phone, logs into Facebook/Meta Business Manager, selects their WhatsApp Business Account (WBA) phone number, and grants permissions [PRD 4.3.1]. Meta's webhook fires an authorization token back to FastAPI, storing `phone_number_id` and `waba_id` under the tenant record [PRD 4.3.2; Doc 08].
5. **Menu & Service Catalog OCR Import**:
   * The bot prompts: *"Please upload your menu as a PDF, JPEG photo, or CSV file"* [PRD 4.2.1].
   * The asynchronous OCR pipeline (Pillow + EasyOCR / Vision LLM) parses categories, item names, descriptions, sizes, and pricing [PRD 4.2.2].
   * **Verification Guardrail**: For any extracted item where price or name extraction confidence is below **85%**, the bot issues an interactive verification list asking the owner to confirm or correct values [PRD 4.2.3].
6. **Catalog Activation**: Upon owner confirmation, the catalog is committed to PostgreSQL (`categories`, `menu_items`, `stock_inventory`) and Stock Flow, rendering the tenant live for incoming customer orders [PRD 4.2.4].

---

## 3. Conversational Owner Management (Dashboard-Less Admin)

Once onboarded, business owners manage daily operations entirely through natural language text or voice commands sent to their connected business WhatsApp number [PRD 1.2, 4.8].

```
+-----------------------------------------------------------------------------------+
|                     OWNER CONVERSATIONAL COMMAND EXAMPLES                         |
+-----------------------------------------------------------------------------------+
| Owner Message                                -> System Action                     |
| --------------------------------------------    --------------------------------- |
| "Set Pepperoni Pizza price to 1400"          -> Updates `menu_items.price = 1400`  |
| "Mark Large BBQ Chicken out of stock"        -> Sets `stock_inventory.count = 0`  |
| "Pause incoming orders for 45 minutes"       -> Toggles `tenants.is_paused = TRUE`|
| "Show today's sales summary"                 -> Generates daily revenue report    |
+-----------------------------------------------------------------------------------+
```

### 3.1 High-Impact Action Guardrails

To prevent accidental price errors or service shutdowns, high-impact owner commands enforce a **Two-Step Confirmation Flow** [PRD 4.8.2]:

1. *Owner Command*: "Set Zinger Burger price to 45 PKR" (Likely typo for 450 PKR).
2. *System Verification Prompt*: "⚠️ **Price Change Alert**: You are updating **Zinger Burger** from **Rs. 450** to **Rs. 45**. Confirm change? [Yes, Update] [Cancel]" [PRD 4.8.2].
3. *Execution*: Database price updates strictly upon tapping `[Yes, Update]` [PRD 4.8.2].

---

## 4. Restaurant Customer Ordering Journey

The end-to-end customer workflow automates food ordering while guaranteeing 100% price accuracy and zero inventory overselling [PRD 1.2, 4.4, 4.5].

```
+-----------------------------------------------------------------------------------+
|                      RESTAURANT CUSTOMER ORDER FLOW                               |
+-----------------------------------------------------------------------------------+
|  1. Customer messages WhatsApp ("Menu please" or "1 Medium Pepperoni Pizza")     |
|  2. AI parses intent; fetches active categories via `get_menu_categories` tool    |
|  3. Interactive List Message displayed -> Customer selects items & size modifiers|
|  4. Address provided via WhatsApp Location Pin or text string                     |
|  5. Pre-order Atomic Stock Check -> Places 5-Minute Hold in PostgreSQL DB         |
|  6. Payment Link generated (NayaPay Arc / Safepay / Raast QR / COD confirm)      |
|  7. Payment Webhook received -> Order status updated to `Paid`                    |
|  8. Itemized WhatsApp Receipt sent to customer; Interactive Alert to Kitchen Group|
|  9. Kitchen taps [Accept] -> Customer receives "Order Preparing" status update    |
+-----------------------------------------------------------------------------------+
```

### 4.1 Key Step Breakdown

1. **Discovery & Customization**: The customer browses interactive list messages or types natural language (e.g., *"1 Medium Pepperoni Pizza with extra cheese"* in Roman Urdu or English) [PRD 2.2, 4.4.1].
2. **Address & Delivery Calculation**: The customer shares a WhatsApp native Location Pin. Backend spatial logic matches latitude/longitude against the tenant's delivery radius, calculating exact delivery fees [PRD 4.4.2].
3. **Atomic Stock Lock (5-Minute Hold)**: Prior to generating a payment link, the backend executes `reserve_stock_item()`. If stock exists, items are placed on a 5-minute database hold [PRD 4.5.1, 4.5.2]. If sold out, the AI suggests same-category alternatives [PRD 4.5.5].
4. **Zero-Trust Payment Execution**: The system generates a single-use payment link/QR code [PRD 4.6.1]. The order transitions to `Paid` **only** upon receiving an HMAC-verified HTTP webhook from the payment gateway [PRD 1.2, 4.6.2].
5. **Kitchen Alert & Status Updates**: An instant alert hits the staff WhatsApp group with `[Accept Order]` and `[Mark Out for Delivery]` buttons [PRD 4.7.2]. Tapping buttons updates database state and pushes automated WhatsApp tracking messages to the customer [PRD 4.7.3].

---

## 5. Boutique Hotel Guest Service Workflows

Boutique hotels and executive guest houses utilize the platform to automate room service, housekeeping, and maintenance while validating guest identity [PRD 1.1, 4.4.3].

### 5.1 Guest Verification Protocol

To prevent unauthorized room charges or prank orders, room service requests enforce **Guest Identity Verification** [PRD 4.4.3; Doc 02]:

1. *Guest Request*: "Send 2 Club Sandwiches and 1 Green Tea to Room 304."
2. *System Verification Prompt*: "Welcome to Grand Palace Hotel! To confirm your order for Room 304, please enter your **Guest Check-in PIN** or the **Last Name** registered at check-in" [PRD 4.4.3].
3. *Backend Validation*: SQL query matches `room_number` and `guest_pin` against active stays in the database [PRD 4.4.3].
4. *Execution*: Upon match, order is submitted and charged to room folio or prompted for mobile wallet payment [PRD 4.4.3].

### 5.2 Housekeeping & Maintenance Request Routing

```
Guest Request: "Need 2 extra towels and AC repair in Room 208"
               │
               ▼
   [FastAPI NLU Tool Router]
               │
    ┌──────────┴──────────┐
    ▼                     ▼
Category: Housekeeping   Category: Maintenance
Task: Towel Request      Task: Air Conditioner Repair
    │                     │
    ▼                     ▼
Dispatch Alert to        Dispatch Alert to
Housekeeping Group       Engineering Group
`[Mark Completed]`       `[Mark Completed]`
```

---

## 6. Staff Workflows & Human Escalation Handoff

When an interaction exceeds AI capabilities or involves a customer complaint, the platform seamlessly transfers control to human staff [PRD 4.10].

```
+-----------------------------------------------------------------------------------+
|                        HUMAN ESCALATION HANDOFF PIPELINE                          |
+-----------------------------------------------------------------------------------+
|  1. Trigger Event: Explicit keyword ("agent"), Sentiment <0.65, or 3 AI Failures  |
|  2. Backend sets `conversations.is_human_handled = TRUE` in PostgreSQL DB         |
|  3. Automated AI LLM response generation immediately PAUSES for this user thread  |
|  4. Escalation Alert sent to Staff WhatsApp Group with direct customer chat link  |
|  5. Staff member replies directly to customer via business chat                    |
|  6. Staff sends `!resume` command -> Resets `is_human_handled = FALSE` (AI Active)|
+-----------------------------------------------------------------------------------+
```

---

## 7. Common Onboarding Failures & Mitigation Strategies

| Onboarding Failure Scenario | Root Cause | Impact | Automated Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **Low-Quality Menu Image Upload** | Blurry photo, handwritten prices, or low contrast. | OCR price extraction confidence < 85% [PRD 4.2.3]. | System sends interactive WhatsApp verification list prompting owner to confirm or correct low-confidence items [PRD 4.2.3]. |
| **Meta OAuth Authorization Drop-off** | Owner gets confused by Facebook Business Manager login. | Unconnected WhatsApp Cloud API account [PRD 4.3.1]. | Resumable state machine sends automated reminder video guide and magic link via WhatsApp 2 hours later [PRD 4.1.4]. |
| **Owner Phone Spoofing Attempt** | Unauthorized employee attempts to issue owner commands. | Unauthorized price edits or catalog wiping. | Owner commands validated strictly against whitelist of verified E.164 phone numbers with 2FA OTP verification [PRD 4.8]. |
| **Expired 5-Min Payment Hold** | Customer abandons checkout before completing payment. | Inventory locked unnecessarily [PRD 4.5.4]. | Background queue (Upstash QStash) releases hold at T+5 mins, returning items to live stock [PRD 4.5.4]. |

---

## 8. MVP Scope Boundary & Feature Categorization

To guarantee a fast, reliable launch in Pakistan, operational capabilities are split between Phase 1 MVP and Deferred Phase 2 [PRD 6]:

| Feature Area | Confirmed MVP Scope (Phase 1) | Deferred Capability (Phase 2+) |
| :--- | :--- | :--- |
| **Business Onboarding** | WhatsApp Conversational Setup & PDF/Image OCR Menu Import [PRD 6]. | Web Admin Dashboard for bulk menu edits [PRD 6]. |
| **WhatsApp Connection** | Meta Embedded Signup OAuth Flow & OpenWA Dev Gateway [PRD 6]. | Multi-WBA Phone Number Load Balancing [PRD 6]. |
| **Ordering & Service** | 100% Native WhatsApp Cart, 5-Min Stock Holds, Raast/COD Payments [PRD 6]. | Outbound Marketing Broadcasts & Loyalty Points Engine [PRD 6]. |
| **Hotel Capabilities** | In-Room Dining & Maintenance Requests with Room PIN Verification [PRD 6]. | Full Property Management System (PMS) Room Booking Engine [PRD 6]. |
| **Staff Management** | Interactive WhatsApp Group Alerts & Human Escalation Takeover [PRD 6]. | Native iOS/Android Staff Mobile Apps [PRD 6]. |

---

## 9. Sources & Reference Documentation

1. `PRODUCT_REQUIREMENTS.md` [PRD] — Sections 1.1, 1.2, 3, 4.1–4.10, 6.
2. `02-prd-requirements-analysis.md` — Section 3 (FR1–FR10 Functional Requirements).
3. `07-system-architecture.md` — Section 3 (Application Core & Multi-Tenant Routing).
4. `08-database-and-multi-tenancy.md` — Section 3 (PostgreSQL Schema & RLS Policies).
5. `09-ai-workflows-and-reliability.md` — Section 4 (Anti-Hallucination & Tool Calling Guardrails).
