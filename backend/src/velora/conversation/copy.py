"""Customer-facing wording (P6). Draft, based on MVP scope Appendix A; not yet tested with
customers. Mainly English with light Roman Urdu. Amounts are always formatted from database
values by the caller; nothing here comes from AI output."""

from __future__ import annotations

from datetime import datetime

from velora.conversation.pktime import format_time
from velora.menu.model import format_rupees
from velora.ordering.gate import ClosedReason


def disclosure(restaurant: str) -> str:
    return (
        f"I'm the automated ordering assistant for {restaurant} 🤖\n"
        "Reply 0 anytime to talk to staff, or STOP to stop messages."
    )


MENU_HINT = 'Reply with an item number, e.g. "1", or "1 x2" for two. Reply MENU for all categories.'
MENU_FOOTER = "Reply CART to view your cart, or 0 to talk to staff."
NO_MENU = "Sorry, the menu isn't available right now. Reply 0 to talk to staff."
ADDED_HINT = "Reply MENU to add more, CART to view your cart, or CHECKOUT to order."
NOT_UNDERSTOOD = (
    'Sorry, I didn\'t understand. Reply MENU for the menu, "status" for your order, or 0 for staff.'
)
AI_FALLBACK = "Sorry, I couldn't read that one. Please choose from the menu:"
HANDOFF = "Connecting you to restaurant staff — someone will reply here shortly."
HANDOFF_AFTER_MISSES = "Let me get a staff member to help you."
RESUMED = "You're back with the ordering assistant. Reply MENU for the menu."
OPTED_OUT_TEMPLATE = (
    "You won't receive more messages from {restaurant}'s assistant. Send START to resume."
)
VOICE_NOTE = (
    "Sorry, I can't listen to voice notes yet. Please type your order, or reply 0 to talk to staff."
)
TEXT_ONLY = "Sorry, I can only read typed messages. Please type your order, or reply 0 for staff."
CART_EMPTY = "Your cart is empty. Reply MENU to see the menu."
CART_CLEARED = (
    "Done — your cart is cleared. If you already placed an order, reply 0 and staff will help."
)
NOTHING_TO_CONFIRM = "There's nothing to confirm right now. Reply MENU for the menu."
PROPOSAL_HINT = "Is this correct? Reply YES to add to your cart, or MENU to choose from the menu."
PAYMENT_CLAIM = (
    "Thanks! Staff will confirm once the payment shows in the restaurant's account. "
    "A screenshot alone does not confirm payment."
)
NO_ORDERS = "You don't have any orders with us yet. Reply MENU to start one."
FULFILMENT_ASK = "Delivery or takeaway?"
ADDRESS_ASK = "Please share your location 📍 (Attach → Location) or type your full address."
ADDRESS_TOO_SHORT = "Please type your full address (house, street, area), or share your location."
NOTE_ASK = 'Got it. Any delivery note? (e.g. "gate no. 2"). Reply SKIP if none.'
PICKUP_ASK = "When will you pick it up? Reply with a number, or type a time like 8:30 pm."
PAYMENT_ASK = "How will you pay?"
CHOOSE_NUMBER = "Please reply with one of the numbers above."
NOT_LIVE_YET = (
    "Thanks! Placing orders over WhatsApp isn't switched on yet for this restaurant. "
    "Reply 0 to talk to staff."
)
SUMMARY_FOOTER = "Reply YES within 10 minutes to place this order. Reply CART to change it."
SUMMARY_UPDATED = (
    "This order summary has expired or something changed (an item or price). "
    "Here is the updated summary:"
)
PAYMENT_LABELS = {"cod": "Cash on delivery", "transfer": "Bank / wallet transfer"}
TAX_LABELS = {"cod": "Tax (cash)", "transfer": "Tax (digital payment)"}
FULFILMENT_LABELS = {"delivery": "Delivery", "takeaway": "Takeaway"}

ORDER_STATUS_LABELS = {
    "draft": "being prepared for confirmation",
    "awaiting_confirmation": "waiting for your confirmation",
    "cod_confirmed": "placed (cash on delivery), waiting for the restaurant",
    "awaiting_transfer": "waiting for your transfer",
    "paid_staff_verified": "payment verified, waiting for the restaurant",
    "accepted": "accepted",
    "rejected": "not accepted by the restaurant",
    "preparing": "being prepared",
    "out_for_delivery": "out for delivery",
    "ready": "ready for pickup",
    "completed": "completed",
    "cancelled": "cancelled",
    "expired": "expired",
    "refund_pending": "refund pending",
}


def opted_out(restaurant: str) -> str:
    return OPTED_OUT_TEMPLATE.format(restaurant=restaurant)


def closed(restaurant: str, reason: ClosedReason | None, reopens_at: datetime | None) -> str:
    why = {
        "at_capacity": " (very busy)",
        "paused": " (paused for a while)",
    }.get(reason or "", "")
    text = f"{restaurant} isn't taking new orders right now{why}."
    if reopens_at is not None:
        text += f" Please try again after {format_time(reopens_at)}."
    else:
        text += " Please try again later."
    return text + " Reply 0 to talk to staff."


def delivery_info(*, delivery: bool, takeaway: bool, fee_paisa: int, minimum_paisa: int) -> str:
    if not delivery:
        return "We're taking takeaway orders only right now. Reply MENU to order."
    text = f"Delivery is {format_rupees(fee_paisa)}"
    if minimum_paisa:
        text += f", with a minimum order of {format_rupees(minimum_paisa)}"
    text += "."
    if takeaway:
        text += " Takeaway has no delivery fee."
    return text + " Reply MENU to order."


def order_status(number: str, status: str, since: datetime) -> str:
    label = ORDER_STATUS_LABELS.get(status, status)
    return f"Order #{number}: {label} (since {format_time(since)})."
