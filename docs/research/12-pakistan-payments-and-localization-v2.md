# Payment Infrastructure & Localization Strategy: Pakistan-First AI SaaS

**Document Title:** `12-pakistan-payments-and-localization-v2.md`  
**Version:** 2.0 (Updated with SadaPay & SadaBiz Integration)  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD], `01-executive-summary-and-feasibility.md`, `03-pakistan-market-research.md`, `06-technology-stack-and-cost-analysis.md`, `11-security-privacy-and-compliance.md`  
**Target Market Context:** Pakistan Launch (Karachi, Lahore, Islamabad) & Phase 2 International Expansion  
**Status:** Approved Payment Engineering & Localization Blueprint  

---

## Executive Summary

This document establishes the payment gateway architecture, subscription collection mechanisms, and localization framework for the **WhatsApp-First AI Hospitality SaaS** [PRD 1.1, 2.1]. The platform enables independent restaurants, cloud kitchens, and boutique hotels in Pakistan to automate food ordering, delivery, and room service natively within WhatsApp [PRD 1.1, 1.2].

Payment processing operates across two distinct commercial channels [PRD 1.2, 4.6]:
1. **B2B SaaS Subscription Collection**: Collecting monthly platform subscription fees (e.g., PKR 5,000 to PKR 15,000/month per merchant) from restaurant and hotel owners [PRD 2.1; Doc 06].
2. **B2C End-Customer Order Payments**: Processing customer food orders and hotel room service charges via direct digital payments or Cash on Delivery (COD) [PRD 2.1, 4.6].

A central commercial advantage of the platform in Pakistan is enabling merchants to bypass third-party aggregator commissions (25%–35%) by utilizing State Bank of Pakistan's **Raast P2M (0% transaction fee)**, **SadaPay / SadaBiz**, **NayaPay Arc**, **Safepay**, **PayPro**, **JazzCash**, **EasyPaisa**, and automated **Interbank Funds Transfer (IBFT)** verification [PRD 2.1, 4.6].

---

## 1. B2B SaaS Subscription Collection (Merchant-to-SaaS)

### 1.1 Commercial Pricing Structure in PKR
Subscription plans are priced in Pakistani Rupees (PKR) to match local merchant cash flow dynamics [PRD 2.1; Doc 06]:

* **Starter / Cloud Kitchen Tier**: **PKR 5,000 / month** (~$18 USD) — Includes up to 1,000 monthly orders, automated WhatsApp catalog, and 1 staff group alert connection [Doc 06].
* **Professional Restaurant Tier**: **PKR 10,000 / month** (~$36 USD) — Includes up to 3,000 monthly orders, OCR menu updates, real-time inventory tracking via Stock Flow, and owner chat commands [PRD 4.8; Doc 06].
* **Enterprise & Boutique Hotel Tier**: **PKR 20,000 / month** (~$72 USD) — Includes unlimited orders, hotel room service verification (Room + PIN), multi-department staff routing, and custom reports [PRD 4.4, 4.8].

### 1.2 Subscription Collection Channels in Pakistan

Collecting recurring B2B software payments in Pakistan requires supporting channels aligned with local banking infrastructure [PRD 2.1; Doc 03]:

```
+-----------------------------------------------------------------------------------+
|                     B2B SAAS SUBSCRIPTION COLLECTION CHANNELS                     |
+--------------------------+--------------------------+-----------------------------+
| 1. RAAST P2M / DIRECT    | 2. SADABIZ INVOICE LINKS | 3. PAYPRO 1LINK BILLING     |
| - 0% transaction fee     | - Instant payment link   | - 14-digit bill ID          |
| - Instant bank push      | - Card & Raast accepted  | - Payable via any banking   |
| - Automated DB webhook   | - Sent via WhatsApp chat |   app or ATM in Pakistan    |
+--------------------------+--------------------------+-----------------------------+
                           | 4. CARD AUTO-DEBIT (CARD) |
                           | - NayaPay Arc / Safepay  |
                           | - Tokenized debit/credit |
                           +--------------------------+
```

