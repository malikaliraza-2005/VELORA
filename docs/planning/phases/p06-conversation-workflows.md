# P6: Conversation workflows

| Field | Value |
|---|---|
| **Authorisation** | **NOT AUTHORISED.** Once authorised and once P5 is complete (as built so far), it may be built and tested against the `FakeProvider` before the OpenWA Go ([AD-25](../../architecture/architecture-decisions.md#ad-25-provider-neutral-build-track-while-the-openwa-test-waits)). Tests that need real WhatsApp traffic stay gated by the Go. |
| **Depends on** | P4, P5. AI model choice (P0-E5). V-09 (menu formatting limits). |
| **Blocks** | P7 |
| **Effort (estimate)** | about 2 weeks |
| **Status** | [`../implementation-status.md`](../implementation-status.md) |
| **Master roadmap** | [`../implementation-plan.md`](../implementation-plan.md) |

## Governing decisions
| Decision | Status |
|---|---|
| AD-04: paid AI tier, no training on our data | APPROVED |
| AD-14: voice notes deferred | ADOPTED (delegated) |
| AD-20: order safety (AI proposes only, templated totals) | ADOPTED (delegated) |
| AD-24: STOP opt-out; first-reply disclosure; data minimisation | ADOPTED (delegated) |
| V-09: menu formatting limits | VALIDATION-DEPENDENT |
| PC-10: AI candidate models | PROPOSED, awaiting review |
| AI accuracy target (≥90%) | **Estimate; owner to confirm** |

Sample wording is in [MVP scope Appendix A](../mvp-scope.md#appendix-a-sample-customer-whatsapp-conversations-draft-2026-10-21) (draft, untested).

## Objective and scope
- **Objective:** customers can build a correct cart in WhatsApp safely.
- **Scope:**
  - **Concurrency:** messages are processed one at a time per conversation (advisory lock).
  - **First reply:** discloses the automated assistant, how to reach staff (0) and STOP.
  - **Menus:** rendered according to `capabilities()`. OpenWA gets numbered menus, with message splitting per V-09.
  - **Free text and the AI:**
    - The AI turns free text into a **proposal only**, as structured items and quantities.
    - It is never given tenant identity, actor identity, phone numbers or addresses, and it has **no write tools**.
    - Its model is chosen from the P0 evaluation.
    - A timeout or error falls back to the numbered menu.
  - **Cart validation:** every proposed item is resolved against the published menu and availability, and priced by the server.
  - **Delivery and takeaway:** delivery address or location pin with the fee and minimum order; takeaway pickup time.
  - **Payment method:** chosen next, then a **templated** summary with tax for that method. The AI never writes the totals.
  - **Handoff:** replying 0 / agent / insaan, or two messages the bot can't understand, hands the chat to a human. The dashboard inbox shows it, and **Resume bot** hands it back.
  - **STOP opt-out:** applies only when the customer sends exactly "STOP". "CANCEL" means cancel the order, never opt out.
  - **"status" command.**
  - **Voice notes:** the bot replies with a polite request to type (AD-14).

## Phase security and reliability requirements
- Prompt-injection text can at most produce a *proposal* that a human sees rendered from database values.
- Customer data sent to the AI is minimised.
- The AI is a paid tier that doesn't train on our data (AD-04).

## Tasks

### P6-T1: Conversation engine and serialisation
- **Prerequisites:** P5 (inbox events).
- **Deliverables:** the conversation engine; one message at a time per conversation (advisory lock).
- **Tests:** **two simultaneous messages in one chat are processed in order.**
- **Acceptance:** the concurrency test passes repeatedly.

### P6-T2: Fixed commands and disclosures
- **Prerequisites:** T1.
- **Deliverables:**
  - a first reply that discloses the automated assistant, how to reach staff (0) and STOP;
  - STOP opt-out only on exactly "STOP";
  - "CANCEL" means cancel the order, never opt out;
  - a "status" command;
  - a voice-note reply asking the customer to type (AD-14).
- **Tests:**
  - "CANCEL" never opts the customer out;
  - "STOP" opts out and stops further messages;
  - the first reply contains the disclosure.
- **Acceptance:** all tests pass.

### P6-T3: Menu renderer
- **Prerequisites:** P4; P5 `capabilities()`; **V-09**.
- **Deliverables:** menus rendered according to `capabilities()`. OpenWA gets numbered menus, with message splitting per V-09.
- **Tests:** **an unavailable item is never added**; a long menu is split within the V-09 limits.
- **Acceptance:** the tests pass.

### P6-T4: AI proposal module
- **Prerequisites:** AI model chosen from the P0 evaluation (P0-E5; candidates proposed in PC-10).
- **Deliverables:**
  - free text becomes a **proposal only**, as structured items and quantities;
  - the AI is never given tenant identity, actor identity, phone numbers or addresses;
  - **no write tools**;
  - a timeout or error falls back to the numbered menu;
  - the AI can be disabled per tenant.
- **Security:**
  - customer data sent to the AI is minimised;
  - paid tier with no training on our data (AD-04);
  - prompt-injection text can at most produce a *proposal* that a human sees rendered from database values.
- **Tests:**
  - **a suite of injection prompts never changes prices, tenant or state;**
  - the prompt payload contains no phone numbers, addresses or identity fields (asserted);
  - a timeout falls back to the menu.
- **Acceptance:** all tests pass.

### P6-T5: Cart validation, fulfilment details and templated summary
- **Prerequisites:** T3, T4; P4 settings.
- **Deliverables:**
  - every proposed item is resolved against the published menu and availability, and priced by the server;
  - delivery address or location pin with the fee and minimum order; takeaway pickup time;
  - the payment method is chosen next, then a **templated** summary with tax for that method.
- **Tests:**
  - **prices always equal the database values;**
  - **the AI output is never sent verbatim as a total;**
  - the tax line changes with the payment method.
- **Acceptance:** the deterministic tests pass.

### P6-T6: Human handoff
- **Prerequisites:** T1; dashboard inbox (P9; it can be stubbed until then).
- **Deliverables:** replying 0 / agent / insaan, or two messages the bot can't understand, hands the chat to a human. The dashboard inbox shows it, and **Resume bot** hands it back.
- **Tests:** each trigger hands over; while handed over, the bot sends nothing; Resume restores the bot.
- **Acceptance:** the tests pass.

### P6-T7: Offline AI evaluation harness
- **Prerequisites:** P0-F6 dataset.
- **Deliverables:** an evaluation harness (offline).
- **Tests and acceptance:** **AI accuracy (offline, not a CI gate): at least 90% correct proposals on the P0 utterance set before the pilot.** This target is an **estimate, to be confirmed**.

## Phase deliverables
- **Deliverables:** conversation engine, renderer, AI proposal module, evaluation harness (offline).

## Phase acceptance criteria (exit)
- **Deterministic:** an unavailable item is never added; prices always equal the database values; the AI output is never sent verbatim as a total.
- **Injection:** a suite of injection prompts never changes prices, tenant or state.
- **Concurrency:** two simultaneous messages in one chat are processed in order.
- **AI accuracy (offline, not a CI gate):** at least 90% correct proposals on the P0 utterance set before the pilot. This target is an **estimate, to be confirmed**.

## Risks and rollback
- **Weak AI accuracy:** rely more on numbered menus. The AI can be disabled per tenant.

## Open items
- The AI accuracy target needs owner confirmation.
- PC-10 (candidate models) is awaiting review.
- V-09 is undecided.
