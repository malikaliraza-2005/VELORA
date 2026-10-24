"""Fixtures for the retention tests (AD-38): the P7 order fixtures, so real chats and orders
fill every table retention touches."""

from __future__ import annotations

from tests.orders.conftest import (  # noqa: F401 - fixtures
    chat,
    clock,
    cluster,
    db,
    fake,
    run,
    shop,
    tenants,
)
