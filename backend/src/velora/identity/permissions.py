"""Staff roles and the role-permission matrix (P3-T4, AD-23).

Every privileged endpoint names the permission it needs; nothing checks roles directly. Menu
endpoints (P4) must require `MENU_EDIT`, payment confirmation (P8) `PAYMENTS_CONFIRM`.
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
    MENU_EDIT = "menu.edit"
    SETTINGS_EDIT = "settings.edit"
    STAFF_VIEW = "staff.view"
    # AD-12: staff are added only by owner invite.
    STAFF_MANAGE = "staff.manage"


MATRIX: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.MANAGER: frozenset(Permission) - {Permission.STAFF_MANAGE},
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
