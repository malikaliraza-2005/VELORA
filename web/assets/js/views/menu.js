// Menu (P9-T5, P4 endpoints and their permission, menu.edit):
// - availability: mark live items sold out or back, straight away;
// - the draft: review what changes against the live menu, then publish it;
// - CSV import into the draft (the same format as the founder's import);
// - earlier versions, which can be published again (rollback).
// Customers only ever see a published version; a draft changes nothing until it is published.

import { Action, get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { dateTime, rupees } from "../format.js";
import { actionButton, actionForm } from "../ui.js";

const CSV_HINT = "Columns: kind,category,sku,name,price,description,group,min,max — one item row, then its option rows. Prices in rupees.";

export default {
  id: "menu",
  title: "Menu",
  permissions: ["menu.edit"],
  mount(root) {
    const live = h("div", {}, h("p", {}, "Loading…"));
    const draft = h("div", { id: "menu-draft" });
    const versions = h("div", { id: "menu-versions" });
    replace(root,
      h("section", { class: "card" }, h("h1", {}, "Menu availability"),
        h("p", { class: "muted" }, "Sold-out items are hidden from customers straight away."), live),
      h("section", { class: "card" }, h("h2", {}, "Draft menu"), draft),
      h("section", { class: "card" }, h("h2", {}, "Versions"), versions));

    async function loadLive() {
      let menu;
      try {
        menu = await get("/api/menu/live");
      } catch (error) {
        if (error.status === 404) {
          replace(live, h("p", { class: "empty" }, "No menu is published yet. Import one below, check it, then publish it."));
          return;
        }
        throw error;
      }
      replace(live, h("p", { class: "muted" }, `Live menu, version ${menu.version_no}.`),
        menu.categories.map((c) => h("div", { class: "menu-category" }, h("h3", {}, c.name),
          h("ul", { class: "list" }, c.items.map((item) => h("li", { class: item.available ? "" : "sold-out", "data-sku": item.sku },
            h("span", {}, item.name, " ", h("span", { class: "muted" }, rupees(item.price_paisa))),
            item.available ? null : h("span", { class: "badge badge-warn" }, "Sold out"),
            " ",
            actionButton(item.available ? "Mark sold out" : "Back in stock",
              new Action("PUT", `/api/menu/availability/${encodeURIComponent(item.sku)}`, () => ({ available: !item.available })),
              loadLive, { kind: item.available ? "secondary" : "primary" })))))));
    }

    function changes(diff) {
      const lines = [
        ...diff.added_skus.map((s) => `Added: ${s}`),
        ...diff.removed_skus.map((s) => `Removed: ${s}`),
        ...diff.price_changes.map((p) => `Price: ${p.key} ${rupees(p.from_paisa)} → ${rupees(p.to_paisa)}`),
      ];
      return lines.length ? h("ul", { class: "changes" }, lines.map((l) => h("li", {}, l)))
        : h("p", { class: "muted" }, "No differences from the live menu.");
    }

    async function loadDraft() {
      const [current, history] = await Promise.all([get("/api/menu/draft"), get("/api/menu/versions")]);
      const pending = history.versions.find((v) => v.status === "draft");
      const items = current.menu.categories.reduce((n, c) => n + c.items.length, 0);
      const csv = h("textarea", { name: "csv", rows: 6, placeholder: CSV_HINT });
      const file = h("input", { type: "file", accept: ".csv,text/csv" });
      file.addEventListener("change", async () => { if (file.files[0]) csv.value = await file.files[0].text(); });
      replace(draft,
        pending
          ? [h("p", {}, `Draft version ${pending.version_no}: ${current.menu.categories.length} categories, ${items} items. Saved ${dateTime(pending.created_at)}.`),
            changes(current.changes_vs_live),
            actionButton("Publish this draft", new Action("POST", `/api/menu/versions/${pending.id}/publish`, () => ({ revision: pending.revision })),
              reloadAll, { confirm: "Publish this menu? Customers see it from their next message." })]
          : h("p", { class: "muted" }, "No draft. Importing a CSV makes one."),
        h("h3", {}, "Import a CSV into the draft"),
        actionForm([field("CSV file", file), field("…or paste it", csv, CSV_HINT)], {
          method: "POST", path: "/api/menu/draft/import",
          body: () => ({ base_revision: current.base_revision, csv: csv.value }),
          check: () => (csv.value.trim() ? null : "Choose a CSV file or paste one."),
          done: reloadAll,
          submitLabel: "Import into draft",
        }));
      replace(versions, h("ul", { class: "list" }, history.versions.map((v) => h("li", { "data-version": v.version_no },
        h("strong", {}, `Version ${v.version_no}`), ` · ${v.status} · ${dateTime(v.created_at)} `,
        v.status === "previous"
          ? actionButton("Publish again", new Action("POST", `/api/menu/versions/${v.id}/publish`, () => ({ revision: v.revision })),
            reloadAll, { kind: "secondary", confirm: `Go back to version ${v.version_no}?` })
          : null))));
    }

    async function reloadAll() {
      await Promise.all([loadLive(), loadDraft()]);
    }

    loadLive().catch((error) => replace(live, h("p", { class: "form-error" }, error.message)));
    loadDraft().catch((error) => replace(draft, h("p", { class: "form-error" }, error.message)));
    return {};
  },
};
