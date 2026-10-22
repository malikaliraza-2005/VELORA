// Building the page without HTML strings (P9-T1). Text always goes in as text nodes, so
// nothing a customer typed can become markup. The CSP requires Trusted Types, so writing an
// HTML string into the page would be refused anyway.

/**
 * h("button", {class: "primary", onclick: fn, disabled: true}, "Accept")
 * Keys starting with "on" are event listeners; value, checked, disabled and selected are
 * properties; everything else is an attribute. Children may be strings, nodes, arrays or
 * null/false (skipped).
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (["value", "checked", "disabled", "selected"].includes(key)) {
      el[key] = value;
    } else if (key === "class") {
      el.className = value;
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replace(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

/** A short message at the bottom of the screen. kind: "ok" | "error". */
export function toast(text, kind = "ok") {
  let box = document.getElementById("toasts");
  if (!box) {
    box = h("div", { id: "toasts", role: "status", "aria-live": "assertive" });
    document.body.append(box);
  }
  const item = h("div", { class: `toast toast-${kind}` }, text);
  box.append(item);
  setTimeout(() => item.remove(), kind === "error" ? 8000 : 4000);
}

/** A modal dialog. Returns {close}. */
export function dialog(title, body, { onclose } = {}) {
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (onclose) onclose();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const panel = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "dialog-head" },
      h("h2", {}, title),
      h("button", { class: "ghost", type: "button", onclick: close, "aria-label": "Close" }, "✕")),
    body);
  const overlay = h("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(); } }, panel);
  document.body.append(overlay);
  document.addEventListener("keydown", onKey);
  const first = panel.querySelector("input, select, textarea");
  if (first) first.focus();
  return { close, panel };
}

/** A labelled form field. */
export function field(label, input, hint) {
  return h("label", { class: "field" },
    h("span", { class: "field-label" }, label),
    input,
    hint ? h("span", { class: "field-hint" }, hint) : null);
}
