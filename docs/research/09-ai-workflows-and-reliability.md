# AI Workflows & Reliability Specification: WhatsApp-First Hospitality SaaS

**Document Title:** `09-ai-workflows-and-reliability.md`  
**Version:** 1.0  
**Date:** October 16, 2026  
**Primary Reference Sources:** `PRODUCT_REQUIREMENTS.md` [PRD], `OpenWA` Documentation [OpenWA], `02-prd-requirements-analysis.md`, `06-technology-stack-and-cost-analysis.md`, `07-system-architecture.md`, `08-database-and-multi-tenancy.md`  
**Status:** Approved Engineering Specification & AI Safety Operating Baseline  

---

## Executive Summary & Guiding AI Safety Principles

This document specifies the artificial intelligence workflows, Natural Language Understanding (NLU) strategies, tool-calling protocols, reliability rules, and failure-handling mechanisms for the **WhatsApp-First AI Hospitality SaaS** [PRD 1.1]. The platform automates food ordering, room service, guest inquiries, payment collection, and inventory management for restaurants and boutique hotels in Pakistan and international expansion markets [PRD 1.1, 2.1].

Because conversational commerce handles real financial transactions, item availability, and guest security, the AI architecture is governed by four strict operational principles [PRD 1.2]:

1. **Zero-Trust Financial State Machine**: No order is marked `Paid` or dispatched to the kitchen based on AI inference, customer text claims, typed transaction references, or uploaded receipt screenshots [PRD 1.2, 4.6]. Order status transitions occur exclusively through cryptographically verified backend payment gateway webhooks [PRD 1.2, 4.6].
2. **Deterministic Pricing & Stock Integrity**: All financial subtotals, taxes, delivery charges, and inventory availability counts are computed and locked by PostgreSQL SQL logic [PRD 1.2, 4.5]. The AI LLM handles natural language intent recognition and entity parsing only, holding **zero write permissions** to database financial or stock tables [PRD 1.2, 4.9].
3. **Strict Confirmation Protocol**: The AI is architecturally prohibited from asserting that an action (e.g., order placement, payment receipt, room booking) succeeded until the backend tool execution returns a verified success confirmation payload [PRD 4.6, 4.9].
4. **Predictable Architecture (Tool-Calling over Autonomous Agents)**: For the Phase 1 MVP, the system uses **Deterministic Intent-Routed Tool Calling** (Groq Llama 3.3 / Gemini 1.5 Flash) rather than autonomous multi-step agent loops [PRD 4.9; Doc 06, 07]. This guarantees sub-1.5-second execution latency, minimal token expense, and complete protection against agent execution loops [PRD 5.1; Doc 06].

---

## 1. Natural Language Understanding & Context Management

### 1.1 Multilingual Intent Recognition & Roman Urdu Processing
Pakistani consumers interact using English, Roman Urdu (*"1 Medium Pepperoni Pizza chahiye with extra cheese"*), or mixed vernacular [PRD 2.2]. The AI NLU engine must parse intent, extract structured entities, and map colloquial menu references to exact SKU identifiers [PRD 2.2, 4.1].

```
+-----------------------------------------------------------------------------------+
|                           MULTILINGUAL NLU PIPELINE                               |
+--------------------------+--------------------------+-----------------------------+
| 1. INBOUND MESSAGE       | 2. NLU INTENT PARSER     | 3. ENTITY & SKU MAPPER      |
| - Text / Voice / Buttons | - Groq Llama 3.3 (Temp=0)| - Extract Items & Modifiers |
| - Language Detection     | - System Guardrails      | - Match against Tenant DB   |
+--------------------------+--------------------------+-----------------------------+
                           | 4. TOOL DISPATCH         |
                           | - Execute SQL Function   |
                           | - Return JSON Payload    |
                           +--------------------------+
```

