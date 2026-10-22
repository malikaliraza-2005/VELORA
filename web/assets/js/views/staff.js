// Staff management (P9-T5): the P3 endpoints and permissions. Everyone with staff.view sees
// the list; only the owner (staff.manage) invites, changes roles, removes, signs devices
// out, resets PINs and makes login links.
//
// Login links are shown once, to pass on privately (B7: manual delivery). They are never
// stored by the page.

import { Action, get } from "../api.js";
import { dialog, field, h, replace } from "../dom.js";
import { ROLE_LABELS } from "../format.js";
import { actionButton, actionForm } from "../ui.js";

const ROLES = ["manager", "kitchen", "kitchen_device", "owner"];

function showLink(name, link) {
  const url = h("input", { readonly: true, value: link.url, class: "link-url", onfocus: (e) => e.target.select() });
  dialog(`Login link for ${name}`, h("div", {},
    h("p", {}, "Send this privately to the person (or open it on the tablet). It works once and expires in 15 minutes."),
    url,
    h("button", { type: "button", class: "secondary", onclick: () => navigator.clipboard?.writeText(link.url) }, "Copy")));
}

export default {
  id: "staff",
  title: "Staff",
  permissions: ["staff.view"],
  mount(root, app) {
    const list = h("div", { id: "staff-list" });
    const manage = app.can("staff.manage");
    const name = h("input", { name: "display_name", maxlength: 200, autocomplete: "off" });
    const role = h("select", { name: "role" }, ROLES.map((r) => h("option", { value: r }, ROLE_LABELS[r])));
    replace(root,
      h("section", { class: "card" }, h("h1", {}, "Staff"), list),
      manage ? h("section", { class: "card" }, h("h2", {}, "Invite someone"), actionForm(
        [field("Name", name), field("Role", role)], {
          method: "POST", path: "/api/staff",
          body: () => ({ display_name: name.value.trim(), role: role.value }),
          check: () => (name.value.trim() ? null : "Enter a name."),
          done: async (result) => {
            if (result && result.link) showLink(result.staff.display_name, result.link);
            name.value = "";
            await load();
          },
          submitLabel: "Invite",
        })) : null);

    function row(member) {
      const actions = [];
      if (manage && member.active && member.id !== app.me.staff_id) {
        const change = h("select", { "aria-label": "Role" }, ROLES.map((r) => h("option", { value: r, selected: r === member.role }, ROLE_LABELS[r])));
        actions.push(change,
          actionButton("Change role", new Action("PATCH", `/api/staff/${member.id}`, () => ({ role: change.value })), load, { kind: "secondary" }),
          actionButton("New login link", new Action("POST", `/api/staff/${member.id}/login-link`),
            (link) => { if (link && link.url) showLink(member.display_name, link); }, { kind: "secondary" }),
          actionButton("Sign out all devices", new Action("POST", `/api/staff/${member.id}/revoke-sessions`), load, { kind: "secondary" }),
          member.has_pin ? actionButton("Reset PIN", new Action("POST", `/api/staff/${member.id}/pin-reset`), load, { kind: "secondary" }) : null,
          actionButton("Remove", new Action("DELETE", `/api/staff/${member.id}`), load,
            { kind: "danger", confirm: `Remove ${member.display_name}? Their devices are signed out.` }));
      }
      return h("li", { class: member.active ? "" : "inactive", "data-staff": member.id },
        h("strong", {}, member.display_name),
        h("span", { class: "muted" }, ` · ${ROLE_LABELS[member.role] || member.role}${member.active ? "" : " · removed"}${member.has_pin ? " · PIN set" : ""}`),
        actions.length ? h("div", { class: "actions" }, actions) : null);
    }

    async function load() {
      const members = await get("/api/staff");
      replace(list, h("ul", { class: "list" }, members.map(row)));
    }

    load().catch((error) => replace(list, h("p", { class: "form-error" }, error.message)));
    return {};
  },
};
