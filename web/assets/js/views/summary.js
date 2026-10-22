// Today summary (P9-T5): one Pakistan day's orders and takings. Owner and manager.

import { get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { pakistanDate, rupees } from "../format.js";

export default {
  id: "summary",
  title: "Today",
  permissions: ["reports.view"],
  mount(root) {
    const day = h("input", { type: "date", value: pakistanDate(), onchange: () => load() });
    const body = h("div", { id: "summary" });
    replace(root, h("section", { class: "card" }, h("h1", {}, "Summary"), field("Day", day), body));

    function tile(label, value) {
      return h("div", { class: "tile" }, h("div", { class: "tile-value" }, value), h("div", { class: "tile-label" }, label));
    }

    async function load() {
      const s = await get(`/api/summary/today?day=${encodeURIComponent(day.value)}`);
      replace(body,
        h("div", { class: "tiles" },
          tile("Orders placed", String(s.placed)),
          tile("Still open", String(s.open)),
          tile("Completed", String(s.completed)),
          tile("Cancelled or rejected", String(s.cancelled)),
          tile("Expired unpaid", String(s.expired)),
          tile("Chats waiting", String(s.handoffs_waiting))),
        h("div", { class: "tiles" },
          tile("Completed sales", rupees(s.completed_paisa)),
          tile("Cash on delivery", rupees(s.completed_cod_paisa)),
          tile("Transfers", rupees(s.completed_transfer_paisa)),
          tile("Transfers verified (net)", rupees(s.transfers_verified_paisa))),
        h("p", { class: "muted" }, "Counts are for orders placed that day. Sales count completed orders only."));
    }

    load().catch((error) => replace(body, h("p", { class: "form-error" }, error.message)));
    return { refresh: () => (day.value === pakistanDate() ? load() : Promise.resolve()) };
  },
};
