"""P11-T1: off-site storage. The S3 request signing matches AWS's published example, and
the client works end to end against a local S3-compatible stand-in."""

from __future__ import annotations

import hashlib
import threading
import urllib.parse
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from velora.backup.config import BackupSettings, open_storage
from velora.backup.storage import (
    LocalStorage,
    S3Settings,
    S3Storage,
    StorageError,
    sigv4_authorization,
)
from velora.clock import FakeClock

AT = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


def test_signature_matches_the_aws_documentation_example() -> None:
    # "Example: GET Object" in the AWS Signature Version 4 documentation for S3.
    auth = sigv4_authorization(
        method="GET",
        path="/test.txt",
        query={},
        headers={
            "host": "examplebucket.s3.amazonaws.com",
            "range": "bytes=0-9",
            "x-amz-content-sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
            "x-amz-date": "20130524T000000Z",
        },
        payload_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        at=datetime(2013, 5, 24, tzinfo=UTC),
        region="us-east-1",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # noqa: S106 - AWS's sample
    )
    assert auth == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_local_storage_never_overwrites_and_lists_by_prefix(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path / "store")
    source = tmp_path / "a"
    source.write_bytes(b"one")
    store.put("20261023T010000Z/x.vbk", source)
    with pytest.raises(StorageError, match="already exists"):
        store.put("20261023T010000Z/x.vbk", source)
    out = tmp_path / "out"
    store.get("20261023T010000Z/x.vbk", out)
    assert out.read_bytes() == b"one"
    assert store.list("2026") == ["20261023T010000Z/x.vbk"]
    assert store.list("2025") == []
    for bad in ("../x", "/abs", "a b", ""):
        with pytest.raises(StorageError):
            store.put(bad, source)


class FakeS3(BaseHTTPRequestHandler):
    """A tiny S3 stand-in: checks each request's signature, keeps objects in memory, and
    pages its listings two keys at a time."""

    objects: dict[str, bytes] = {}
    secret = "test-secret"  # noqa: S105 - test credential

    def log_message(self, *args: object) -> None:
        pass

    def _check(self, body: bytes) -> bool:
        parts = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        signed = {
            "host": self.headers["Host"],
            "x-amz-content-sha256": self.headers["x-amz-content-sha256"],
            "x-amz-date": self.headers["x-amz-date"],
        }
        expected = sigv4_authorization(
            method=self.command,
            path=urllib.parse.unquote(parts.path),
            query=query,
            headers=signed,
            payload_sha256=self.headers["x-amz-content-sha256"],
            at=datetime.strptime(self.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
            region="eu-central-003",
            access_key="test-key",
            secret_key=self.secret,
        )
        if hashlib.sha256(body).hexdigest() != self.headers["x-amz-content-sha256"]:
            return False
        return self.headers["Authorization"] == expected

    def _answer(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:  # noqa: N802 - http.server naming
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not self._check(body):
            return self._answer(403)
        key = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        self.objects[key] = body
        return self._answer(200)

    def do_GET(self) -> None:  # noqa: N802
        if not self._check(b""):
            return self._answer(403)
        parts = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parts.query))
        path = urllib.parse.unquote(parts.path)
        if query.get("list-type") == "2":
            bucket = path.rstrip("/")
            keys = sorted(
                k[len(bucket) + 1 :]
                for k in self.objects
                if k.startswith(bucket + "/" + query.get("prefix", ""))
            )
            start = int(query.get("continuation-token", "0"))
            page = keys[start : start + 2]
            more = start + 2 < len(keys)
            xml = (
                '<?xml version="1.0"?><ListBucketResult '
                'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                + "".join(f"<Contents><Key>{k}</Key></Contents>" for k in page)
                + f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>"
                + (f"<NextContinuationToken>{start + 2}</NextContinuationToken>" if more else "")
                + "</ListBucketResult>"
            )
            return self._answer(200, xml.encode())
        if path not in self.objects:
            return self._answer(404)
        return self._answer(200, self.objects[path])


@pytest.fixture()
def s3() -> Iterator[str]:
    FakeS3.objects = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _store(endpoint: str, secret: str = "test-secret") -> S3Storage:  # noqa: S107
    return S3Storage(
        S3Settings(endpoint, "eu-central-003", "velora-backups", "pilot", "test-key", secret),
        FakeClock(AT),
    )


def test_s3_storage_puts_gets_and_lists_through_pages(s3: str, tmp_path: Path) -> None:
    store = _store(s3)
    for i in range(5):
        source = tmp_path / f"f{i}"
        source.write_bytes(f"object {i}".encode())
        store.put(f"20261023T01000{i}Z/manifest.json.vbk", source)
    assert sorted(FakeS3.objects)[0] == "/velora-backups/pilot/20261023T010000Z/manifest.json.vbk"
    assert store.list() == [f"20261023T01000{i}Z/manifest.json.vbk" for i in range(5)]
    assert store.list("20261023T010003Z/") == ["20261023T010003Z/manifest.json.vbk"]
    out = tmp_path / "out"
    store.get("20261023T010002Z/manifest.json.vbk", out)
    assert out.read_bytes() == b"object 2"


def test_s3_storage_reports_a_refusal_without_details(s3: str, tmp_path: Path) -> None:
    source = tmp_path / "f"
    source.write_bytes(b"x")
    with pytest.raises(StorageError, match=r"storage refused PUT \(403\)"):
        _store(s3, secret="wrong").put("a/b.vbk", source)  # noqa: S106


def test_storage_settings() -> None:
    clock = FakeClock(AT)
    assert isinstance(open_storage("file:///srv/backups", clock), LocalStorage)
    with pytest.raises(StorageError, match="s3 storage needs"):
        open_storage("s3://bucket/prefix", clock)
    with pytest.raises(StorageError, match="must be file"):
        open_storage("ftp://x", clock)
    with pytest.raises(ValueError, match="https"):
        BackupSettings.from_env(
            {"VELORA_ENV": "production", "VELORA_BACKUP_S3_ENDPOINT": "http://s3.example"}
        )
    settings = BackupSettings.from_env(
        {
            "VELORA_BACKUP_PG_TOOLS": "docker exec -i -e PGPASSWORD velora-pg",
            "VELORA_BACKUP_PG_TOOLS_ADDRESS": "127.0.0.1:5432",
        }
    )
    argv, env = settings.tools.command(
        "pg_dump", "postgresql://velora_backup:pw@db.example:6543/velora", "--dbname", "velora"
    )
    assert argv[:6] == ["docker", "exec", "-i", "-e", "PGPASSWORD", "velora-pg"]
    assert argv[6:12] == ["pg_dump", "--host", "127.0.0.1", "--port", "5432", "--username"]
    assert "pw" not in " ".join(argv)  # the password goes in the environment only
    assert env["PGPASSWORD"] == "pw"  # noqa: S105
