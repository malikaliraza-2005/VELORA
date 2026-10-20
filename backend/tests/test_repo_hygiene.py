"""P1 repository hygiene checks (tasks P1-T1 and P1-T3).

- `.env.example` may contain only placeholders, never real values.
- `.gitignore` must exclude env files, WhatsApp session data and database dumps.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Values allowed in .env.example: empty, an explicit placeholder, or harmless non-secret
# settings that are documented in the example itself.
ALLOWED_NON_SECRET = {
    "VELORA_ENV": {"development"},
    "VELORA_LOG_LEVEL": {"INFO"},
}
PLACEHOLDERS = {"", "CHANGE_ME"}


def _env_example_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep, f"malformed line in .env.example: {raw!r}"
        entries[key.strip()] = value.strip()
    return entries


def test_env_example_contains_only_placeholders() -> None:
    entries = _env_example_entries()
    assert entries, ".env.example must document at least one variable"
    for key, value in entries.items():
        allowed = PLACEHOLDERS | ALLOWED_NON_SECRET.get(key, set())
        assert value in allowed, f"{key} in .env.example must be a placeholder, got {value!r}"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "backend/.env",
        "sessions/session-biz1/Default/Cookies",
        "openwa-data/baileys/biz1/creds.json",
        ".wwebjs_auth/session/data",
        "dumps/velora-2026-10-21.dump",
        "backups/hourly.sql.gz",
        "velora.pgdump",
        "trial/evidence/T1.1.json",
    ],
)
def test_sensitive_paths_are_ignored(path: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "check-ignore", "--no-index", "-q", path],  # noqa: S607
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{path} must be ignored by .gitignore"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_env_example_is_not_ignored() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "check-ignore", "--no-index", "-q", ".env.example"],  # noqa: S607
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 1, ".env.example must stay tracked"
