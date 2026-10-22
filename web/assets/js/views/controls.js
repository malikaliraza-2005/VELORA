// Controls (P9-T5): pause and resume ordering, the capacity cap and the AI switch. Uses the
// P4 endpoints and their permission (settings.edit).

import { Action, get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { CLOSED_REASONS, time } from "../format.js";
import { actionButton, actionForm } from "../ui.js";

export default {
  id: "controls",
  title: "Controls",
  permissions: ["settings.edit"],
  mount(root, app) {
    const status = h("div", { id: "ordering-status" });
    const pause = h("div");
    const capacity = h("div");
    const ai = h("div");
    replace(root,
      h("section", { class: "card" }, h("h1", {}, "Taking orders"), status, pause),
      h("section", { class: "card" }, h("h2", {}, "Kitchen capacity"), capacity),
      h("section", { class: "card" }, h("h2", {}, "Ordering assistant"), ai));

    async function load() {
      const [s, settings] = await Promise.all([get("/api/ordering/status"), get("/api/settings/ordering")]);
      replace(status, s.accepting_orders
        ? h("p", { class: "ok" }, "New orders are being taken.")
        : h("p", { class: "warn" }, CLOSED_REASONS[s.reason] || "New orders are stopped.",
          s.paused_until ? ` Until ${time(s.paused_until)}.` : ""));
      if (s.paused_until) {
        replace(pause, actionButton("Resume ordering now", new Action("POST", "/api/ordering/resume"), changed));
      } else {
        const minutes = h("select", { name: "minutes" },
          [15, 30, 60, 120, 240].map((m) => h("option", { value: m }, m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? "s" : ""}`)));
        const reason = h("input", { name: "reason", maxlength: 200, placeholder: "e.g. kitchen busy" });
        replace(pause, actionForm([field("Pause for", minutes), field("Reason (optional)", reason)], {
          method: "POST", path: "/api/ordering/pause",
          body: () => ({ minutes: Number(minutes.value), reason: reason.value.trim() || undefined }),
          done: changed, submitLabel: "Pause new orders",
        }));
      }
      const cap = h("input", { name: "max_orders_per_slot", type: "number", min: 1, max: 1000, value: settings.max_orders_per_slot ?? "" });
      replace(capacity,
        h("p", { class: "muted" }, `This slot: ${s.slot_orders} order${s.slot_orders === 1 ? "" : "s"}${s.max_orders_per_slot ? ` of ${s.max_orders_per_slot}` : ""}.`),
        actionForm([field("Most orders per time slot (empty: no limit)", cap)], {
          method: "PATCH", path: "/api/settings/ordering",
          body: () => ({ max_orders_per_slot: cap.value === "" ? null : Number(cap.value) }),
          check: () => (cap.value !== "" && !(Number(cap.value) >= 1) ? "Enter a number, or leave it empty." : null),
          done: changed, submitLabel: "Save capacity",
        }));
      replace(ai,
        h("p", {}, settings.ai_enabled
          ? "The AI assistant reads customers' messages and suggests a cart. Prices always come from your menu."
          : "The AI assistant is off: customers order from the numbered menu."),
        actionButton(settings.ai_enabled ? "Turn the AI off" : "Turn the AI on",
          new Action("PATCH", "/api/settings/ordering", () => ({ ai_enabled: !settings.ai_enabled })), changed,
          { kind: "secondary" }));
    }

    async function changed() {
      await load();
      await app.refreshNow();
    }

    load().catch(() => {});
    return {};
  },
};
