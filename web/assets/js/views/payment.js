// The payment panel for one order (P9-T3, AD-03, AD-32).
//
// "Payment received" needs the transaction reference from the restaurant's own bank or
// wallet app and the confirmer's personal PIN. On a shared kitchen tablet the confirmer
// picks their name first. A screenshot or "I have paid" from the customer is shown as
// evidence only; it never marks an order paid.

import { get } from "../api.js";
import { dialog, field, h, replace } from "../dom.js";
import { STATUS_LABELS, dateTime, rupees } from "../format.js";
import { actionForm } from "../ui.js";

const EVIDENCE = { image: "Screenshot", document: "Document", text_claim: "Said they paid" };

function pinInput() {
  return h("input", {
    name: "pin", type: "password", inputmode: "numeric", autocomplete: "off",
    maxlength: 16, required: true, class: "pin",
  });
}

async function confirmerPicker(app) {
  if (app.me.role !== "kitchen_device") return null;
  const people = await get("/api/payments/confirmers");
  return h("select", { name: "confirmer_id", required: true },
    h("option", { value: "" }, "Who is confirming?"),
    people.map((p) => h("option", { value: p.id }, p.display_name)));
}

function confirmFields(reference, pin, confirmer) {
  return [
    confirmer ? field("Confirmed by", confirmer, "Your own PIN, not the tablet's.") : null,
    field("Transaction reference", reference, "Copy it from the restaurant's bank or wallet app — not from the customer's screenshot."),
    field("Your payment PIN", pin),
  ];
}

function confirmCheck(reference, pin, confirmer) {
  return () => {
    if (confirmer && !confirmer.value) return "Choose who is confirming.";
    if (!reference.value.trim()) return "Enter the transaction reference.";
    if (!pin.value) return "Enter your payment PIN.";
    return null;
  };
}

function confirmBody(reference, pin, confirmer, extra = {}) {
  return () => ({
    reference: reference.value.trim(),
    pin: pin.value,
    ...(confirmer ? { confirmer_id: confirmer.value } : {}),
    ...extra(),
  });
}

