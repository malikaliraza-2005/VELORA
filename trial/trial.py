"""The trial's helper commands (P0-E1). Throwaway test tooling, not product code.

Run through Docker, so nothing needs installing on the computer (see README.md):

    docker compose run --rm tool <command>

link SESSION              create the session (if new), start it, and point its webhook at the
                          recorder; then scan the QR code on the local dashboard
status                    every session and its state
mode [ok|error|hang]      show or set how the recorder answers webhooks (G1 retry tests)
messages SESSION [--minutes N]
                          OpenWA's stored messages from the last N minutes (default 60), saved to
                          evidence/: the reconciliation tests (G2)
failures [SESSION]        failed webhook deliveries, saved to evidence/ (G1)
send SESSION NUMBER TEXT [--key KEY]
                          send one text to a number that agreed to take part. --key sends it
                          with an Idempotency-Key; repeat it to test duplicates (T4.3)
test-webhook SESSION      ask OpenWA to send a test event to the recorder
logout SESSION            unlink the device and stop the session (end of the trial)
mask FILE                 a copy of an evidence file with phone numbers masked, for sharing

Standard library only. Settings come from the environment (.env, through compose).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OPENWA = os.environ.get("OPENWA_URL", "http://openwa:2785")
RECORDER = os.environ.get("RECORDER_CONTROL_URL", "http://recorder:8090")
WEBHOOK_URL = os.environ.get("RECORDER_WEBHOOK_URL", "http://recorder:8080/webhook")
EVIDENCE = Path(os.environ.get("TRIAL_EVIDENCE_DIR", "/evidence"))
# Phone numbers as they appear in OpenWA's payloads: a WhatsApp id (…@c.us, …@s.whatsapp.net,
# …@lid), a +number, or a Pakistani number (92… or 03…). Timestamps and ids are left alone.
PHONE = re.compile(r"(?<![0-9])(\+?[0-9]{9,15}(?=@)|\+[0-9]{9,15}|92[0-9]{10}|03[0-9]{9})(?![0-9])")
NUMBER = re.compile(r"^\+?[1-9][0-9]{7,14}$")


class TrialError(Exception):
    pass


def mask(text: str) -> str:
    """Keep the first three and last two characters: +923001234545 → +92********45."""
    return PHONE.sub(lambda m: m.group(0)[:3] + "*" * (len(m.group(0)) - 5) + m.group(0)[-2:], text)


def api(method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    key = os.environ.get("API_MASTER_KEY", "")
    if not key:
        raise TrialError("API_MASTER_KEY is not set: run the setup script first")
    request = urllib.request.Request(  # noqa: S310 - the trial's own OpenWA, by URL from .env
        OPENWA + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise TrialError(f"{method} {path}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise TrialError(f"OpenWA isn't answering at {OPENWA}: {exc.reason}") from None
    return json.loads(raw) if raw else None


def _items(found: Any) -> list[dict[str, Any]]:
    if isinstance(found, dict):
        for key in ("data", "items", "sessions"):
            if isinstance(found.get(key), list):
                return list(found[key])
    return list(found) if isinstance(found, list) else []


def session_id(name: str) -> str:
    for session in _items(api("GET", "/api/sessions")):
        if name in (session.get("name"), session.get("id")):
            return str(session["id"])
    raise TrialError(f"no session named {name!r}: run `link {name}` first")


def save(name: str, data: Any) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def link(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", name):
        raise TrialError("a session name is letters, digits and hyphens, e.g. biz1")
    try:
        sid = session_id(name)
        print(f"session {name} exists ({sid})")
    except TrialError:
        # A deliberate reconnect limit (desk review §10.6), not OpenWA's unlimited default.
        created = api(
            "POST",
            "/api/sessions",
            {"name": name, "config": {"maxReconnectAttempts": 10, "reconnectBaseDelay": 5000}},
        )
        sid = str(created["id"])
        print(f"created session {name} ({sid})")
    hooks = _items(api("GET", f"/api/sessions/{sid}/webhooks"))
    if not any(str(h.get("url", "")).startswith(WEBHOOK_URL) for h in hooks):
        secret = os.environ.get("WEBHOOK_SECRET", "")
        if len(secret) < 16:
            raise TrialError("WEBHOOK_SECRET is missing or shorter than 16 characters")
        api(
            "POST",
            f"/api/sessions/{sid}/webhooks",
            {"url": f"{WEBHOOK_URL}/{name}", "events": ["*"], "secret": secret, "retryCount": 5},
        )
        print("webhook registered: every event goes to the recorder, signed, up to 5 attempts")
    api("POST", f"/api/sessions/{sid}/start")
    print("started. Now open http://localhost:2785 on THIS computer, sign in with the admin key")
    print(
        "from .env (API_MASTER_KEY), open the session and scan its QR code with WhatsApp Business:"
    )
    print("  ⋮ → Linked devices → Link a device. Never share or screenshot the QR code.")


def status() -> None:
    for session in _items(api("GET", "/api/sessions")):
        print(f"{session.get('name', '?'):16} {session.get('status', '?'):16} {session.get('id')}")


def recorder_mode(mode: str | None) -> None:
    method, path = ("GET", "/mode") if mode is None else ("POST", f"/mode/{mode}")
    request = urllib.request.Request(RECORDER + path, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            print(f"recorder mode: {response.read().decode().strip()}")
    except urllib.error.HTTPError as exc:
        raise TrialError(exc.read().decode().strip()) from None
    except urllib.error.URLError:
        raise TrialError("the recorder is down (that is the 'down' mode)") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trial")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("link").add_argument("session")
    commands.add_parser("status")
    commands.add_parser("mode").add_argument("mode", nargs="?", choices=["ok", "error", "hang"])
    messages = commands.add_parser("messages")
    messages.add_argument("session")
    messages.add_argument("--minutes", type=int, default=60)
    commands.add_parser("failures").add_argument("session", nargs="?")
    send = commands.add_parser("send")
    send.add_argument("session")
    send.add_argument("number")
    send.add_argument("text")
    send.add_argument("--key")
    commands.add_parser("test-webhook").add_argument("session")
    commands.add_parser("logout").add_argument("session")
    commands.add_parser("mask").add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "link":
            link(args.session)
        elif args.command == "status":
            status()
        elif args.command == "mode":
            recorder_mode(args.mode)
        elif args.command == "messages":
            sid = session_id(args.session)
            since = int((time.time() - args.minutes * 60) * 1000)
            found = api("GET", f"/api/sessions/{sid}/messages?since={since}&limit=500")
            print(
                f"{len(_items(found))} messages saved to {save(f'messages-{args.session}', found)}"
            )
        elif args.command == "failures":
            query = "" if args.session is None else f"?sessionId={session_id(args.session)}"
            found = api("GET", f"/api/webhooks/delivery-failures{query}")
            print(f"{len(_items(found))} failures saved to {save('delivery-failures', found)}")
        elif args.command == "send":
            if not NUMBER.match(args.number):
                raise TrialError("the number is digits with the country code, e.g. 923001234567")
            chat = args.number.lstrip("+") + "@c.us"
            headers = {} if args.key is None else {"Idempotency-Key": args.key}
            sent = api(
                "POST",
                f"/api/sessions/{session_id(args.session)}/messages/send-text",
                {"chatId": chat, "text": args.text},
                headers,
            )
            print(f"accepted (not yet delivered): {json.dumps(sent)}")
        elif args.command == "test-webhook":
            sid = session_id(args.session)
            for hook in _items(api("GET", f"/api/sessions/{sid}/webhooks")):
                api("POST", f"/api/sessions/{sid}/webhooks/{hook['id']}/test")
                print(f"test event sent to {hook.get('url')}")
        elif args.command == "logout":
            api("POST", f"/api/sessions/{session_id(args.session)}/logout")
            print("logged out. On the phone, check Linked devices: the device must be gone.")
        else:
            source: Path = args.file
            target = source.with_name(source.stem + "-masked" + source.suffix)
            target.write_text(mask(source.read_text(encoding="utf-8")), encoding="utf-8")
            print(f"masked copy: {target}")
    except TrialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