#### Core Intent Classifications:
* **`BROWSE_CATALOG`**: Customer wants to view menu, categories, or specific dishes.
* **`CHECK_ITEM_AVAILABILITY`**: Customer asks if a specific dish or size is available.
* **`ADD_TO_CART`**: Customer specifies items, sizes, quantities, and modifiers.
* **`VIEW_CART`**: Customer requests cart summary, subtotals, or delivery charges.
* **`INITIATE_CHECKOUT`**: Customer requests payment link or specifies fulfillment mode (Delivery/Takeaway/Dine-in).
* **`VERIFY_ROOM_GUEST`**: Hotel guest provides Room Number and Last Name/PIN for room service [PRD 4.4].
* **`HOTEL_GUEST_REQUEST`**: Guest asks for towels, room cleaning, late check-out, or amenities [PRD 4.4].
* **`OWNER_ADMIN_COMMAND`**: Verified owner updates prices, stock toggles, or pauses kitchen intake [PRD 4.8].
* **`HUMAN_ESCALATION`**: Customer requests live agent support or expresses severe dissatisfaction [PRD 4.10].

### 1.2 Conversation Context Window & State Persistence
To maintain contextual awareness across multi-turn ordering dialogues while minimizing LLM prompt token costs, conversation state is persisted across PostgreSQL and Redis [Doc 06, 08]:

1. **Short-Term Context Window (Redis)**: Holds the last **10 message turns** for the active conversation key (`tenant_id:customer_phone`), cached with a 1-hour sliding TTL.
2. **Session Cart State (PostgreSQL `orders` table)**: Active unconfirmed carts are stored in state `Draft` with `expires_at` timestamps set 15 minutes into the future [Doc 08].
3. **Prompt Injection Payload**: Each LLM completion request receives a structured prompt containing:
   * Tenant Metadata (Name, Business Type, Currency, Minimum Order, Operating Status).
   * Active Cart JSON Summary (Items, Quantities, Subtotal, Applied Stock Holds).
   * Last 6 Conversation Turns (User utterances and Assistant responses).
   * System Guardrails & Tool Schema Declarations.

---

## 2. Tool-Calling Architecture & Backend Integration

### 2.1 LLM Tool Definitions (JSON Schema)
The LLM layer interacts with the backend strictly by outputting structured JSON function calls. The backend executes the logic against PostgreSQL/Redis and passes the JSON result back to the LLM for final response synthesis [Doc 07, 08].

```
+-----------------------------------------------------------------------------------+
|                         DETERMINISTIC TOOL EXECUTION FLOW                         |
+--------------------------+--------------------------+-----------------------------+
| Customer Utterance       | LLM Tool Calling JSON    | Backend FastAPI Execution   |
| "Add 2 Medium Zinger     | {"tool": "add_to_cart",  | - Enforces RLS              |
| Burgers with Extra Cheese"|  "sku": "ZNG-M", "qty":2}| - Calls `reserve_stock_item`|
+--------------------------+--------------------------+-----------------------------+
                                                      |
                                                      v
| Customer Output Response | LLM Context Injection    | PostgreSQL DB Output        |
| "Added 2 Medium Zingers. | <- {"status": "SUCCESS", | {"status": "SUCCESS",       |
| Total: Rs. 1,100 [1]"    |     "subtotal": 1100}    |  "reserved_until": "10:15"} |
+--------------------------+--------------------------+-----------------------------+
```

#### Core Tool Specs:

1. **`get_active_catalog(tenant_id, category_id=None)`**
   * *Purpose*: Fetches active menu items, sizes, modifiers, and prices directly from database SQL.
2. **`check_stock_availability(tenant_id, item_id)`**
   * *Purpose*: Executes SQL query on `stock_inventory` returning `available_quantity - reserved_quantity` [Doc 08].
3. **`add_to_cart(tenant_id, customer_id, item_id, quantity, modifiers=[])`**
   * *Purpose*: Calls PL/pgSQL function `reserve_stock_item()` placing a **5-minute temporary hold** on requested quantities [PRD 4.5; Doc 08].
