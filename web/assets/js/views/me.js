// Me: the payment PIN and signing out (P3 endpoints). A shared kitchen tablet has no
// personal PIN; each person confirms payments with their own.

import { Action } from "../api.js";
import { field, h, replace } from "../dom.js";
import { ROLE_LABELS } from "../format.js";
import { actionButton, actionForm } from "../ui.js";

function signedOut() {
  location.replace("/");
}

export default {
  id: "me",
  title: "Me",
  permissions: [],
  mount(root, app) {
    const parts = [h("section", { class: "card" },
      h("h1", {}, app.me.display_name),
      h("p", {}, ROLE_LABELS[app.me.role] || app.me.role),
      h("div", { class: "actions" },
        actionButton("Sign out", new Action("POST", "/api/auth/logout"), signedOut, { kind: "secondary" }),
        actionButton("Sign out on all my devices", new Action("POST", "/api/auth/logout-all"), signedOut, { kind: "secondary" })))];

    if (app.me.role !== "kitchen_device") {
      const current = h("input", { type: "password", inputmode: "numeric", autocomplete: "off", maxlength: 12 });
      const next = h("input", { type: "password", inputmode: "numeric", autocomplete: "off", maxlength: 12 });
      const again = h("input", { type: "password", inputmode: "numeric", autocomplete: "off", maxlength: 12 });
      const saved = h("p", { class: "ok", hidden: true }, "PIN saved.");
      parts.push(h("section", { class: "card" }, h("h2", {}, "Payment PIN"),
        h("p", { class: "muted" }, "Six digits. You need it to confirm a transfer. Never share it, not even with the owner."),
        actionForm([
          field("Current PIN (leave empty if you've never set one)", current),
          field("New PIN", next),
          field("New PIN again", again),
        ], {
          method: "PUT", path: "/api/me/pin",
          body: () => ({ current_pin: current.value || undefined, new_pin: next.value }),
          check: () => (!/^\d{6}$/.test(next.value) ? "A PIN is exactly 6 digits."
            : next.value !== again.value ? "The two new PINs don't match." : null),
          done: async () => { current.value = next.value = again.value = ""; saved.hidden = false; },
          submitLabel: "Save PIN",
        }), saved));
    }
    replace(root, parts);
    return {};
  },
};
