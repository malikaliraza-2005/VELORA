"""The webhook recorder for the OpenWA trial (P0-E1). Throwaway test tooling, not product code.

It receives OpenWA's webhooks and writes each one, exactly as it arrived, to
`evidence/webhooks-<UTC date>.jsonl`: arrival time, headers, raw body, whether the HMAC
signature verified, and what the recorder answered. For the retry tests (G1) it can be told
to answer 200 (`ok`), 500 (`error`) or to hang past OpenWA's 10-second timeout (`hang`);
"down" is `docker compose stop recorder`.

    webhook:  POST http://recorder:8080/webhook/<session>   (from OpenWA only)
    control:  GET/POST http://recorder:8090/mode[/ok|error|hang]   (from `trial.py mode`)

Standard library only. The mode is kept in `evidence/recorder-mode.txt`, so a restart keeps it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

MODES = ("ok", "error", "hang")
HANG_SECONDS = 15  # past OpenWA's 10-second delivery timeout
SIGNATURE_HEADER = "X-OpenWA-Signature"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool | None:
    """True or False against `sha256=<hex>`; None when there is no secret or no header."""
    if not secret or not header:
        return None
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


class Recorder:
    def __init__(self, evidence: Path, secret: str) -> None:
        self.evidence = evidence
        self.secret = secret
        self._lock = threading.Lock()
        evidence.mkdir(parents=True, exist_ok=True)

    @property
    def mode_file(self) -> Path:
        return self.evidence / "recorder-mode.txt"

    def mode(self) -> str:
        try:
            value = self.mode_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "ok"
        return value if value in MODES else "ok"

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.mode_file.write_text(mode + "\n", encoding="utf-8")

    def record(
        self, path: str, headers: dict[str, str], body: bytes, mode: str, answered: int | str
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        signature = next(
            (v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER.lower()), None
        )
        try:
            text: str | None = body.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        entry: dict[str, object] = {
            "received_at": now.isoformat(timespec="milliseconds"),
            "monotonic": time.monotonic(),
            "path": path,
            "headers": headers,
            "body": text,
            "body_base64": None if text is not None else base64.b64encode(body).decode(),
            "signature_valid": verify_signature(self.secret, body, signature),
            "mode": mode,
            "answered": answered,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with (
            self._lock,
            open(self.evidence / f"webhooks-{now:%Y%m%d}.jsonl", "a", encoding="utf-8") as f,
        ):
            f.write(line + "\n")
        return entry


def webhook_handler(recorder: Recorder) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the http.server API
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            mode = recorder.mode()
            answered: int | str = {"ok": 200, "error": 500}.get(mode, "hung up")
            recorder.record(self.path, dict(self.headers.items()), body, mode, answered)
            if mode == "hang":
                time.sleep(HANG_SECONDS)
                return  # no response at all: OpenWA times out
            self.send_response(int(answered))
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    return Handler


def control_handler(recorder: Recorder) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, status: int, text: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((text + "\n").encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/mode":
                self._answer(200, recorder.mode())
            else:
                self._answer(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            parts = self.path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "mode":
                try:
                    recorder.set_mode(parts[1])
                except ValueError as exc:
                    self._answer(400, str(exc))
                    return
                self._answer(200, parts[1])
            else:
                self._answer(404, "not found")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    return Handler


def main() -> None:
    evidence = Path(os.environ.get("TRIAL_EVIDENCE_DIR", "/evidence"))
    recorder = Recorder(evidence, os.environ.get("WEBHOOK_SECRET", ""))
    hooks = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), webhook_handler(recorder))  # noqa: S104
    control = http.server.ThreadingHTTPServer(("0.0.0.0", 8090), control_handler(recorder))  # noqa: S104
    threading.Thread(target=control.serve_forever, daemon=True).start()
    print(f"recorder ready (mode {recorder.mode()}), writing to {evidence}", flush=True)
    try:
        hooks.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