4. **`verify_hotel_guest(tenant_id, room_number, guest_pin)`**
   * *Purpose*: Matches room number and PIN against tenant guest registration records [PRD 4.4].
5. **`generate_checkout_link(tenant_id, order_id, payment_method)`**
   * *Purpose*: Creates single-use payment link/QR code for NayaPay Arc, Safepay, or Raast P2M [PRD 4.6].
6. **`execute_owner_update(tenant_id, owner_phone, command_type, payload)`**
   * *Purpose*: Validates owner phone number against `staff_members` table and updates pricing/stock availability [PRD 4.8].

### 2.2 Zero-Write Permission Enforcer
To guarantee database integrity, the database user assigned to the LLM service role possesses `SELECT` permissions on catalog tables and `EXECUTE` permissions **only** on locked, validated PL/pgSQL functions [Doc 08]. The LLM cannot execute direct `INSERT`, `UPDATE`, or `DELETE` statements on orders, prices, or inventory tables [PRD 1.2, 4.9].

---

## 3. Anti-Hallucination Guardrails & Data Validation

### 3.1 Strict Prompt Engineering Rules
The system prompt injected into Groq Llama 3.3 / Gemini 1.5 Flash contains explicit negative constraints and behavioral boundaries [PRD 4.9]:

```markdown
SYSTEM GUARDRAILS (UNYIELDING OPERATIONAL RULES):
1. You are an AI ordering assistant. You DO NOT know prices, discounts, or stock availability from your training data.
2. NEVER state a price, subtotal, tax, delivery fee, or stock status unless it was returned explicitly by a backend tool payload in this conversation.
3. NEVER promise or confirm discounts, free items, or custom price reductions.
4. NEVER claim an order is placed, confirmed, or paid until the tool `generate_checkout_link` or `check_order_status` returns "status": "CONFIRMED" or "PAID".
5. If a tool output indicates an item is out of stock ("available_quantity": 0), recommend same-category alternatives provided in the tool response.
6. Speak concisely, clearly, and politely. Format menu items using bullet points or Meta Interactive Lists.
```

### 3.2 Temperature & Sampling Strategy
* **Tool Selection & Entity Extraction**: `temperature = 0.0`, `top_p = 0.1` (Strictly deterministic function outputs).
* **Conversational Formatting**: `temperature = 0.2`, `top_p = 0.3` (Minimal creative variation, maintaining strict grounding).

### 3.3 Pre-Submission Order Validation Pipeline
Before generating a payment link or confirming an order, the backend executes a 5-point validation check:

```
+-----------------------------------------------------------------------------------+
|                        5-POINT ORDER VALIDATION PIPELINE                          |
+-----------------------------------------------------------------------------------+
| 1. Operating Hours Check   : Verify tenant `is_active` and within operating hours |
| 2. Minimum Order Check     : Subtotal >= tenant `min_order_amount`                |
| 3. Atomic Stock Lock Check : `reserve_stock_item()` returned SUCCESS for all SKUs  |
| 4. Delivery / Room Check   : Valid location pin OR verified Hotel Room + PIN      |
| 5. Price Audit Check       : Recalculate item_price * qty + tax + delivery == total|
+-----------------------------------------------------------------------------------+
```

---

## 4. Backend Confirmation Protocol & Two-Phase State Transitions

To eliminate false confirmations where customers are told their food is cooking before payment arrives, the architecture enforces a strict **Two-Phase Confirmation Protocol** [PRD 1.2, 4.6, 4.9]:

