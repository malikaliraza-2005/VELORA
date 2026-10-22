"""Staff roles and the role-permission matrix (P3-T4, AD-23).

Every privileged endpoint names the permission it needs; nothing checks roles directly. Menu
endpoints (P4) must require `MENU_EDIT`, payment confirmation (P8) `PAYMENTS_CONFIRM`.

The payment reconciliation report and the reversal of a verified payment are the owner's
alone (P8-T6): they are the check on everyone else's confirmations. So is re-linking the
WhatsApp number (P9-T6): a pairing QR code gives control of the restaurant's ordering number.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    KITCHEN = "kitchen"
    # A shared kitchen tablet, not a person. It has no personal PIN.
    KITCHEN_DEVICE = "kitchen_device"


class Permission(StrEnum):
    ORDERS_VIEW = "orders.view"
    ORDERS_UPDATE = "orders.update"
    CHATS_HANDLE = "chats.handle"
    # Held by the person whose PIN confirms a payment, not by the session (see P3-T5).
    PAYMENTS_CONFIRM = "payments.confirm"
    # P8-T6: owner only.
    PAYMENTS_RECONCILE = "payments.reconcile"
    PAYMENTS_REVERSE = "payments.reverse"
    # P8-T2: the cash-on-delivery blocklist.
    CUSTOMERS_BLOCK_COD = "customers.block_cod"
    MENU_EDIT = "menu.edit"
    SETTINGS_EDIT = "settings.edit"
    # P9-T6: the WhatsApp channel's status details and QR re-link. Owner only.
    CHANNELS_MANAGE = "channels.manage"
    # P9-T5: the today summary (takings). Kitchen roles see only the order board.
    REPORTS_VIEW = "reports.view"
    STAFF_VIEW = "staff.view"
    # AD-12: staff are added only by owner invite.
    STAFF_MANAGE = "staff.manage"


OWNER_ONLY: frozenset[Permission] = frozenset(
    {
        Permission.STAFF_MANAGE,
        Permission.PAYMENTS_RECONCILE,
        Permission.PAYMENTS_REVERSE,
        Permission.CHANNELS_MANAGE,
    }
)

MATRIX: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.MANAGER: frozenset(Permission) - OWNER_ONLY,
    Role.KITCHEN: frozenset(
        {Permission.ORDERS_VIEW, Permission.ORDERS_UPDATE, Permission.PAYMENTS_CONFIRM}
    ),
    Role.KITCHEN_DEVICE: frozenset({Permission.ORDERS_VIEW, Permission.ORDERS_UPDATE}),
}


def allows(role: Role, permission: Permission) -> bool:
    return permission in MATRIX[role]


def is_person(role: Role) -> bool:
    """Whether the role belongs to one human, who can hold a personal PIN."""
    return role is not Role.KITCHEN_DEVICE
