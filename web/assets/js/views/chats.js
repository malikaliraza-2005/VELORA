// Chats (P9-T4): customers waiting for staff, their messages, a reply box and Resume bot.
// Replies go out through the outbox like every other message. Below the inbox: messages
// that failed or whose delivery is unknown, for staff to follow up by hand (P5 finding).

import { Action, get } from "../api.js";
import { field, h, replace } from "../dom.js";
import { ago, dateTime, time } from "../format.js";
import { actionButton, actionForm } from "../ui.js";

const REASONS = {
  requested: "Asked for staff",
  not_understood: "The assistant didn't understand",
};
const OUT_STATUS = {
  pending: "queued", waiting_for_channel: "waiting for WhatsApp", sending: "sending", sent: "sent",
  delivered: "delivered", read: "read", failed: "FAILED", unknown: "delivery unknown",
  stale: "dropped (too late)", cancelled: "cancelled",
};

export default {
  id: "chats",
  title: "Chats",
  permissions: ["chats.handle"],
  mount(root) {
    const inbox = h("ul", { class: "list inbox", id: "handoffs" });
    const chat = h("div", { class: "chat", id: "chat" }, h("p", { class: "muted" }, "Choose a chat."));
    const problems = h("div", { id: "message-problems" });
    replace(root,
      h("div", { class: "chats" },
        h("section", { class: "card" }, h("h1", {}, "Waiting for staff"), inbox),
        h("section", { class: "card" }, chat)),
      h("section", { class: "card" }, h("h2", {}, "Messages that need a look"),
        h("p", { class: "muted" }, "From the last day. They are never re-sent automatically; contact the customer another way if needed."),
        problems));

    let selected = null;
    let lastInbox = "";

    async function loadInbox() {
      const list = await get("/api/conversations/handoffs");
      const snapshot = JSON.stringify(list);
      if (snapshot !== lastInbox) {
        lastInbox = snapshot;
        replace(inbox, list.length ? list.map((c) => h("li", {},
          h("button", { type: "button", class: `chat-pick${selected === c.conversation_id ? " active" : ""}`, "data-conversation": c.conversation_id, onclick: () => open(c) },
            h("strong", {}, c.customer_name || c.customer_phone || "Customer"),
            h("span", { class: "muted" }, ` · ${REASONS[c.reason] || c.reason} · ${ago(c.handoff_at)}`))))
          : h("li", { class: "empty" }, "Nobody is waiting."));
      }
      if (selected && !list.some((c) => c.conversation_id === selected)) {
        selected = null;
        replace(chat, h("p", { class: "muted" }, "That chat is back with the assistant."));
      }
    }

    async function open(c) {
      selected = c.conversation_id;
      lastInbox = "";
      await loadInbox();
      await renderChat(c);
    }

    async function renderChat(c) {
      const messages = await get(`/api/conversations/${c.conversation_id}/messages`);
      const text = h("textarea", { name: "text", rows: 3, maxlength: 1000, placeholder: "Type a reply…" });
      const log = h("ol", { class: "messages" }, messages.map((m) =>
        h("li", { class: `msg msg-${m.direction}` },
          h("span", { class: "msg-text" }, m.text ?? `[${m.kind}]`),
          h("span", { class: "msg-meta" }, time(m.at), m.direction === "out" && m.status ? ` · ${OUT_STATUS[m.status] || m.status}` : ""))));
      replace(chat,
        h("h2", {}, c.customer_name || c.customer_phone || "Customer"),
        c.customer_phone ? h("p", { class: "muted" }, c.customer_phone) : null,
        log,
        actionForm([field("Reply", text)], {
          method: "POST",
          path: `/api/conversations/${c.conversation_id}/reply`,
          body: () => ({ text: text.value.trim() }),
          check: () => (text.value.trim() ? null : "Type a reply first."),
          done: async () => { text.value = ""; await renderChat(c); },
          submitLabel: "Send reply",
        }),
        actionButton("Resume bot", new Action("POST", `/api/conversations/${c.conversation_id}/resume`), async () => {
          selected = null;
          replace(chat, h("p", { class: "ok" }, "The assistant is handling this chat again."));
          lastInbox = "";
          await loadInbox();
        }, { kind: "secondary" }));
      log.scrollTop = log.scrollHeight;
    }

    async function loadProblems() {
      const list = await get("/api/conversations/message-problems");
      replace(problems, list.length ? h("ul", { class: "list" }, list.map((m) =>
        h("li", { class: `problem problem-${m.status}` },
          h("strong", {}, OUT_STATUS[m.status]), ` · to ${m.to} · ${dateTime(m.created_at)}`,
          m.text ? h("div", { class: "muted" }, m.text.slice(0, 160)) : null)))
        : h("p", { class: "empty" }, "None."));
    }

    const refresh = () => Promise.all([loadInbox(), loadProblems()]);
    refresh().catch(() => {});
    return { refresh };
  },
};