```
+-----------------------------------------------------------------------------------+
|                       TWO-PHASE CONFIRMATION PROTOCOL                             |
+-----------------------------------------------------------------------------------+
| PHASE 1: PREPARATION & HOLD (AI Controlled)                                       |
| - Customer builds cart via WhatsApp text/buttons.                                 |
| - Backend places 5-minute stock hold via `reserve_stock_item()`.                  |
| - AI sends single-use Payment Link / Raast QR code.                              |
| - Order status remains strictly `Draft` or `Pending_Payment`.                     |
|                                                                                   |
| PHASE 2: CONFIRMATION & DISPATCH (Deterministic Webhook Controlled)               |
| - Customer completes payment on gateway (NayaPay / Safepay / Raast).              |
| - Payment Gateway sends HMAC-signed HTTP POST webhook to FastAPI listener.        |
| - Backend verifies HMAC SHA-256 signature and updates order to `Paid`.             |
| - Backend triggers automated WhatsApp receipt to customer AND kitchen alert group. |
| - AI LLM IS NOTIFIED AFTER THE FACT to send tracking updates.                    |
+-----------------------------------------------------------------------------------+
```

---

## 5. Edge Cases, Failures, Retries & Interrupted Conversations

| Failure Scenario | Root Cause | Automated Architectural Resolution |
| :--- | :--- | :--- |
| **Duplicate Webhook Delivery** | Meta retries webhooks if response > 3.0s [PRD 5.1; Doc 07]. | Redis deduplication cache inspects `entry.changes.value.messages.id` with 24h TTL, dropping duplicates instantly [PRD 4.3]. |
| **Payment Timeout (> 5 mins)** | Customer drops off or abandons payment link [PRD 4.5]. | Upstash QStash cron releases reserved stock after 5 mins, returning items to live inventory and expiring payment link [PRD 4.5]. |
| **LLM API Rate Limit / Outage** | Groq / Gemini API latency spike or HTTP 503 error [Doc 06]. | FastAPI catches timeout (>2.5s), falling back to static Meta Interactive List buttons generated directly from PostgreSQL. |
| **Interrupted Onboarding** | Business owner drops off midway through setup [PRD 4.1]. | Onboarding state is saved transactionally per message in `tenants.onboarding_step`, allowing resumption at any time [PRD 4.1]. |
| **Ambiguous Voice Note** | Customer sends noisy audio or unclear Roman Urdu phrase. | System prompts user with Interactive Reply Buttons (`[View Menu]`, `[Speak to Agent]`) to clarify intent without guessing [PRD 4.10]. |

---

## 6. Human Escalation & Staff Handoff Workflows

Automated AI handling immediately pauses and hands over control to human staff under specific operational conditions [PRD 4.10]:

### 6.1 Escalation Triggers
1. **Explicit Customer Command**: Keywords like `"agent"`, `"human"`, `"talk to staff"`, `"call me"`, `"manager"` [PRD 4.10].
2. **Negative Sentiment Threshold**: NLP sentiment score drops below **0.65** (e.g., anger, billing dispute, delayed order complaint) [PRD 4.10].
3. **Repeated NLU Failures**: 2 consecutive turns where intent classification confidence is < 60% or tool execution fails.
4. **Unsafe / Complex Request**: Custom hotel requests (e.g., medical emergency, room change) or unlisted dietary constraints.

```
+-----------------------------------------------------------------------------------+
|                        HUMAN AGENT ESCALATION PROTOCOL                            |
+-----------------------------------------------------------------------------------+
| 1. Trigger Detected        : Sentiment < 0.65 OR explicit "talk to human" command |
| 2. State Lock              : Set `conversations.is_human_handled = TRUE` in DB    |
| 3. AI Pause                : Suppress all automated LLM completions for thread   |
| 4. Kitchen / Staff Alert   : Dispatch urgent alert to staff WhatsApp group with    |
|                              direct customer chat link                             |
| 5. Staff Takeover          : Staff replies directly in WhatsApp group or portal   |
| 6. Session Resume          : Staff taps `[Resume AI]` button to return to bot      |
+-----------------------------------------------------------------------------------+
```

---

