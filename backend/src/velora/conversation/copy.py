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
CART_CLEARED = "Done — your cart is cleared."
NOTHING_TO_CONFIRM = "There's nothing to confirm right now. Reply MENU for the menu."
PROPOSAL_HINT = "Is this correct? Reply YES to add to your cart, or MENU to choose from the menu."
PAYMENT_CLAIM = (
    "Thanks! Staff will confirm once the payment shows in the restaurant's account. "
    "A screenshot alone does not confirm payment."
)
COD_BLOCKED = "cash on delivery isn't available for this number"
NO_ORDERS = "You don't have any orders with us yet. Reply MENU to start one."
FULFILMENT_ASK = "Delivery or takeaway?"
ADDRESS_ASK = "Please share your location 📍 (Attach → Location) or type your full address."
ADDRESS_TOO_SHORT = "Please type your full address (house, street, area), or share your location."
NOTE_ASK = 'Got it. Any delivery note? (e.g. "gate no. 2"). Reply SKIP if none.'
PICKUP_ASK = "When will you pick it up? Reply with a number, or type a time like 8:30 pm."
PAYMENT_ASK = "How will you pay?"
CHOOSE_NUMBER = "Please reply with one of the numbers above."
ORDER_FAILED = (
    "Sorry, something went wrong and your order was not placed. "
    "Reply YES to try again, or 0 to talk to staff."
)
RECONFIRM_AFTER_OUTAGE = (
    "Sorry for the delay — your previous order summary expired during a technical problem. "
    "Reply CART to review your cart and confirm again."
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


def order_placed(
    reference: str,
    *,
    total_paisa: int,
    payment_method: str,
    payment_due_at: datetime | None,
    transfer_details: str | None = None,
) -> str:
    """A7: the confirmation, from the order as stored. A transfer order carries the
    restaurant's account details (P8-T3)."""
    lines = [
        f"✅ Order #{reference} placed.",
        f"Total: {format_rupees(total_paisa)} ({PAYMENT_LABELS[payment_method]}).",
    ]
    if payment_method == "transfer" and payment_due_at is not None:
        lines += [
            f"Please pay by {format_time(payment_due_at)} to the restaurant's own account:",
            transfer_details or "(staff will send the account details here)",
            f'Put "{reference}" in the payment reference if possible. Staff will confirm once '
            "they see the payment in the restaurant's account. "
            "A screenshot alone does not confirm payment.",
        ]
    else:
        lines.append("The restaurant will confirm it here shortly.")
    lines.append('Reply "status" anytime, or CANCEL to cancel before the restaurant accepts it.')
    return "\n".join(lines)


def transfer_reminder(
    reference: str, total_paisa: int, payment_due_at: datetime, transfer_details: str | None
) -> str:
    """Order "status" while a transfer is awaited: where and by when to pay."""
    return (
        f"Please pay {format_rupees(total_paisa)} for order #{reference} by "
        f"{format_time(payment_due_at)} to:\n"
        f"{transfer_details or '(reply 0 and staff will send the account details)'}"
    )


def evidence_received(reference: str, *, expired: bool) -> str:
    """A screenshot, a document or "I have paid" for a transfer order (P8-T3). It is kept
    for staff and changes nothing about the order."""
    if expired:
        return (
            f"Thanks — order #{reference} had already expired, so staff will check your payment "
            "and get back to you here. A screenshot alone does not confirm payment."
        )
    return (
        f"Thanks — we've passed this to staff for order #{reference}. They will confirm once "
        "the payment shows in the restaurant's account. A screenshot alone does not confirm "
        "payment."
    )


def open_order_kept(reference: str) -> str:
    return (
        f"{CART_CLEARED} Your order #{reference} is still placed; reply CANCEL again to cancel it."
    )


def cannot_cancel(reference: str, status: str) -> str:
    if status == "paid_staff_verified":
        return (
            f"Your payment for order #{reference} is already verified, so staff need to cancel "
            "it and arrange the refund. Reply 0 to talk to staff."
        )
    label = ORDER_STATUS_LABELS.get(status, status)
    return (
        f"Order #{reference} is {label}, so it can't be cancelled here. Reply 0 to talk to staff."
    )


def order_update(
    reference: str, status: str, restaurant: str, *, reason: str | None, by_customer: bool
) -> str | None:
    """A8/A9: the message for an order that has just changed to `status`. None: no message
    (being prepared follows "accepted" closely, so it isn't announced)."""
    because = f" (reason: {reason})" if reason else ""
    if status == "cancelled" and by_customer:
        return f"Order #{reference} is cancelled. Reply MENU to start a new order."
    return {
        "paid_staff_verified": (
            f"💳 Payment received for order #{reference} — verified by restaurant staff. Thank you!"
        ),
        # Only after the owner withdraws a verification (P8-T6).
        "awaiting_transfer": (
            f"Sorry — {restaurant} couldn't match your payment for order #{reference} yet. "
            "Staff will check and get back to you; reply 0 if you have questions."
        ),
        "accepted": f"👨‍🍳 Order #{reference} accepted — being prepared.",
        "out_for_delivery": f"🛵 Order #{reference} is out for delivery.",
        "ready": f"✅ Order #{reference} is ready for pickup.",
        "completed": f"✅ Order #{reference} completed. Shukriya! Reply MENU to order again.",
        "rejected": (
            f"Sorry, {restaurant} couldn't accept order #{reference}{because}. "
            "No payment is needed. If you already transferred, reply 0 and staff will arrange "
            "a refund."
        ),
        "cancelled": (
            f"Sorry, {restaurant} cancelled order #{reference}{because}. "
            "If you already paid, reply 0 and staff will help."
        ),
        "expired": (
            f"We didn't receive payment for order #{reference} in time, so it was cancelled. "
            "If you already paid, reply 0 and staff will check right away."
        ),
        "refund_pending": (
            f"{restaurant} is arranging a refund for order #{reference}{because}. "
            "Reply 0 if you have questions."
        ),
    }.get(status)


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
