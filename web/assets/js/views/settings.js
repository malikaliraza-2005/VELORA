// Settings (P9-T5): the restaurant's name and its ordering settings (P4 endpoints,
// settings.edit). Kitchen roles can't open this screen, and the API refuses them too.

import { get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { actionForm } from "../ui.js";

// [field, label, kind]. Money is entered in rupees and sent in paisa; tax in percent, sent
// in basis points.
const FIELDS = [
  ["delivery_enabled", "Delivery", "bool"],
  ["takeaway_enabled", "Pickup", "bool"],
  ["delivery_fee_paisa", "Delivery fee (Rs)", "rupees"],
  ["delivery_min_order_paisa", "Minimum delivery order (Rs)", "rupees"],
  ["pickup_min_lead_minutes", "Pickup: earliest, minutes from now", "int"],
  ["pickup_max_ahead_minutes", "Pickup: latest, minutes ahead", "int"],
  ["cod_enabled", "Cash on delivery", "bool"],
  ["cod_first_order_limit_paisa", "First-order cash limit (Rs, empty: none)", "rupees?"],
  ["transfer_enabled", "Bank or wallet transfer", "bool"],
  ["transfer_window_minutes", "Transfer window (minutes)", "int"],
  ["transfer_account_details", "Transfer account details shown to customers", "text?"],
  ["tax_cod_bps", "Tax on cash orders (%, empty: not set)", "percent?"],
  ["tax_transfer_bps", "Tax on transfer orders (%, empty: not set)", "percent?"],
];

function input(kind, value) {
  if (kind === "bool") return h("input", { type: "checkbox", checked: Boolean(value) });
  if (kind === "text?") return h("textarea", { rows: 3, maxlength: 500, value: value ?? "" });
  let shown = value;
  if (kind.startsWith("rupees") && value !== null) shown = value / 100;
  if (kind.startsWith("percent") && value !== null) shown = value / 100;
  return h("input", { type: "number", step: kind.startsWith("int") ? 1 : 0.01, min: 0, value: shown ?? "" });
}

function read(kind, el) {
  if (kind === "bool") return el.checked;
  const raw = el.value.trim();
  if (kind.endsWith("?") && raw === "") return null;
  if (kind === "text?") return raw;
  const n = Number(raw);
  if (kind.startsWith("rupees") || kind.startsWith("percent")) return Math.round(n * 100);
  return Math.round(n);
}

export default {
  id: "settings",
  title: "Settings",
  permissions: ["settings.edit"],
  mount(root) {
    const name = h("input", { name: "display_name", maxlength: 200 });
    const ordering = h("div", {}, h("p", {}, "Loading…"));
    replace(root,
      h("section", { class: "card", id: "settings-form" }, h("h1", {}, "Ordering settings"), ordering),
      h("section", { class: "card" }, h("h2", {}, "Restaurant name"), actionForm([field("Name customers see", name)], {
        method: "PATCH", path: "/api/settings",
        body: () => ({ display_name: name.value.trim() }),
        check: () => (name.value.trim() ? null : "Enter a name."),
        done: async () => {}, submitLabel: "Save name",
      })));

    async function load() {
      const settings = await get("/api/settings/ordering");
      const inputs = Object.fromEntries(FIELDS.map(([key, , kind]) => [key, input(kind, settings[key])]));
      const saved = h("p", { class: "ok", hidden: true }, "Saved.");
      replace(ordering, actionForm(FIELDS.map(([key, label]) => field(label, inputs[key])), {
        method: "PATCH", path: "/api/settings/ordering",
        body: () => Object.fromEntries(FIELDS.map(([key, , kind]) => [key, read(kind, inputs[key])])),
        check: () => (FIELDS.some(([key, , kind]) => kind !== "bool" && !kind.endsWith("?") && inputs[key].value.trim() === "")
          ? "Fill in every number." : null),
        done: async () => { saved.hidden = false; },
        submitLabel: "Save settings",
      }), saved);
    }

    load().catch((error) => replace(ordering, h("p", { class: "form-error" }, error.message)));
    return {};
  },
};