1. **Raast P2M & Direct Bank Push (MVP Default)**: On the monthly billing date, the SaaS engine generates an automated WhatsApp billing message containing a Raast P2M QR code or IBFT account IBAN with a unique reference code (e.g., `SAAS-TNT-0102`) [PRD 2.1, 4.8]. Upon receiving the payment, the SBP Raast webhook automatically updates the tenant status to `Active` [PRD 1.2, 4.6].
2. **SadaBiz Business Payment Links**: The SaaS dispatches a dynamic SadaBiz invoice link directly to the owner's WhatsApp number [PRD 4.8]. The owner can pay using any local debit/credit card or bank transfer, triggering a real-time web callback to extend the subscription [PRD 1.2, 4.6].
3. **PayPro 1LINK Billing Integration**: Generates a standardized 14-digit PayPro consumer ID that merchants can pay through any mobile banking application or ATM in Pakistan [Doc 06].
4. **Tokenized Card Recurring Auto-Debit**: For larger clients equipped with corporate debit/credit cards, NayaPay Arc or Safepay tokenizes the card details during initial onboarding for automated monthly charging [PRD 4.6].

---

## 2. B2C End-Customer Ordering Payments (Customer-to-Merchant)

End-customer checkout takes place 100% inside WhatsApp text, interactive lists, and reply buttons [PRD 1.2, 4.1]. Payment collection options are structured to maximize conversion and eliminate payment fraud [PRD 1.2, 4.6].

### 2.1 Primary Payment Channels for Pakistani Merchants

1. **State Bank of Pakistan Raast P2M (0% MDR)**:
   * **Cost**: 0% Merchant Discount Rate (MDR) [PRD 2.1].
   * **Settlement**: Real-time instant bank settlement [PRD 2.1].
   * **Flow**: The bot presents a dynamic Raast QR image or a deep link. The customer approves the transfer in their bank app, and SBP dispatches an instant webhook to update order status to `Paid` [PRD 1.2, 4.6].
2. **SadaPay / SadaBiz Checkout**:
   * **Cost**: ~1.5% – 3.0% for card processing; 0% for Raast.
   * **Settlement**: T+1 Business Day.
   * **Flow**: The bot generates a single-use SadaBiz link bound to `order_id`. Supports local PayPak, Visa, Mastercard, and digital wallets with HMAC webhook validation [PRD 1.2, 4.6].
3. **NayaPay Arc & Safepay Gateway Links**:
   * **Cost**: 2.2% – 3.3% + Sales Tax [PRD 4.6].
   * **Settlement**: T+1 to T+2 Business Days.
   * **Flow**: Generates an encrypted single-use payment link for debit/credit card checkout [PRD 4.6].
4. **Cash on Delivery (COD) Interactive Protocol**:
   * **Market Volume**: Accounts for 60%–70% of food delivery orders in Pakistan [Doc 03].
   * **Safety Mechanism**: To prevent fake orders, COD requires explicit customer button interaction (`[Confirm COD Order]`). The backend sets order status to `Confirmed (COD)` and alerts the kitchen staff group [PRD 4.6.3, 4.7.2].
5. **IBFT Manual Receipt Verification (OCR Pipeline)**:
   * **Flow**: If a customer uploads a screenshot of a manual bank transfer, an asynchronous OCR service parses the reference number, amount, and timestamp [PRD 4.2].
   * **Safety Boundary**: The AI LLM **never** confirms payment based on screenshot analysis alone. The order status remains `Pending Payment` until verified against the bank statement or staff approval [PRD 1.2, 4.6, 4.9].

---

## 3. Comprehensive Payment Provider Comparison Matrix

| Provider / Channel | Target Role | Registration Requirements | Processing Fees (MDR) | Settlement Timeline | Webhook & API Support | Zero-Trust State Machine Alignment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Raast P2M (SBP)** [PRD 2.1] | B2C Orders & B2B SaaS | Sole Proprietorship / Corporate IBAN | **0% (Zero Fee)** [PRD 2.1] | **Real-Time (Instant)** | SBP API & Bank Callback Webhooks | **Full Compliance** (Direct bank confirmation) [PRD 1.2] |
| **SadaPay / SadaBiz** | B2C Orders & B2B SaaS | Sole Proprietorship / NTN | 1.5% – 3.0% (Card) / 0% (Raast) | T+1 Business Day | REST API Links + Event Webhooks | **Full Compliance** (Cryptographic webhook) [PRD 1.2] |
| **NayaPay Arc** [PRD 4.6] | B2C Orders & B2B SaaS | Sole Proprietorship / NTN | 2.2% – 2.9% + Tax | T+1 Business Day | HMAC SHA-256 Webhooks [PRD 4.6] | **Full Compliance** (HMAC signature check) [PRD 1.2] |
| **Safepay** [PRD 4.6] | B2C Orders & B2B SaaS | Registered Business / NTN | 2.5% – 3.3% + Tax | T+2 Business Days | Tokenized REST API + Webhooks | **Full Compliance** (HMAC signature check) [PRD 1.2] |
| **PayPro** [Doc 06] | B2B Subscription Invoicing | Registered Business / NTN | PKR 25–50 fixed or 1.8% | T+1 Business Day | 1LINK Bill Push API + Callback Webhooks | **Full Compliance** (1LINK clearing notification) [PRD 1.2] |
| **Cash on Delivery (COD)** [PRD 4.6] | B2C Food Orders | None | **0% Gateway Fee** | Upon Delivery | Interactive WhatsApp Button Confirmation | **Full Compliance** (Explicit `Confirmed COD` state) [PRD 4.6] |

