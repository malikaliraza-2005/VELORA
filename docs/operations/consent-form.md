# Restaurant pilot agreement and consent (draft)

> **For the founder (remove this box before printing):**
> - **Status:** engineering draft, 2026-10-24 (AD-35, AD-38). **It is not legal advice.** The owner withdrew the lawyer review and accepts that risk (AD-35).
> - **Version:** `2026-10`. Print it, have the restaurant owner read it with you, and sign two copies: one for them, one for you.
> - **Recording:** record each part with the onboarding tool, with the signed scan as the document, so its SHA-256 is kept (P3):
>
>   ```bash
>   velora admin consent-record --tenant <slug> --kind separate_number_ban_risk --version 2026-10 --document signed.pdf --accepted-by "<owner name>"
>   velora admin consent-record --tenant <slug> --kind data_processing --version 2026-10 --document signed.pdf --accepted-by "<owner name>"
>   velora admin consent-record --tenant <slug> --kind terms --version 2026-10 --document signed.pdf --accepted-by "<owner name>"
>   ```
>
> - **Keep the signed scan** in your private storage, not in VELORA's database or the repository. `go-live` is refused until the consent and the verification ([`owner-verification.md`](owner-verification.md)) are recorded.
> - **Fill in** the bracketed fields. Read Part A aloud: the owner must understand the ban risk before signing.
> - **Translation:** if the owner prefers Urdu, explain each part in Urdu. The signed English text is the one recorded.

---

**Between:** VELORA ("we"), represented by [founder's name], and [restaurant's legal or trading name] ("the restaurant"), represented by [owner's name], CNIC [last 4 digits only], [address].

**What this is:** a free trial ("the pilot") of VELORA, a WhatsApp ordering assistant, at the restaurant, from [start date] for up to [8] weeks. Either side may end it at any time by telling the other.

## Part A. A separate WhatsApp number, and the risk of a ban

1. **How VELORA connects to WhatsApp.** VELORA links to a WhatsApp Business account through an unofficial connection (OpenWA). WhatsApp and Meta have **not** approved it. WhatsApp's terms may not allow it.
2. **The risk.** WhatsApp may **restrict or permanently ban the number at any time, without warning and without appeal.** We can't prevent this or promise it won't happen.
3. **A separate number.** So that a ban never takes the restaurant's main line, the restaurant uses a **separate number used only for VELORA orders** (a new SIM): number [ordering number]. **The restaurant's main WhatsApp number is never linked.** The main number may carry an away message pointing customers to the ordering number.
4. **If the number is restricted or banned:**
   - we pause VELORA for the restaurant straight away;
   - the restaurant takes orders by phone and on paper (the manual fallback);
   - we tell the restaurant what happened;
   - **neither side creates a new account to get around the ban** unless WhatsApp allows it.
5. **No compensation.** The pilot is free. Neither side owes the other anything for a restriction, a ban, or downtime.
6. **Stopping the pilot.** We may pause or stop the pilot for safety, for example after a warning from WhatsApp, repeated disconnections, or wrong orders (our stop rules).

## Part B. Customer data

1. **What VELORA keeps** about the restaurant's customers:
   - their WhatsApp number and name;
   - their messages to the ordering number;
   - their orders, including delivery address and note;
   - payment records, such as transfer references and any proof they send.
2. **Why:** only to take, confirm, prepare and deliver their orders, and to show the restaurant's staff their chats and orders.
3. **Who sees it:**
   - the restaurant's staff, through the dashboard, each according to their role;
   - VELORA's founder, to run and support the service.

   **We don't sell it, share it for advertising, or message customers for marketing.**
4. **Automated assistant.** Customers are told in the first reply that they are talking to an automated assistant. They can type **STOP** to stop all messages.
5. **AI processing.** When a customer writes an order in their own words, the text is sent to an AI service (Groq, in the United States), with no phone number, address or name, to suggest items. The restaurant can switch the AI off at any time; the numbered menu always works.
6. **How long it is kept** (VELORA removes it automatically):
   - message text: 30 days;
   - delivery address on a finished order: 90 days;
   - a customer's number and name: 1 year after their last conversation or order;
   - orders and payment amounts: kept as the restaurant's business record.
7. **Deletion on request.** If a customer asks to be forgotten, the restaurant tells us, and we remove their personal data within 7 days. Their past orders stay, without their name, number or address.
8. **Security.** Data is stored encrypted in backups, on a server only VELORA's founder administers. Staff sign in with personal links and the payment PIN, and every action is logged.
9. **If something goes wrong.** If customer data is exposed, we tell the restaurant within 72 hours, with what we know and what we're doing.
10. **The restaurant's part:**
    - keep staff login links and the payment PIN private;
    - remove staff who leave, on the same day;
    - pass on customers' deletion requests.

## Part C. Pilot terms

1. **Free.** No fee is charged during the pilot. If the restaurant wants to continue afterwards, any price is agreed separately, in writing.
2. **Orders and food.** The restaurant remains responsible for its menu, prices, food, preparation, delivery and customer service. VELORA takes orders only from the menu the restaurant publishes, at those prices.
3. **Money.** VELORA never holds money.
   - **Cash on delivery** is collected by the restaurant.
   - **Bank or wallet transfers** go straight to the restaurant's own account. Its staff verify each one before an order is accepted, and VELORA never confirms a payment by itself.
4. **Tax.** The restaurant tells us its sales tax rate for each payment method, and confirms it is correct. VELORA calculates totals with it.
5. **No guarantee.** VELORA is in trial. It may stop, make mistakes or be unavailable. Staff check every order before preparing it, and the restaurant keeps a way to take orders by phone.
6. **Feedback.** The restaurant agrees to give honest feedback, and to let us see how staff use the dashboard, to improve VELORA.
7. **Ending.** When the pilot ends:
   - we unlink the ordering number;
   - we confirm on the restaurant's phone that the device is removed;
   - we keep the data only as Part B says.

## Signatures

| | VELORA | The restaurant |
|---|---|---|
| Name | | |
| Signature | | |
| Date | | |

I have read Parts A, B and C, and I understand that **the ordering number may be banned by WhatsApp at any time** (Part A).

Restaurant owner's initials: ______
