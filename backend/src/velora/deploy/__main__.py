"""Deployment helpers: `python -m velora.deploy <command>` (P12).

On the server these run inside the application image, through `infra/bin/velora`:

env-init --dir DIR --env staging|production --domain HOST [--tls acme|internal]
         [--alert-email ADDR] [--alert-webhook URL]
        write the settings files with fresh random passwords (refuses to overwrite)
env-check --dir DIR
        verify the settings files before a release; exit 1 listing every problem

From your own computer, against the server's public address:

scan HOST [--expect 443] [--ports 1-65535] [--timeout 1.5] [--concurrency 300]
        TCP connect scan; exit 1 unless the open ports are exactly the expected ones
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from velora.deploy.envfiles import StackChoices, check, initial_files, write_initial
from velora.deploy.scan import parse_ports, scan

# P12-T1: the internet sees HTTPS only. SSH is open to the founder's own addresses, so a scan
# from one of them also expects 22 (`--expect 443 --expect 22`).
DEFAULT_EXPECTED = (443,)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m velora.deploy")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("env-init")
    init.add_argument("--dir", type=Path, required=True)
    init.add_argument("--env", choices=["staging", "production"], required=True)
    init.add_argument("--domain", required=True)
    init.add_argument("--tls", choices=["acme", "internal"], default="acme")
    init.add_argument("--alert-email")
    init.add_argument("--alert-webhook")
    env_check = commands.add_parser("env-check")
    env_check.add_argument("--dir", type=Path, required=True)
    port_scan = commands.add_parser("scan")
    port_scan.add_argument("host")
    port_scan.add_argument("--expect", type=int, action="append")
    port_scan.add_argument("--ports", default="1-65535")
    port_scan.add_argument("--timeout", type=float, default=1.5)
    port_scan.add_argument("--concurrency", type=int, default=300)
    port_scan.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "env-init":
        try:
            files = initial_files(
                StackChoices(
                    env=args.env,
                    domain=args.domain.strip().lower(),
                    tls=args.tls,
                    alert_email=args.alert_email,
                    alert_webhook=args.alert_webhook,
                )
            )
            written = write_initial(args.dir, files)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for path in written:
            print(f"wrote {path}")
        print("Next: fill in the CHANGE_ME values, then run env-check.")
        return 0
    if args.command == "env-check":
        problems = check(args.dir)
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"{args.dir}: settings ready")
        return 0
    try:
        ports = parse_ports(args.ports)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started = datetime.now(UTC)
    result = asyncio.run(
        scan(
            args.host,
            ports,
            args.expect or DEFAULT_EXPECTED,
            timeout=args.timeout,
            concurrency=args.concurrency,
        )
    )
    if args.json:
        print(
            json.dumps(
                {
                    "host": result.host,
                    "started_at": started.isoformat(timespec="seconds"),
                    "scanned": result.scanned,
                    "open": result.open,
                    "expected": result.expected,
                    "matches": result.matches,
                }
            )
        )
    else:
        print(f"{result.host}: scanned {result.scanned} TCP ports at {started:%Y-%m-%d %H:%M}Z")
        print(f"open:     {', '.join(map(str, result.open)) or 'none'}")
        print(f"expected: {', '.join(map(str, result.expected))}")
        if result.unexpected:
            print(f"UNEXPECTED open ports: {', '.join(map(str, result.unexpected))}")
        if result.missing:
            print(f"expected but closed: {', '.join(map(str, result.missing))}")
        print("PASS" if result.matches else "FAIL")
    return 0 if result.matches else 1


if __name__ == "__main__":
    sys.exit(main())
