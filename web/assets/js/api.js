// Talking to the VELORA API (P9-T1, T2, T7).
//
// - The session is an HttpOnly cookie the page can't read. The CSRF token comes from
//   /api/me and lives only in this module's memory, never in browser storage.
// - Every action (anything but GET) carries the CSRF token and an Idempotency-Key. An
//   `Action` keeps one key for as long as it is on screen, so tapping it twice, or retrying
//   it after a lost answer, makes one change.
// - Offline-aware: while the connection is lost, actions are refused at once with a visible
//   message. Nothing is ever queued to be sent later.

let csrfToken = null;
const listeners = new Set();

export const connection = {
  online: navigator.onLine,
  // Set by the poll loop: false after a request to the server failed.
  reachable: true,
  get ok() { return this.online && this.reachable; },
};

export function onConnectionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setConnection(changes) {
  const before = connection.ok;
  Object.assign(connection, changes);
  if (before !== connection.ok) listeners.forEach((fn) => fn(connection.ok));
}

window.addEventListener("online", () => setConnection({ online: true }));
window.addEventListener("offline", () => setConnection({ online: false }));

export class ApiError extends Error {
  constructor(status, detail, body) {
    super(detail || `request failed (${status})`);
    this.status = status;
    this.body = body;
  }
}

export class OfflineError extends Error {
  constructor() {
    super("Connection lost — this was not sent. Nothing is saved to send later; try again once the connection is back.");
  }
}

export class SignedOutError extends Error {
  constructor() { super("You're signed out. Open a new login link from the owner."); }
}

let onSignedOut = () => {};
export function whenSignedOut(fn) { onSignedOut = fn; }

export function setCsrf(token) { csrfToken = token; }

export function newKey() {
  return crypto.randomUUID();
}

function detailOf(body, status) {
  if (body && typeof body.detail === "string") return body.detail;
  if (body && Array.isArray(body.detail)) {
    return body.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return `request failed (${status})`;
}

/**
 * request("POST", "/api/orders/…/status", {from, to}, {key})
 * Resolves to the parsed JSON (or null), and to {replayed: true} when the server says this
 * key was already done. Rejects with OfflineError, SignedOutError or ApiError.
 */
export async function request(method, path, body, { key } = {}) {
  const unsafe = method !== "GET";
  if (unsafe && !connection.ok) throw new OfflineError();
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (unsafe) {
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    headers["Idempotency-Key"] = key || newKey();
  }
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    setConnection({ reachable: false });
    throw new OfflineError();
  }
  setConnection({ reachable: true });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : null;
  if (response.status === 401 && path !== "/api/auth/login") {
    onSignedOut();
    throw new SignedOutError();
  }
  if (!response.ok) throw new ApiError(response.status, detailOf(data, response.status), data);
  return data;
}

export const get = (path) => request("GET", path);

/** Mark the server reachable or not from a poll that didn't throw OfflineError itself. */
export function markReachable(ok) { setConnection({ reachable: ok }); }

/**
 * One action button's request. The key is made when the action is created and reused for
 * every attempt, and `run` refuses to start while an attempt is in flight.
 */
export class Action {
  constructor(method, path, bodyFn) {
    this.method = method;
    this.path = path;
    this.bodyFn = bodyFn || (() => undefined);
    this.key = newKey();
    this.busy = false;
  }

  async run() {
    if (this.busy) return { skipped: true };
    this.busy = true;
    try {
      return await request(this.method, this.path, this.bodyFn(), { key: this.key });
    } finally {
      this.busy = false;
    }
  }
}
