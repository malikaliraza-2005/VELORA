"""P12-T1: the external port scan finds every open port and fails on any difference."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Iterator

import pytest

from velora.deploy.__main__ import main
from velora.deploy.scan import parse_ports, scan


@pytest.fixture
def listeners() -> Iterator[list[int]]:
    sockets = []
    for _ in range(2):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        sockets.append(s)
    yield [s.getsockname()[1] for s in sockets]
    for s in sockets:
        s.close()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def test_open_ports_are_found_and_compared(listeners: list[int]) -> None:
    closed = _closed_port()
    ports = [*listeners, closed]
    result = asyncio.run(scan("127.0.0.1", ports, [listeners[0]], timeout=1))
    assert result.open == tuple(sorted(listeners))
    assert result.unexpected == (listeners[1],)
    assert not result.matches
    exact = asyncio.run(scan("127.0.0.1", ports, listeners, timeout=1))
    assert exact.matches


def test_an_expected_port_that_is_closed_fails() -> None:
    closed = _closed_port()
    result = asyncio.run(scan("127.0.0.1", [closed], [closed], timeout=1))
    assert result.missing == (closed,)
    assert not result.matches


def test_port_lists() -> None:
    assert parse_ports("443") == [443]
    assert parse_ports("20-22, 443,22") == [20, 21, 22, 443]
    for bad in ("", "0", "70000", "22-20", "ssh"):
        with pytest.raises(ValueError):
            parse_ports(bad)


def test_command_line(listeners: list[int], capsys: pytest.CaptureFixture[str]) -> None:
    ports = ",".join(map(str, listeners))
    expect = [arg for port in listeners for arg in ("--expect", str(port))]
    assert main(["scan", "127.0.0.1", "--ports", ports, *expect, "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["matches"] is True and record["open"] == sorted(listeners)
    assert main(["scan", "127.0.0.1", "--ports", ports, "--expect", "443"]) == 1
    out = capsys.readouterr().out
    assert "UNEXPECTED open ports" in out and "expected but closed: 443" in out
    assert out.rstrip().endswith("FAIL")
