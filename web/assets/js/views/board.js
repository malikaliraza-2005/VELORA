// Order board (P9-T2): New / Preparing / Ready-Out / Done.
//
// Each move button sends the status the staff member saw (`from`), so the server refuses a
// move from a status that has changed, and an Idempotency-Key that stays the same for as
// long as that button is on screen. A double tap therefore makes one move.

import { Action, get } from "../api.js";
import { h, dialog, field, replace } from "../dom.js";
import { MOVE_LABELS, STATUS_LABELS, ago, pakistanDate, rupees, time } from "../format.js";
import { actionButton, actionForm } from "../ui.js";
import { openPayment } from "./payment.js";

const COLUMNS = [
  { id: "new", title: "New", statuses: ["cod_confirmed", "awaiting_transfer", "paid_staff_verified", "refund_pending"] },
  { id: "preparing", title: "Preparing", statuses: ["accepted", "preparing"] },
  { id: "ready", title: "Ready / Out", statuses: ["ready", "out_for_delivery"] },
  { id: "done", title: "Done today", statuses: ["completed", "cancelled", "rejected", "expired"] },
];
const NEW = new Set(COLUMNS[0].statuses);
// The customer is told why (lifecycle.REASON_REQUIRED).
const NEEDS_REASON = new Set(["rejected", "cancelled", "refund_pending"]);

export default {
  id: "board",
  title: "Orders",
  permissions: ["orders.view"],
  mount(root, app) {
    const columns = Object.fromEntries(COLUMNS.map((c) => [c.id, h("div", { class: "column-body" })]));
    const counts = Object.fromEntries(COLUMNS.map((c) => [c.id, h("span", { class: "count" })]));
    replace(root, h("div", { class: "board" }, COLUMNS.map((c) =>
      h("section", { class: "column", "data-column": c.id },
        h("h2", {}, c.title, " ", counts[c.id]), columns[c.id]))));

    // One Action per (order, status, target), kept across refreshes so its key holds.
    let actions = new Map();
    let seen = null;
    let last = "";

    function action(order, to, reason) {
      const id = `${order.id}:${order.status}:${to}`;
      if (!actions.has(id)) {
        actions.set(id, new Action("POST", `/api/orders/${order.id}/status`,
          () => ({ from: order.status, to, reason: reason ? reason() : undefined })));
      }
      return actions.get(id);
    }

    function moveButton(order, to) {
      const label = MOVE_LABELS[to] || to;
      const kind = NEEDS_REASON.has(to) ? "danger" : "primary";
      if (!NEEDS_REASON.has(to)) {
        return actionButton(label, action(order, to), refresh, { kind });
      }
      return h("button", { type: "button", class: kind, onclick: () => askReason(order, to, label) }, label);
    }

    function askReason(order, to, label) {
      const reason = h("input", { name: "reason", maxlength: 200, required: true, autocomplete: "off" });
      const box = dialog(`${label}: ${order.reference}`, actionForm(
        [field("Why? The customer sees this.", reason)],
        {
          method: "POST",
          path: `/api/orders/${order.id}/status`,
          body: () => ({ from: order.status, to, reason: reason.value.trim() }),
          check: () => (reason.value.trim() ? null : "Say why — the customer is told."),
          done: async () => { box.close(); await refresh(); },
          submitLabel: label,
        }));
    }

    function card(order) {
      const lines = h("ul", { class: "lines" }, order.lines.map((l) =>
        h("li", {}, `${l.quantity} × ${l.name}`,
          l.choices.length ? h("span", { class: "choices" }, ` (${l.choices.map((c) => c[1]).join(", ")})`) : null)));
      const buttons = order.next_statuses.map((to) => moveButton(order, to));
      if (order.payment_method === "transfer") {
        const label = order.status === "awaiting_transfer" ? "Payment received" : "Payment";
        buttons.unshift(h("button", {
          type: "button", class: order.status === "awaiting_transfer" ? "primary" : "secondary",
          onclick: () => openPayment(order.id, app, refresh),
        }, label));
      }
      return h("article", {
        class: `order status-${order.status}${order.possible_duplicate ? " duplicate" : ""}`,
        "data-order": order.id, "data-status": order.status,
      },
        h("header", {},
          h("strong", { class: "ref" }, order.reference),
          h("span", { class: "when", title: order.created_at }, ago(order.created_at))),
        h("div", { class: "status-line" }, STATUS_LABELS[order.status] || order.status),
        order.possible_duplicate ? h("div", { class: "flag" }, "⚠ Possible duplicate — check with the customer before making it") : null,
        h("div", { class: "meta" },
          order.fulfilment === "delivery" ? "Delivery" : "Pickup",
          order.pickup_at ? ` at ${time(order.pickup_at)}` : "",
          " · ", order.payment_method === "transfer" ? "Transfer" : "Cash",
          " · ", h("strong", {}, rupees(order.total_paisa))),
        order.payment_due_at && order.status === "awaiting_transfer"
          ? h("div", { class: "meta" }, `Transfer due by ${time(order.payment_due_at)}`) : null,
        lines,
        order.delivery_address ? h("div", { class: "address" }, order.delivery_address) : null,
        order.delivery_note ? h("div", { class: "note" }, `Note: ${order.delivery_note}`) : null,
        order.customer_phone ? h("div", { class: "phone" }, order.customer_phone) : null,
        buttons.length ? h("div", { class: "actions" }, buttons) : null,
        h("button", { type: "button", class: "link", onclick: () => showHistory(order) }, "History"));
    }

    async function showHistory(order) {
      const full = await get(`/api/orders/${order.id}`);
      dialog(`${order.reference} history`, h("ol", { class: "history" }, full.history.map((m) =>
        h("li", {}, `${time(m.at)} — ${STATUS_LABELS[m.to_status] || m.to_status}`,
          ` (${m.actor_kind})`, m.reason ? `: ${m.reason}` : ""))));
    }

    async function refresh() {
      const [active, recent] = await Promise.all([get("/api/orders"), get("/api/orders?active=false")]);
      const today = pakistanDate();
      const done = recent.filter((o) => COLUMNS[3].statuses.includes(o.status)
        && pakistanDate(new Date(o.updated_at)) === today);
      const all = [...active, ...done];

      const fresh = active.filter((o) => NEW.has(o.status)).map((o) => o.id);
      if (seen !== null) {
        const arrived = active.filter((o) => NEW.has(o.status) && !seen.has(o.id));
        if (arrived.length) app.newOrders(arrived.map((o) => o.reference));
      }
      seen = new Set([...(seen || []), ...fresh]);

      const snapshot = JSON.stringify(all.map((o) => [o.id, o.status, o.updated_at, o.possible_duplicate]));
      if (snapshot === last) return;
      last = snapshot;
      const keep = new Set();
      for (const column of COLUMNS) {
        const orders = all.filter((o) => column.statuses.includes(o.status))
          .sort((a, b) => a.created_at.localeCompare(b.created_at));
        if (column.id === "done") orders.reverse();
        orders.forEach((o) => o.next_statuses.forEach((to) => keep.add(`${o.id}:${o.status}:${to}`)));
        counts[column.id].textContent = orders.length ? String(orders.length) : "";
        replace(columns[column.id], orders.length ? orders.map(card) : h("p", { class: "empty" }, "None"));
      }
      actions = new Map([...actions].filter(([id]) => keep.has(id)));
    }

    refresh().catch(() => {});
    return { refresh };
  },
};
