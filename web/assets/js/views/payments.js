// Payments screen (P9-T3): late payments (expired transfer orders, which the board leaves
// out) and, for the owner, the day's transfer report with reversals (P8-T6).

import { get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { STATUS_LABELS, dateTime, pakistanDate, rupees } from "../format.js";
import { openPayment } from "./payment.js";

export default {
  id: "payments",
  title: "Payments",
  permissions: ["orders.view"],
  mount(root, app) {
    const late = h("div", { id: "late-payments" });
    const report = h("div", { id: "report" });
    const day = h("input", { type: "date", value: pakistanDate(), onchange: () => loadReport() });
    replace(root,
      h("section", { class: "card" }, h("h1", {}, "Late payments"),
        h("p", { class: "muted" }, "Transfer orders whose payment window passed. If the money did arrive, review it here."),
        late),
      app.can("payments.reconcile")
        ? h("section", { class: "card" }, h("h1", {}, "Transfers report"), field("Day", day), report)
        : null);

    async function loadLate() {
      const found = await get("/api/orders/late-payments");
      replace(late, found.length ? h("ul", { class: "list" }, found.map(({ order, evidence_count }) =>
        h("li", {},
          h("strong", {}, order.reference), ` · ${rupees(order.total_paisa)} · expired ${dateTime(order.updated_at)}`,
          evidence_count ? h("span", { class: "badge" }, `${evidence_count} from customer`) : null,
          " ", h("button", { type: "button", class: "secondary", onclick: () => openPayment(order.id, app, loadLate) }, "Review"))))
        : h("p", { class: "empty" }, "None in the last 7 days."));
    }

    async function loadReport() {
      if (!app.can("payments.reconcile")) return;
      const r = await get(`/api/payments/reconciliation?day=${encodeURIComponent(day.value)}`);
      replace(report,
        h("p", {}, `${r.verified_count} verified · ${rupees(r.verified_paisa)} · reversed ${rupees(r.reversed_paisa)} · net `,
          h("strong", {}, rupees(r.net_paisa))),
        r.lines.length ? h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ["Order", "Amount", "Reference", "Confirmed by", "When", "Order now", ""].map((t) => h("th", {}, t)))),
          h("tbody", {}, r.lines.map((l) => h("tr", { class: l.reversed_at ? "reversed" : "" },
            h("td", {}, l.order_reference), h("td", {}, rupees(l.amount_paisa)), h("td", {}, l.reference),
            h("td", {}, l.verified_by_name), h("td", {}, dateTime(l.verified_at) + (l.late ? " (late)" : "")),
            h("td", {}, STATUS_LABELS[l.order_status] || l.order_status),
            h("td", {}, l.reversed_at
              ? `Reversed by ${l.reversed_by_name}: ${l.reversal_reason}`
              : h("button", { type: "button", class: "secondary", onclick: () => openPayment(l.order_id, app, loadReport) }, "Open"))))))
          : h("p", { class: "empty" }, "No transfers verified that day."));
    }

    loadLate().catch(() => {});
    loadReport().catch(() => {});
    return { refresh: loadLate };
  },
};
