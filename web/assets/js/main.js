// VELORA staff dashboard: start-up, login, navigation and live updates (P9-T1, T7).
//
// - Login: a magic link opens /login#<token>. The token is read from the fragment (which
//   never reaches the server or its logs), removed from the address bar and history at once,
//   and posted to /api/auth/login. The session cookie that comes back is HttpOnly.
// - Views are shown by role: the menu only lists what the signed-in role may open, and a
//   view the role can't use shows a refusal instead (the API refuses it too).
// - Live updates by polling every few seconds (AD-33). When a poll fails the page shows
//   "Connection lost" and every action is refused until it is back.

import {
  OfflineError, SignedOutError, connection, get, markReachable, onConnectionChange, request, setCsrf,
  whenSignedOut,
} from "./api.js";
import { h, replace } from "./dom.js";
import { CHANNEL_STATES, CLOSED_REASONS, ROLE_LABELS, time } from "./format.js";
import { reportError } from "./ui.js";
import board from "./views/board.js";
import payments from "./views/payments.js";
import chats from "./views/chats.js";
import menu from "./views/menu.js";
import controls from "./views/controls.js";
import summary from "./views/summary.js";
import whatsapp from "./views/whatsapp.js";
import staff from "./views/staff.js";
import settings from "./views/settings.js";
import me from "./views/me.js";

const VIEWS = [board, payments, chats, menu, controls, summary, whatsapp, staff, settings, me];
const POLL_MS = 5000;

const root = document.getElementById("app");
const app = {
  me: null,
  can(permission) { return Boolean(this.me && this.me.permissions.includes(permission)); },
  status: null,
  channels: null,
  handoffs: 0,
  sound: false,
  current: null,
};

function allowed(view) {
  return view.permissions.every((p) => app.can(p));
}

// Screens before sign-in ---------------------------------------------------------------

function message(title, text) {
  replace(root, h("main", { class: "centre" },
    h("div", { class: "card narrow" }, h("h1", {}, title), h("p", {}, text))));
}

async function signIn() {
  const token = location.hash.slice(1);
  history.replaceState(null, "", "/");
  if (!token) {
    message("Sign in", "Open the login link the owner gave you. Links work once, for 15 minutes.");
    return null;
  }
  try {
    return await request("POST", "/api/auth/login", { token, device_label: navigator.userAgent.slice(0, 100) });
  } catch (error) {
    message("This link didn't work", `${error.message}. Ask the owner for a new login link.`);
    return null;
  }
}

// The CSP requires Trusted Types. This one policy allows exactly one script URL: the
// service worker.
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  let url = "/sw.js";
  if (window.trustedTypes) {
    const policy = trustedTypes.createPolicy("velora", {
      createScriptURL: (value) => {
        if (value !== "/sw.js") throw new TypeError("only the service worker may load");
        return value;
      },
    });
    url = policy.createScriptURL("/sw.js");
  }
  navigator.serviceWorker.register(url).catch(() => {});
}

async function start() {
  registerServiceWorker();
  let found = null;
  if (location.pathname === "/login") {
    found = await signIn();
  } else {
    try {
      found = await get("/api/me");
    } catch (error) {
      if (error.status !== 401) message("Can't reach VELORA", error.message);
      else message("Signed out", "Open a login link from the owner to sign in on this device.");
      return;
    }
  }
  if (!found) return;
  app.me = found;
  setCsrf(found.csrf_token);
  whenSignedOut(() => message("Signed out", "Your session ended. Open a new login link from the owner."));
  layout();
  window.addEventListener("hashchange", route);
  onConnectionChange(showConnection);
  // Back online: check the server straight away rather than at the next poll.
  window.addEventListener("online", () => tick());
  route();
  poll();
}

// Layout --------------------------------------------------------------------------------

const els = {};

function layout() {
  els.connection = h("div", { class: "banner banner-error", id: "connection-banner", hidden: true, role: "alert" },
    "Connection lost. Actions are switched off until it's back — nothing is saved to send later.");
  els.ordering = h("div", { class: "banner banner-warn", id: "ordering-banner", hidden: true, role: "status" });
  els.whatsapp = h("div", { class: "banner banner-error", id: "whatsapp-banner", hidden: true, role: "alert" });
  els.alert = h("div", { class: "banner banner-new", id: "new-order-banner", hidden: true, role: "alert" });
  els.nav = h("nav", { class: "nav", "aria-label": "Sections" });
  els.main = h("main", { class: "content", id: "view" });
  els.soundButton = h("button", { type: "button", class: "ghost", id: "sound-toggle", onclick: toggleSound },
    "🔕 Sound off");
  replace(root,
    h("header", { class: "top" },
      h("div", { class: "brand" }, "VELORA"),
      h("div", { class: "who" }, `${app.me.display_name} · ${ROLE_LABELS[app.me.role] || app.me.role}`),
      app.can("orders.view") ? els.soundButton : null),
    els.connection, els.ordering, els.whatsapp, els.alert, els.nav, els.main);
  renderNav();
}

