"""Errors raised by the services (identity, menu, ordering). The API maps them to HTTP codes."""

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


class ValidationProblemsError(InvalidInputError):
    """Input failed several checks at once, e.g. a menu CSV. Each problem is shown to the user."""

    def __init__(self, message: str, problems: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.problems = problems
