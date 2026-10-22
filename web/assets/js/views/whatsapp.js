// WhatsApp status and QR re-link (P9-T6). Owner only.
//
// The QR code is an image from the server (no-store), shown only while pairing. It never
// passes through the page's script, and is never logged or saved. Once the phone has scanned
// it, the provider reports "connected" and the banner clears at the next update.

import { Action, get } from "../api.js";
import { h, replace } from "../dom.js";
import { CHANNEL_STATES, dateTime } from "../format.js";
import { actionButton } from "../ui.js";

export default {
  id: "whatsapp",
  title: "WhatsApp",
  permissions: ["channels.manage"],
  mount(root, app) {
    const body = h("div", {}, h("p", {}, "Loading…"));
    replace(root, h("section", { class: "card" }, h("h1", {}, "WhatsApp connection"), body));
    // The channel being paired. Its QR image is reloaded on every update, because the code
    // changes while it waits to be scanned.
    let pairing = null;
    let last = "";

    function qrImage(channelId) {
      const waiting = h("p", { class: "muted", hidden: true }, "Waiting for a QR code from WhatsApp…");
      const img = h("img", {
        src: `/api/channels/${channelId}/qr.svg?n=${Date.now()}`, alt: "WhatsApp pairing QR code",
        id: "qr-image", width: 264, height: 264,
      });
      // 204 (no code yet, or already paired) loads as a broken image.
      img.addEventListener("error", () => { img.hidden = true; waiting.hidden = false; });
      return h("figure", { class: "qr" }, img, waiting,
        h("figcaption", {}, "On the restaurant's ordering phone: WhatsApp → Settings → Linked devices → Link a device, then scan this code."));
    }

    async function load() {
      const channels = await get("/api/channels");
      const snapshot = JSON.stringify(channels.map((c) => [c.id, c.state]));
      if (snapshot === last && !pairing) return;
      last = snapshot;
      const parts = [];
      for (const c of channels) {
        const connected = c.state === "connected";
        if (connected && pairing === c.id) pairing = null;
        parts.push(h("div", { class: `channel channel-${c.state}`, "data-channel": c.id },
          h("p", {}, h("strong", {}, connected ? "✅ Connected" : `⚠ ${CHANNEL_STATES[c.state] || c.state}`),
            c.state_changed_at ? h("span", { class: "muted" }, ` since ${dateTime(c.state_changed_at)}`) : null),
          !connected && !c.adapter_running
            ? h("p", { class: "muted" }, "This connection can't be re-linked from the dashboard yet. Contact VELORA support.")
            : null,
          !connected && c.adapter_running
            ? actionButton(pairing === c.id ? "Get a new QR code" : "Re-link WhatsApp", new Action("POST", `/api/channels/${c.id}/relink`),
              async () => { pairing = c.id; last = ""; await load(); await app.refreshNow(); })
            : null));
        if (pairing === c.id && !connected) parts.push(qrImage(c.id));
      }
      if (!channels.length) parts.push(h("p", { class: "empty" }, "No WhatsApp number is set up. VELORA support registers it during onboarding."));
      replace(body, parts);
    }

    load().catch((error) => replace(body, h("p", { class: "form-error" }, error.message)));
    return { refresh: load };
  },
};
