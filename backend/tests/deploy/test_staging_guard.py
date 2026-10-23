"""P12-T4: staging holds test restaurants only, never merchant data."""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

from velora.admin.__main__ import run
from velora.admin.onboarding import OnboardingError
from velora.backup.__main__ import _read_secret
from velora.config import Settings


class RecordingOps:
    def __init__(self) -> None:
        self.created: list[str] = []

    def create_tenant(self, *, slug: str, **_: Any) -> Any:
        self.created.append(slug)
        return argparse.Namespace(value="00000000-0000-0000-0000-000000000000")


def _create(slug: str) -> argparse.Namespace:
    return argparse.Namespace(command="tenant-create", slug=slug, name="A Shop", prefix="AS")


def test_staging_refuses_a_restaurant_that_isnt_a_test_one() -> None:
    ops = RecordingOps()
    with pytest.raises(OnboardingError, match="test-"):
        run(_create("karachi-grill"), ops, Settings(env="staging", log_level="INFO"))
    run(_create("test-karachi-grill"), ops, Settings(env="staging", log_level="INFO"))
    assert ops.created == ["test-karachi-grill"]


def test_production_takes_any_slug() -> None:
    ops = RecordingOps()
    run(_create("karachi-grill"), ops, Settings(env="production", log_level="INFO"))
    assert ops.created == ["karachi-grill"]


def test_a_restore_can_read_the_secret_key_from_standard_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `velora restore` pipes the key in, so it is never copied into a container (P12).
    monkeypatch.setattr("sys.stdin", io.StringIO("velora-backup-secret-1:abc\n"))
    assert _read_secret("-") == "velora-backup-secret-1:abc\n"
