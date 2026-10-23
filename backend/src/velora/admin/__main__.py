"""Founder onboarding tools: `python -m velora.admin <command>` (P3-T1).

Connects as velora_platform through VELORA_PLATFORM_DATABASE_URL. Every command needs an
operator name (`--operator`, or VELORA_OPERATOR) for the audit log.

tenant-create        --slug --name --prefix   (in staging the slug must start with
                                               "test-": staging holds no merchant data, P12)
tenants              list tenants with what each still needs before going live
verification-record  --tenant --method --status [--evidence-ref] [--supersedes]
consent-record       --tenant --kind --version (--document FILE | --sha256 HEX)
                     --accepted-by [--supersedes]
channel-register     --tenant --provider --session-ref
owner-invite         --tenant --name           prints a 15-minute invite link
recovery-link        --tenant --staff-id       prints a 15-minute login link (recovery runbook)
go-live              --tenant
menu-import          --tenant --file CSV       loads the CSV into the draft menu; the owner
                                               reviews and publishes it on the dashboard
global-pause         --reason                  stop new orders for every tenant (P5)
global-resume                                  take new orders again
global-status                                  show whether the global pause is on
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path
from typing import get_args

import psycopg

from velora.admin.menu import import_menu
from velora.admin.onboarding import (
    ConsentKind,
    Onboarding,
    OnboardingError,
    OwnerInvite,
    Provider,
    VerificationMethod,
    VerificationStatus,
)
from velora.config import PLACEHOLDER, Settings
from velora.menu.csv_import import CsvImportError

# P12-T4: staging uses test numbers and test restaurants only.
STAGING_SLUG_PREFIX = "test-"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m velora.admin")
    parser.add_argument("--operator", default=os.environ.get("VELORA_OPERATOR", ""))
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("tenant-create")
    create.add_argument("--slug", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--prefix", required=True, help="order number prefix, 2-4 capitals")

    commands.add_parser("tenants")

    verification = commands.add_parser("verification-record")
    verification.add_argument("--tenant", required=True)
    verification.add_argument("--method", required=True, choices=get_args(VerificationMethod))
    verification.add_argument("--status", required=True, choices=get_args(VerificationStatus))
    verification.add_argument("--evidence-ref", help="where the evidence is kept, not the evidence")
    verification.add_argument("--supersedes", type=uuid.UUID)

    consent = commands.add_parser("consent-record")
    consent.add_argument("--tenant", required=True)
    consent.add_argument("--kind", required=True, choices=get_args(ConsentKind))
    consent.add_argument("--version", required=True)
    document = consent.add_mutually_exclusive_group(required=True)
    document.add_argument("--document", type=Path, help="the signed document, hashed locally")
    document.add_argument("--sha256")
    consent.add_argument("--accepted-by", required=True)
    consent.add_argument("--supersedes", type=uuid.UUID)

    channel = commands.add_parser("channel-register")
    channel.add_argument("--tenant", required=True)
    channel.add_argument("--provider", required=True, choices=get_args(Provider))
    channel.add_argument("--session-ref", required=True)

    owner = commands.add_parser("owner-invite")
    owner.add_argument("--tenant", required=True)
    owner.add_argument("--name", required=True)

    recovery = commands.add_parser("recovery-link")
    recovery.add_argument("--tenant", required=True)
    recovery.add_argument("--staff-id", required=True, type=uuid.UUID)

    go_live = commands.add_parser("go-live")
    go_live.add_argument("--tenant", required=True)

    menu = commands.add_parser("menu-import")
    menu.add_argument("--tenant", required=True)
    menu.add_argument("--file", required=True, type=Path)

    pause = commands.add_parser("global-pause")
    pause.add_argument("--reason", required=True)
    commands.add_parser("global-resume")
    commands.add_parser("global-status")
    return parser


def _print_link(settings: Settings, invite: OwnerInvite) -> None:
    print(f"staff id:   {invite.staff_id}")
    print(f"expires at: {invite.expires_at.isoformat(timespec='seconds')}")
    print("link (single use; pass it on privately, never in a group chat):")
    print(settings.magic_link_url(invite.token.wire()))


def _menu_import(args: argparse.Namespace, ops: Onboarding, url: str) -> None:
    tenant = ops.tenant_by_slug(args.tenant)
    data = args.file.read_bytes()
    # psycopg's async mode cannot use the Windows proactor event loop.
    loop = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    result = asyncio.run(import_menu(url, tenant, data, operator=args.operator), loop_factory=loop)
    print(f"draft menu version {result.version_no} (revision {result.revision}): ", end="")
    print(f"{result.items} items. The owner must review and publish it on the dashboard.")


def run(args: argparse.Namespace, ops: Onboarding, settings: Settings) -> None:
    if args.command == "tenant-create":
        if settings.env == "staging" and not args.slug.startswith(STAGING_SLUG_PREFIX):
            raise OnboardingError(
                f"staging is for test restaurants only: the slug must start with "
                f"{STAGING_SLUG_PREFIX!r}"
            )
        tenant = ops.create_tenant(slug=args.slug, display_name=args.name, order_prefix=args.prefix)
        print(f"created tenant {args.slug}: {tenant.value}")
    elif args.command == "global-pause":
        ops.set_global_pause(paused=True, reason=args.reason)
        print("global pause ON: no tenant takes new orders until global-resume")
    elif args.command == "global-resume":
        ops.set_global_pause(paused=False)
        print("global pause OFF")
    elif args.command == "global-status":
        paused, reason, since = ops.global_pause_status()
        line = f"global pause {'ON' if paused else 'OFF'}"
        if since is not None:
            line += f" since {since.isoformat(timespec='seconds')}"
        print(f"{line}: {reason}" if reason else line)
    elif args.command == "tenants":
        for t in ops.list_tenants():
            needs = ", ".join(t.gaps) or "nothing"
            print(f"{t.slug:24} {t.status:11} needs: {needs}")
    else:
        tenant = ops.tenant_by_slug(args.tenant)
        if args.command == "verification-record":
            record = ops.record_verification(
                tenant,
                method=args.method,
                status=args.status,
                evidence_ref=args.evidence_ref,
                supersedes=args.supersedes,
            )
            print(f"recorded owner verification {record}")
        elif args.command == "consent-record":
            sha256 = (
                hashlib.sha256(args.document.read_bytes()).hexdigest()
                if args.document
                else args.sha256
            )
            record = ops.record_consent(
                tenant,
                kind=args.kind,
                document_version=args.version,
                document_sha256=sha256,
                accepted_by_name=args.accepted_by,
                supersedes=args.supersedes,
            )
            print(f"recorded consent {record}")
        elif args.command == "channel-register":
            channel = ops.register_channel(
                tenant, provider=args.provider, session_ref=args.session_ref
            )
            print(f"registered channel {channel}")
        elif args.command == "owner-invite":
            _print_link(settings, ops.invite_owner(tenant, display_name=args.name))
        elif args.command == "recovery-link":
            _print_link(settings, ops.recovery_link(tenant, args.staff_id))
        elif args.command == "go-live":
            ops.go_live(tenant)
            print(f"{args.tenant} is live")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    url = os.environ.get("VELORA_PLATFORM_DATABASE_URL", "").strip()
    if not url or url == PLACEHOLDER:
        print("VELORA_PLATFORM_DATABASE_URL is not set", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    try:
        with psycopg.connect(url) as conn:
            ops = Onboarding(conn, operator=args.operator)
            if args.command == "menu-import":
                _menu_import(args, ops, url)
            else:
                run(args, ops, settings)
    except CsvImportError as exc:
        print(f"error: {exc}; nothing was saved", file=sys.stderr)
        for problem in exc.problems:
            where = f"line {problem.line}" if problem.line is not None else "file"
            print(f"  {where}: {problem.message}", file=sys.stderr)
        return 1
    except (OnboardingError, psycopg.errors.IntegrityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
