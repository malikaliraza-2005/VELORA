// Display formats. Money comes from the server in paisa; times are shown in Pakistan time.

const TZ = "Asia/Karachi";

export function rupees(paisa) {
  const negative = paisa < 0;
  const abs = Math.abs(paisa);
  const whole = Math.floor(abs / 100).toLocaleString("en-PK");
  const cents = abs % 100;
  return `${negative ? "−" : ""}Rs ${whole}${cents ? "." + String(cents).padStart(2, "0") : ""}`;
}

export function time(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-PK", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
}

export function dateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-PK", {
    timeZone: TZ, day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

export function ago(iso) {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} h ago` : dateTime(iso);
}

export function pakistanDate(when = new Date()) {
  // en-CA formats as YYYY-MM-DD.
  return when.toLocaleDateString("en-CA", { timeZone: TZ });
}

export const STATUS_LABELS = {
  cod_confirmed: "New (cash)",
  awaiting_transfer: "Awaiting transfer",
  paid_staff_verified: "Paid — accept it",
  accepted: "Accepted",
  preparing: "Preparing",
  ready: "Ready for pickup",
  out_for_delivery: "Out for delivery",
  completed: "Completed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  expired: "Expired (unpaid)",
  refund_pending: "Refund due",
};

// The button that moves an order to each status.
export const MOVE_LABELS = {
  accepted: "Accept",
  rejected: "Reject",
  cancelled: "Cancel order",
  preparing: "Start preparing",
  ready: "Ready for pickup",
  out_for_delivery: "Out for delivery",
  completed: "Done",
};

export const ROLE_LABELS = {
  owner: "Owner",
  manager: "Manager",
  kitchen: "Kitchen",
  kitchen_device: "Kitchen tablet (shared)",
};

export const CHANNEL_STATES = {
  pending: "not set up yet",
  connected: "connected",
  reconnecting: "reconnecting",
  needs_qr: "needs the QR code scanned",
  logged_out: "logged out",
  failed: "not reachable",
  suspected_restricted: "possibly restricted by WhatsApp",
};

// Why new orders are refused (velora.ordering.gate).
export const CLOSED_REASONS = {
  global_pause: "VELORA has paused all ordering. The founder has been alerted.",
  channel_unavailable: "WhatsApp is disconnected, so no orders can come in. Tell the owner.",
  restaurant_unavailable: "This restaurant isn't live for ordering.",
  paused: "Ordering is paused.",
  at_capacity: "The kitchen is at its order limit for this time slot.",
  no_menu: "No menu is published.",
};