## 7. MVP Architecture Recommendation: Intent-Routed Tool Calling

When choosing between an **Autonomous AI Agent** (e.g., ReAct / LangGraph), a **Rigid Static Workflow Engine**, and an **Intent-Routed Tool-Calling Framework**, the engineering recommendation for the Phase 1 MVP is unambiguously **Intent-Routed Tool Calling (FastAPI + Groq/Gemini Function Calling)** [Doc 06, 07].

### 7.1 Architecture Comparison Matrix

| Evaluation Metric | Autonomous AI Agent (LangGraph/ReAct) | Rigid Static Flow (ManyChat style) | **Intent-Routed Tool Calling (Recommended MVP)** |
| :--- | :--- | :--- | :--- |
| **Execution Latency** | High (3.0s – 8.0s across multi-step loops) | Ultra-Low (< 200ms) | **Low (0.8s – 1.5s single LLM call)** [Doc 06]. |
| **Cost per Conversation** | High (\$0.01 – \$0.05 per conversation) | Very Low (< \$0.001) | **Ultra-Low (\$0.0001 – \$0.0005 per conversation)** [Doc 06]. |
| **Hallucination Risk** | High (Can execute unverified tool loops) | Zero (Static rules only) | **Zero (LLM has zero write access; locked SQL)** [PRD 4.9]. |
| **Natural Language Flexibility**| High (Handles complex freeform chat) | Zero (Breaks on unexpected input) | **High (Handles Roman Urdu, typos, freeform chat)** [PRD 2.2]. |
| **Engineering Complexity** | High (Complex state graph debugging) | Low (Drag-and-drop node logic) | **Moderate (FastAPI routes + Function calling)** [Doc 07]. |

---

## 8. Requirements Traceability Matrix

| Document Requirement | Technical / Architectural Mechanism | Verification Method |
| :--- | :--- | :--- |
| **PRD FR5.1-5.4**: 5-Min Temp Stock Hold | PL/pgSQL `reserve_stock_item()` + Upstash QStash cron release | PostgreSQL concurrent transaction unit tests [Doc 08]. |
| **PRD FR6.2**: Zero-Trust Payment Confirmation | HMAC SHA-256 Webhook validation on NayaPay/Safepay/Raast | Automated HTTP POST mock webhook suite [Doc 07]. |
| **PRD FR8.1**: Conversational Owner Admin | Phone authentication against `staff_members` + `execute_owner_update()` | Owner WhatsApp command integration tests [PRD 4.8]. |
| **PRD FR9.2**: Zero LLM DB Write Access | RLS policies + Restricted Database User Role permissions | PostgreSQL permission isolation audit [Doc 08]. |
| **PRD FR10.1**: Human Escalation Handoff | `conversations.is_human_handled = TRUE` + Staff WhatsApp Alert | Integration test simulating negative sentiment score [PRD 4.10]. |

---

## 9. References & Source Attribution

1. **`PRODUCT_REQUIREMENTS.md`**: Core operational principles, FR1–FR10 functional requirements, NFR matrix, unsafe workflows, and PRD scope [PRD 1.1, 1.2, 2.1, 2.2, 4.1-4.10, 5.1].
2. **`OpenWA` Technical Documentation**: Gateway capabilities, multi-session management, and Webhooks API [OpenWA 1, 2, 4, 7].
3. **`02-prd-requirements-analysis.md`**: MVP scope bounds, operational roles, and non-functional specifications.
4. **`06-technology-stack-and-cost-analysis.md`**: Groq Llama 3.3 / Gemini 1.5 Flash performance benchmarks, API costs, and unit economics.
5. **`07-system-architecture.md`**: FastAPI webhook pipeline, Redis idempotency deduplication, and event flow architecture.
6. **`08-database-and-multi-tenancy.md`**: PostgreSQL schema, Row Level Security policies, and PL/pgSQL stock reservation functions.
