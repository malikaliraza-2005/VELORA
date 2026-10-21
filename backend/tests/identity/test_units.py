"""P3 unit tests without a database: tokens, PINs, the permission matrix, rate limiting."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest

from velora.clock import FakeClock
from velora.db import TenantId
from velora.identity.permissions import MATRIX, Permission, Role, allows, is_person
from velora.identity.pins import WeakPinError, hash_pin, validate_new_pin, verify_pin
from velora.identity.ratelimit import RateLimiter
from velora.identity.tokens import csrf_matches, csrf_token_for, new_token, parse_token

TENANT = TenantId(uuid.UUID("8d6f1c1e-0000-4000-8000-000000000001"))

# Tokens -----------------------------------------------------------------------------------


def test_token_round_trips_and_stores_only_a_hash() -> None:
    token = new_token(TENANT)
    wire = token.wire()
    assert parse_token(wire) == token
    assert token.hash == hashlib.sha256(token.secret).digest()
    assert token.hash not in wire.encode()


def test_tokens_are_unique() -> None:
    assert len({new_token(TENANT).wire() for _ in range(100)}) == 100


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not-a-token",
        f"{TENANT.value.hex}.",
        f"{TENANT.value.hex}.{'A' * 42}",
        f"{TENANT.value.hex}.{'A' * 44}",
        f"{TENANT.value.hex.upper()}.{'A' * 43}",
        f"{TENANT.value}.{'A' * 43}",  # dashed UUID
        f"{TENANT.value.hex}.{'A' * 42}=",
        f"{TENANT.value.hex}.{'A' * 43}\n",
    ],
)
def test_malformed_tokens_are_rejected(raw: str | None) -> None:
    assert parse_token(raw) is None


def test_csrf_token_is_bound_to_the_session() -> None:
    a, b = new_token(TENANT), new_token(TENANT)
    assert csrf_matches(a, csrf_token_for(a))
    assert not csrf_matches(a, csrf_token_for(b))
    assert not csrf_matches(a, None)
    assert not csrf_matches(a, "")


# PINs -------------------------------------------------------------------------------------


def test_pin_hash_verifies_and_hides_the_pin() -> None:
    stored = hash_pin("480135")
    assert "480135" not in stored
    assert verify_pin("480135", stored)
    assert not verify_pin("480136", stored)
    assert hash_pin("480135") != stored  # salted


@pytest.mark.parametrize(
    "pin", ["", "12345", "1234567", "12a456", " 48013", "111111", "123456", "987654"]
)
def test_weak_or_malformed_pins_are_refused(pin: str) -> None:
    with pytest.raises(WeakPinError):
        validate_new_pin(pin)


@pytest.mark.parametrize("stored", [None, "", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$AA$AA"])
def test_verify_pin_fails_closed_on_bad_hashes(stored: str | None) -> None:
    assert not verify_pin("480135", stored)


# Permission matrix (P3-T4) ----------------------------------------------------------------


def test_role_permission_matrix() -> None:
    """The whole matrix, written out, so any change to it is a deliberate test change."""
    expected = {
        Role.OWNER: set(Permission),
        Role.MANAGER: set(Permission)
        - {Permission.STAFF_MANAGE, Permission.PAYMENTS_RECONCILE, Permission.PAYMENTS_REVERSE},
        Role.KITCHEN: {
            Permission.ORDERS_VIEW,
            Permission.ORDERS_UPDATE,
            Permission.PAYMENTS_CONFIRM,
        },
        Role.KITCHEN_DEVICE: {Permission.ORDERS_VIEW, Permission.ORDERS_UPDATE},
    }
    assert {role: set(perms) for role, perms in MATRIX.items()} == expected


@pytest.mark.parametrize(
    "permission",
    [
        Permission.MENU_EDIT,
        Permission.SETTINGS_EDIT,
        Permission.STAFF_MANAGE,
        Permission.STAFF_VIEW,
        Permission.PAYMENTS_CONFIRM,
    ],
)
def test_kitchen_device_cannot_change_menu_settings_staff_or_payments(
    permission: Permission,
) -> None:
    assert not allows(Role.KITCHEN_DEVICE, permission)


def test_only_the_owner_manages_staff() -> None:
    assert [r for r in Role if allows(r, Permission.STAFF_MANAGE)] == [Role.OWNER]


@pytest.mark.parametrize("permission", [Permission.PAYMENTS_RECONCILE, Permission.PAYMENTS_REVERSE])
def test_only_the_owner_reconciles_and_reverses_payments(permission: Permission) -> None:
    """P8-T6: the owner's report and reversal are the check on everyone else."""
    assert [r for r in Role if allows(r, permission)] == [Role.OWNER]


def test_kitchen_device_is_not_a_person() -> None:
    assert [r for r in Role if not is_person(r)] == [Role.KITCHEN_DEVICE]


# Rate limiting ----------------------------------------------------------------------------


def test_rate_limiter_refuses_above_the_limit_and_recovers() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, timedelta(minutes=5), clock)
    assert [limiter.allow("ip-1") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("ip-2")
    clock.advance(timedelta(minutes=5, seconds=1))
    assert limiter.allow("ip-1")
