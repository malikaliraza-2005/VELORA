"""P0-E1: the OpenWA trial kit (`trial/`, AD-39). Throwaway tooling, but it produces the
evidence for the Go, so the recorder, the masking and the container rules are tested."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

KIT = Path(__file__).resolve().parents[3] / "trial"
SECRET = "s3cret-for-the-test-only"  # noqa: S105 - a test value


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"trial_{name}", KIT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recorder_mod = _load("recorder")
trial_mod = _load("trial")


def _serve(handler: type[http.server.BaseHTTPRequestHandler]) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def recorder(tmp_path: Path) -> Iterator[tuple[Any, str, str]]:
    rec = recorder_mod.Recorder(tmp_path, SECRET)
    hooks = _serve(recorder_mod.webhook_handler(rec))
    control = _serve(recorder_mod.control_handler(rec))
    yield (
        rec,
        f"http://127.0.0.1:{hooks.server_port}",
        f"http://127.0.0.1:{control.server_port}",
    )
    hooks.shutdown()
    control.shutdown()


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers or {})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-OpenWA-Signature": f"sha256={digest}", "Content-Type": "application/json"}


def test_every_webhook_is_recorded_with_its_signature_check(
    recorder: tuple[Any, str, str],
) -> None:
    rec, hooks, _ = recorder
    body = b'{"event":"message.received","data":{"id":"ABC"}}'
    assert _post(f"{hooks}/webhook/biz1", body, _signed(body)) == 200
    assert _post(f"{hooks}/webhook/biz1", body + b" ", _signed(body)) == 200  # tampered
    assert _post(f"{hooks}/webhook/biz1", body) == 200  # unsigned
    (log,) = list(rec.evidence.glob("webhooks-*.jsonl"))
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [e["signature_valid"] for e in entries] == [True, False, None]
    assert entries[0]["body"] == body.decode() and entries[0]["path"] == "/webhook/biz1"
    headers = {k.lower(): v for k, v in entries[0]["headers"].items()}
    assert headers["x-openwa-signature"].startswith("sha256=")


def test_the_recorder_answers_by_mode(recorder: tuple[Any, str, str]) -> None:
    rec, hooks, control = recorder
    assert _post(f"{control}/mode/error", b"") == 200
    assert _post(f"{hooks}/webhook/biz1", b"{}") == 500
    assert _post(f"{control}/mode/sideways", b"") == 400
    assert rec.mode() == "error"  # unchanged by a bad request
    with urllib.request.urlopen(f"{control}/mode", timeout=5) as response:  # noqa: S310
        assert response.read().decode().strip() == "error"
    assert _post(f"{control}/mode/ok", b"") == 200
    assert _post(f"{hooks}/webhook/biz1", b"{}") == 200
    assert (rec.evidence / "recorder-mode.txt").read_text(encoding="utf-8").strip() == "ok"


def test_a_hanging_recorder_never_answers(
    recorder: tuple[Any, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, hooks, _ = recorder
    monkeypatch.setattr(recorder_mod, "HANG_SECONDS", 2)
    rec.set_mode("hang")
    request = urllib.request.Request(f"{hooks}/webhook/biz1", data=b"{}", method="POST")  # noqa: S310
    with pytest.raises((TimeoutError, urllib.error.URLError, ConnectionError)):
        urllib.request.urlopen(request, timeout=1)  # noqa: S310


def test_masking_hides_numbers_and_keeps_timestamps() -> None:
    text = json.dumps(
        {
            "from": "923001234567@c.us",
            "lid": "112233445566778@lid",
            "plus": "+923001234567",
            "local": "03001234567",
            "timestamp": 1729760000000,
            "id": "3EB0ABCD1234",
        }
    )
    masked = trial_mod.mask(text)
    for number in ("923001234567", "112233445566778", "03001234567"):
        assert number not in masked
    assert '"923*******67@c.us"' in masked and '"+92********67"' in masked
    assert "1729760000000" in masked and "3EB0ABCD1234" in masked


def test_the_kits_secrets_and_evidence_stay_out_of_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    for path in ("trial/.env", "trial/evidence/webhooks-20261024.jsonl"):
        ignored = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "check-ignore", "--no-index", "-q", path],  # noqa: S607
            cwd=KIT.parent,
            check=False,
        )
        assert ignored.returncode == 0, path


def _compose_config(tmp_path: Path) -> dict[str, Any]:
    if shutil.which("docker") is None:
        if os.environ.get("VELORA_REQUIRE_COMPOSE_TESTS") == "1":
            pytest.fail("VELORA_REQUIRE_COMPOSE_TESTS=1 but docker is not available")
        pytest.skip("docker not available")
    env = tmp_path / "trial.env"
    env.write_text(
        "OPENWA_DB_PASSWORD=x\nAPI_MASTER_KEY=y\nAPI_KEY_PEPPER=z\nWEBHOOK_SECRET=w\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603 - fixed argv
        [  # noqa: S607
            "docker",
            "compose",
            "--project-directory",
            str(KIT),
            "-f",
            str(KIT / "compose.yaml"),
            "--env-file",
            str(env),
            "--profile",
            "tool",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0 and "docker compose" in result.stderr and "not" in result.stderr:
        pytest.skip("docker compose not available")
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_openwa_and_its_dashboard_are_reachable_from_this_computer_only(tmp_path: Path) -> None:
    config = _compose_config(tmp_path)
    services = config["services"]
    published = {
        name: [(p.get("host_ip"), p["published"]) for p in svc.get("ports", [])]
        for name, svc in services.items()
        if svc.get("ports")
    }
    assert published == {"openwa": [("127.0.0.1", "2785")]}
    assert set(services["openwa-db"]["networks"]) == {"storage"}
    assert config["networks"]["storage"]["internal"] is True
    for name, svc in services.items():
        assert "@sha256:" in svc["image"], f"{name} must be pinned by digest"
        assert "no-new-privileges:true" in svc.get("security_opt", []) or name == "openwa-db"


def test_the_desk_review_baseline_is_configured(tmp_path: Path) -> None:
    env = _compose_config(tmp_path)["services"]["openwa"]["environment"]
    assert env["SIMULATE_TYPING"] == "false"  # no detection evasion (AD-24)
    assert env["MESSAGE_RETENTION_DAYS"] == "30"  # PC-7
    assert int(env["WEBHOOK_FAILURE_PAYLOAD_RETENTION_HOURS"]) > 0  # redrive possible
    assert env["API_KEY_PEPPER"] and env["API_MASTER_KEY"]  # set before first boot
    assert env["SSRF_ALLOWED_HOSTS"] == "recorder"
    assert env["DATABASE_TYPE"] == "postgres"
