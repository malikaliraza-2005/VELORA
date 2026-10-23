"""The external port scan (P12-T1): which TCP ports a server accepts connections on.

Run it from outside the server (your own computer), so the result is what the internet
sees: the cloud firewall, the host firewall and Docker's published ports together. A TCP
connect scan with the standard library, so it needs nothing installed. UDP isn't scanned;
nothing VELORA runs listens on UDP, and `nmap -sU` can confirm it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanResult:
    host: str
    scanned: int
    open: tuple[int, ...]
    expected: tuple[int, ...]

    @property
    def unexpected(self) -> tuple[int, ...]:
        return tuple(p for p in self.open if p not in self.expected)

    @property
    def missing(self) -> tuple[int, ...]:
        return tuple(p for p in self.expected if p not in self.open)

    @property
    def matches(self) -> bool:
        return not self.unexpected and not self.missing


def parse_ports(text: str) -> list[int]:
    """`1-1024,8080` -> the ports, each once, in order."""
    ports: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        low, sep, high = part.partition("-")
        try:
            start = int(low)
            end = int(high) if sep else start
        except ValueError:
            raise ValueError(f"not a port or range: {part!r}") from None
        if not 1 <= start <= end <= 65535:
            raise ValueError(f"not a port or range: {part!r}")
        ports.update(range(start, end + 1))
    if not ports:
        raise ValueError("no ports to scan")
    return sorted(ports)


async def _is_open(host: str, port: int, timeout: float, limit: asyncio.Semaphore) -> bool:
    async with limit:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return True


async def scan(
    host: str,
    ports: Iterable[int],
    expected: Iterable[int],
    *,
    timeout: float = 1.5,
    concurrency: int = 300,
) -> ScanResult:
    targets = list(ports)
    limit = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(_is_open(host, p, timeout, limit) for p in targets))
    return ScanResult(
        host=host,
        scanned=len(targets),
        open=tuple(sorted(p for p, is_open in zip(targets, results, strict=True) if is_open)),
        expected=tuple(sorted(set(expected))),
    )