---

## 4. Technical & Regional Localization Requirements

### 4.1 Currency Handling & Minor Units
* **ISO 4217 Currency Code**: Default base currency is `PKR` for Phase 1 [PRD 2.2].
* **Minor Unit Storage**: Stored in PostgreSQL as integer minor units (`paisa`, 1 PKR = 100 paisa) or `NUMERIC(12,2)` to eliminate floating-point calculation errors [PRD 2.2; Doc 08].
* **Display Formatting**: Formatted cleanly in WhatsApp chat as `Rs. 1,400` or `PKR 1,400` [PRD 2.2].

### 4.2 Timezone Management (`Asia/Karachi`)
* **Timezone Standard**: All tenant operating hours, stock hold expirations, and sales reporting execute under `Asia/Karachi` (UTC+5) [PRD 2.2; Doc 08].
* **Late-Night Shifts**: Supports restaurant operating hours spanning midnight (e.g., 6:00 PM to 3:00 AM) [PRD 2.2].
* **Daily Sales Summaries**: Generates automated owner revenue reports at 03:00 AM PKT following late-night kitchen closing [PRD 4.8].

### 4.3 Trilingual Natural Language Understanding (NLU)
The AI orchestrator processes customer messages across three primary language modes [PRD 2.2, 4.9]:

```
+-----------------------------------------------------------------------------------+
|                        TRILINGUAL PAKISTANI NLU PIPELINE                          |
+--------------------------+--------------------------+-----------------------------+
| 1. ENGLISH               | 2. URDU (SCRIPT)         | 3. ROMAN URDU               |
| "1 Medium Pepperoni      | "ایک میڈیم پیپرونی پزا   | "1 Medium Pepperoni Pizza   |
| Pizza with extra cheese" |  چاہیے اضافی چیز کے ساتھ"|  chahiye extra cheese ke   |
|                          |                          |  sath"                      |
+--------------------------+--------------------------+-----------------------------+
                           | NORMALIZATION ENGINE     |
                           | Fast LLM Entity Parser   |
                           | -> JSON Tool Call        |
                           +--------------------------+
```

* **Roman Urdu Normalization**: System prompts convert colloquial Roman Urdu variations (*"chahiye"*, *"kitne ka hai"*, *"menu dikhao"*, *"bhejj do"*) into standardized JSON parameters for tool execution [PRD 2.2, 4.9].

---

## 5. Implementation Strategy & Recommendations

1. **MVP Payment Setup**:
   * B2C Food Orders: Deploy **Raast P2M QR / Deep Links** + **SadaBiz / NayaPay Arc** links + **COD interactive button confirmation** [PRD 2.1, 4.6].
   * B2B SaaS Subscriptions: Issue automated monthly WhatsApp invoices with Raast P2M / SadaBiz checkout links [PRD 2.1, 4.8].
2. **Zero-Trust Security**:
   * Enforce strict HMAC SHA-256 signature verification on all incoming gateway webhooks (`X-Hub-Signature-256`) before updating order status to `Paid` [PRD 1.2, 4.6].
3. **Multi-Gateway Fallback**:
   * Configure backend payment link generation to automatically fail over to SadaBiz or NayaPay Arc if a primary gateway API call times out [PRD 4.6, 5.1].

---

## 6. Primary Reference Sources

1. `PRODUCT_REQUIREMENTS.md` [PRD] — Version 1.0 Final Approved [PRD 1.1–5.1].
2. `01-executive-summary-and-feasibility.md` — Product overview and commercial drivers.
3. `03-pakistan-market-research.md` — Pakistan market landscape and delivery aggregator economics.
4. `06-technology-stack-and-cost-analysis.md` — SaaS unit economics and hosting costs.
5. `11-security-privacy-and-compliance.md` — PECA 2016, SBP rules, and HMAC webhook security.
