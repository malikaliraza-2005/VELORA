// Menu availability (P9-T5): mark items sold out or back on the live menu. Uses the P4
// endpoints and their permission (menu.edit). Editing the menu itself stays with the CSV
// import and publishing (P4).

import { Action, get } from "../api.js";
import { h, replace } from "../dom.js";
import { rupees } from "../format.js";
import { actionButton } from "../ui.js";

export default {
  id: "menu",
  title: "Menu",
  permissions: ["menu.edit"],
  mount(root) {
    const body = h("div", {}, h("p", {}, "Loading…"));
    replace(root, h("section", { class: "card" }, h("h1", {}, "Menu availability"),
      h("p", { class: "muted" }, "Sold-out items are hidden from customers straight away."), body));

    async function load() {
      let live;
      try {
        live = await get("/api/menu/live");
      } catch (error) {
        if (error.status === 404) {
          replace(body, h("p", { class: "empty" }, "No menu is published yet."));
          return;
        }
        throw error;
      }
      replace(body, h("p", { class: "muted" }, `Live menu, version ${live.version_no}.`),
        live.categories.map((c) => h("div", { class: "menu-category" }, h("h2", {}, c.name),
          h("ul", { class: "list" }, c.items.map((item) => h("li", { class: item.available ? "" : "sold-out", "data-sku": item.sku },
            h("span", {}, item.name, " ", h("span", { class: "muted" }, rupees(item.price_paisa))),
            item.available ? null : h("span", { class: "badge badge-warn" }, "Sold out"),
            " ",
            actionButton(item.available ? "Mark sold out" : "Back in stock",
              new Action("PUT", `/api/menu/availability/${encodeURIComponent(item.sku)}`, () => ({ available: !item.available })),
              load, { kind: item.available ? "secondary" : "primary" })))))));
    }

    load().catch((error) => replace(body, h("p", { class: "form-error" }, error.message)));
    return {};
  },
};
