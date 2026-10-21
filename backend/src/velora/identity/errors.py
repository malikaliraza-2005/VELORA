"""Errors raised by the identity services. The API maps them to HTTP status codes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def expect_row[T: Sequence[Any]](row: T | None) -> T:
    """A row a statement must return (INSERT ... RETURNING, a foreign-key target)."""
    if row is None:
        raise RuntimeError("expected a row, got none")
    return row


class IdentityError(Exception):
    """Base class. The message is safe to show to the signed-in staff member."""


class NotFoundError(IdentityError):
    pass


class ForbiddenError(IdentityError):
    pass


class ConflictError(IdentityError):
    pass


class InvalidInputError(IdentityError):
    pass


class PinRejectedError(IdentityError):
    """A payment PIN check failed. The failure has already been counted and audited."""

    def __init__(self, reason: str) -> None:
        super().__init__("PIN not accepted")
        self.reason = reason