export async function openPayment(orderId, app, onChange) {
  const body = h("div", { class: "payment-panel" }, h("p", {}, "Loading…"));
  const box = dialog("Payment", body);

  async function render() {
    const view = await get(`/api/orders/${orderId}/payment`);
    const confirmer = await confirmerPicker(app);
    const parts = [
      h("dl", { class: "facts" },
        h("dt", {}, "Order"), h("dd", {}, view.reference),
        h("dt", {}, "Status"), h("dd", { id: "payment-status" }, STATUS_LABELS[view.status] || view.status),
        h("dt", {}, "Amount"), h("dd", {}, rupees(view.total_paisa)),
        view.payment_due_at ? [h("dt", {}, "Due by"), h("dd", {}, dateTime(view.payment_due_at))] : null),
    ];

    if (view.actions.includes("verify")) {
      const reference = h("input", { name: "reference", maxlength: 64, autocomplete: "off" });
      const pin = pinInput();
      parts.push(h("h3", {}, "Payment received"), actionForm(confirmFields(reference, pin, confirmer), {
        method: "POST",
        path: `/api/orders/${orderId}/payment/verify`,
        body: confirmBody(reference, pin, confirmer, () => ({})),
        check: confirmCheck(reference, pin, confirmer),
        done: async () => { await changed("Payment confirmed."); },
        submitLabel: "Confirm payment received",
      }));
    }

    if (view.actions.includes("late_review")) {
      const reference = h("input", { name: "reference", maxlength: 64, autocomplete: "off" });
      const pin = pinInput();
      const decision = h("select", { name: "decision" },
        h("option", { value: "accept" }, "Accept the order (we can still make it)"),
        h("option", { value: "refund" }, "Refund the customer"));
      const reason = h("input", { name: "reason", maxlength: 200, autocomplete: "off" });
      parts.push(h("h3", {}, "Late payment"),
        h("p", {}, "The transfer window passed. If the money did arrive, accept the order or refund it."),
        actionForm([field("Decision", decision), field("Reason (needed to refund; the customer sees it)", reason),
          ...confirmFields(reference, pin, confirmer)], {
          method: "POST",
          path: `/api/orders/${orderId}/payment/late-review`,
          body: confirmBody(reference, pin, confirmer, () => ({
            decision: decision.value, reason: reason.value.trim() || undefined,
          })),
          check: () => (decision.value === "refund" && !reason.value.trim()
            ? "Say why you are refunding." : confirmCheck(reference, pin, confirmer)()),
          done: async () => { await changed("Late payment recorded."); },
          submitLabel: "Record late payment",
        }));
    }

    parts.push(h("h3", {}, "From the customer"), view.evidence.length
      ? h("ul", { class: "evidence" }, view.evidence.map((e) =>
        h("li", {}, `${EVIDENCE[e.kind] || e.kind} — ${dateTime(e.received_at)}`)))
      : h("p", { class: "muted" }, "Nothing yet."),
    view.evidence.some((e) => e.kind !== "text_claim")
      ? h("p", { class: "muted" }, "Viewing images comes with the WhatsApp connection. A screenshot alone never confirms a payment.")
      : null);

    if (view.ledger.length) {
      const reversed = new Set(view.ledger.filter((e) => e.corrects_payment_id).map((e) => e.corrects_payment_id));
      parts.push(h("h3", {}, "Payment record"), h("ul", { class: "ledger" }, view.ledger.map((e) =>
        h("li", {},
          e.status === "verified"
            ? `Verified ${rupees(e.amount_paisa)} · ref ${e.reference} · by ${e.verified_by_name || "?"} · ${dateTime(e.verified_at)}${e.late ? " · late" : ""}`
            : `Reversed · ${e.reason || ""} · by ${e.verified_by_name || "?"} · ${dateTime(e.verified_at)}`,
          app.can("payments.reverse") && e.status === "verified" && !reversed.has(e.id)
            ? reverseForm(e) : null))));
    }

    if (app.can("customers.block_cod")) parts.push(codBlock(view));
    replace(body, parts);
  }

  function reverseForm(entry) {
    const pin = pinInput();
    const reason = h("input", { name: "reason", maxlength: 200, autocomplete: "off" });
    return h("details", { class: "reverse" }, h("summary", {}, "Reverse this payment"),
      actionForm([field("Why (the money wasn't there, wrong order…)", reason), field("Your PIN", pin)], {
        method: "POST",
        path: `/api/payments/${entry.id}/reverse`,
        body: () => ({ pin: pin.value, reason: reason.value.trim() }),
        check: () => (!reason.value.trim() ? "Say why." : !pin.value ? "Enter your PIN." : null),
        done: async () => { await changed("Payment reversed."); },
        submitLabel: "Reverse payment",
      }));
  }

  function codBlock(view) {
    const path = `/api/customers/${view.customer_id}/cod-block`;
    if (view.cod_blocked) {
      return h("div", { class: "cod" }, h("p", {}, "This customer can't order cash on delivery."),
        actionForm([], { method: "DELETE", path, body: () => undefined, done: async () => { await changed("Cash on delivery allowed again."); }, submitLabel: "Allow cash on delivery" }));
    }
    const reason = h("input", { name: "reason", maxlength: 200, autocomplete: "off" });
    return h("details", { class: "cod" }, h("summary", {}, "Block cash on delivery for this customer"),
      actionForm([field("Reason", reason)], {
        method: "PUT", path, body: () => ({ reason: reason.value.trim() }),
        check: () => (reason.value.trim() ? null : "Give a reason."),
        done: async () => { await changed("Cash on delivery blocked for this customer."); },
        submitLabel: "Block cash on delivery",
      }));
  }

  async function changed(text) {
    replace(body, h("p", { class: "ok" }, text));
    if (onChange) await onChange();
    setTimeout(() => box.close(), 1200);
  }

  render().catch((error) => replace(body, h("p", { class: "form-error" }, error.message)));
}