function renderNav() {
  const here = currentId();
  replace(els.nav, VIEWS.filter(allowed).map((view) => {
    const badge = view.id === "chats" && app.handoffs > 0 ? h("span", { class: "badge" }, String(app.handoffs)) : null;
    return h("a", { href: `#/${view.id}`, class: view.id === here ? "active" : "", "data-view": view.id }, view.title, badge);
  }));
}

function currentId() {
  const id = location.hash.replace(/^#\//, "").split("/")[0];
  return id || (app.can("orders.view") ? "board" : "me");
}

function route() {
  if (app.current && app.current.unmount) app.current.unmount();
  app.current = null;
  const id = currentId();
  const view = VIEWS.find((v) => v.id === id);
  renderNav();
  if (!view) {
    replace(els.main, h("div", { class: "card" }, h("h1", {}, "Not found"), h("p", {}, "There's no such screen.")));
    return;
  }
  if (!allowed(view)) {
    replace(els.main, h("div", { class: "card refusal", id: "refused" },
      h("h1", {}, view.title),
      h("p", {}, "Your role can't open this screen. Ask the owner if you need it.")));
    return;
  }
  const container = h("section", { class: `view view-${view.id}`, "data-view": view.id });
  replace(els.main, container);
  app.current = view.mount(container, app) || {};
}

// Banners and live updates ----------------------------------------------------------------

function showConnection(ok) {
  els.connection.hidden = ok;
  document.body.classList.toggle("offline", !ok);
}

function showOrdering(status) {
  if (!status || status.accepting_orders) {
    els.ordering.hidden = true;
    return;
  }
  let text = CLOSED_REASONS[status.reason] || "New orders are not being taken.";
  if (status.reason === "paused" && status.paused_until) {
    text = `Ordering is paused until ${time(status.paused_until)}${status.pause_reason ? ` (${status.pause_reason})` : ""}.`;
  }
  replace(els.ordering, "New orders are stopped: ", text);
  els.ordering.hidden = false;
}

function showWhatsapp(channels) {
  const down = (channels || []).filter((c) => c.state !== "connected");
  if (!down.length) {
    els.whatsapp.hidden = true;
    return;
  }
  replace(els.whatsapp,
    `WhatsApp ${CHANNEL_STATES[down[0].state] || down[0].state}. Customers can't order until it is fixed. `,
    h("a", { href: "#/whatsapp" }, "Re-link WhatsApp"));
  els.whatsapp.hidden = false;
}

// One round of updates. Errors are shown, never thrown.
async function tick() {
  try {
    const tasks = [];
    if (app.can("orders.view")) tasks.push(get("/api/ordering/status").then((s) => { app.status = s; showOrdering(s); }));
    if (app.can("channels.manage")) tasks.push(get("/api/channels").then((c) => { app.channels = c; showWhatsapp(c); }));
    if (app.can("chats.handle")) tasks.push(get("/api/conversations/handoffs").then((list) => { app.handoffs = list.length; }));
    await Promise.all(tasks);
    renderNav();
    if (app.current && app.current.refresh) await app.current.refresh();
    markReachable(true);
  } catch (error) {
    if (error instanceof OfflineError) markReachable(false);
    else if (!(error instanceof SignedOutError)) reportError(error);
  }
}

async function poll() {
  await tick();
  setTimeout(poll, POLL_MS);
}

// New-order alert (P9-T2) ----------------------------------------------------------------

let audio = null;

function toggleSound() {
  app.sound = !app.sound;
  if (app.sound && !audio) audio = new AudioContext();
  els.soundButton.textContent = app.sound ? "🔔 Sound on" : "🔕 Sound off";
}

function beep() {
  if (!app.sound || !audio) return;
  const now = audio.currentTime;
  for (const offset of [0, 0.35]) {
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.4, now + offset);
    gain.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.3);
    osc.connect(gain).connect(audio.destination);
    osc.start(now + offset);
    osc.stop(now + offset + 0.3);
  }
}

app.newOrders = (references) => {
  replace(els.alert, `New order${references.length > 1 ? "s" : ""}: ${references.join(", ")}`);
  els.alert.hidden = false;
  beep();
  clearTimeout(app.alertTimer);
  app.alertTimer = setTimeout(() => { els.alert.hidden = true; }, 30000);
};

// After an action: update now rather than at the next poll.
app.refreshNow = tick;
app.connection = connection;

start();
