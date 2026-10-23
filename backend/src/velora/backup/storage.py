"""Off-site storage for encrypted backups (P11-T1, AD-35, AD-36).

Two backends, chosen by `VELORA_BACKUP_STORAGE`:

- `file:///path`: a directory. For tests and local drills, or a mounted volume.
- `s3://bucket/prefix`: any S3-compatible service, e.g. the free tiers of Backblaze B2 or
  Cloudflare R2 (AD-35; which one is still B5). Requests are signed with AWS Signature V4,
  using the standard library only.

Objects are only ever written whole and never overwritten (each backup has its own
timestamped prefix). Old backups are removed by the bucket's own lifecycle rule (set it in
the provider's console, e.g. delete after 30 days). The job never deletes anything, so give
it a storage key without delete permission: a stolen server then can't erase the history.
Only encrypted files reach storage.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # noqa: S405 - parses our storage provider's own answer
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
TIMEOUT_SECONDS = 120.0


class StorageError(RuntimeError):
    pass


def check_name(name: str) -> str:
    if not name or name.startswith("/") or ".." in name.split("/") or not set(name) <= _NAME_CHARS:
        raise StorageError(f"bad object name: {name!r}")
    return name


class Storage(Protocol):
    def put(self, name: str, source: Path) -> None: ...

    def get(self, name: str, dest: Path) -> None: ...

    def list(self, prefix: str = "") -> list[str]: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, name: str, source: Path) -> None:
        target = self.root / check_name(name)
        if target.exists():
            raise StorageError(f"{name} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        shutil.copyfile(source, partial)
        partial.rename(target)

    def get(self, name: str, dest: Path) -> None:
        source = self.root / check_name(name)
        if not source.is_file():
            raise StorageError(f"{name} not found")
        shutil.copyfile(source, dest)

    def list(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        found = (
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.endswith(".partial")
        )
        return sorted(n for n in found if n.startswith(prefix))


# --- S3-compatible storage, AWS Signature Version 4 ---------------------------------------


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _quote(text: str, *, safe: str) -> str:
    return urllib.parse.quote(text, safe=safe)


def sigv4_authorization(
    *,
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_sha256: str,
    at: datetime,
    region: str,
    access_key: str,
    secret_key: str,
    service: str = "s3",
) -> str:
    """The `Authorization` header value. `headers` are the extra headers to sign (lower-case
    names); host, x-amz-date and x-amz-content-sha256 must be among them."""
    amz_date = at.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    canonical_query = "&".join(
        f"{_quote(k, safe='-_.~')}={_quote(v, safe='-_.~')}" for k, v in sorted(query.items())
    )
    names = sorted(headers)
    canonical_headers = "".join(f"{n}:{' '.join(headers[n].split())}\n" for n in names)
    signed = ";".join(names)
    canonical_request = "\n".join(
        [
            method,
            _quote(path, safe="/-_.~"),
            canonical_query,
            canonical_headers,
            signed,
            payload_sha256,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class S3Settings:
    endpoint: str  # e.g. https://s3.eu-central-003.backblazeb2.com
    region: str  # e.g. eu-central-003 (B2) or auto (R2)
    bucket: str
    prefix: str
    access_key: str
    secret_key: str = field(repr=False)


class Clock(Protocol):
    def now(self) -> datetime: ...


class S3Storage:
    """Path-style requests (`endpoint/bucket/key`), which B2 and R2 both accept."""

    def __init__(self, settings: S3Settings, clock: Clock) -> None:
        parts = urllib.parse.urlsplit(settings.endpoint)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path not in {"", "/"}:
            raise StorageError("the storage endpoint must be an http(s) origin")
        self._settings = settings
        self._scheme = parts.scheme
        self._host = parts.netloc
        self._clock = clock

    def _key(self, name: str) -> str:
        prefix = self._settings.prefix.strip("/")
        return f"{prefix}/{check_name(name)}" if prefix else check_name(name)

    def _request(
        self,
        method: str,
        key: str | None,
        *,
        query: dict[str, str] | None = None,
        body: Path | None = None,
    ) -> bytes:
        query = query or {}
        path = f"/{self._settings.bucket}" + ("" if key is None else f"/{key}")
        payload = EMPTY_SHA256 if body is None else _sha256_file(body)
        at = self._clock.now()
        headers = {
            "host": self._host,
            "x-amz-content-sha256": payload,
            "x-amz-date": at.strftime("%Y%m%dT%H%M%SZ"),
        }
        auth = sigv4_authorization(
            method=method,
            path=path,
            query=query,
            headers=headers,
            payload_sha256=payload,
            at=at,
            region=self._settings.region,
            access_key=self._settings.access_key,
            secret_key=self._settings.secret_key,
        )
        url = f"{self._scheme}://{self._host}{_quote(path, safe='/-_.~')}"
        if query:
            url += "?" + urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
        data = None if body is None else body.read_bytes()
        request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            url,
            data=data,
            method=method,
            headers={
                "Authorization": auth,
                "x-amz-content-sha256": payload,
                "x-amz-date": headers["x-amz-date"],
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            raise StorageError(f"storage refused {method} ({exc.code})") from None
        except (urllib.error.URLError, OSError) as exc:
            raise StorageError(f"storage unreachable ({type(exc).__name__})") from None

    def put(self, name: str, source: Path) -> None:
        self._request("PUT", self._key(name), body=source)

    def get(self, name: str, dest: Path) -> None:
        dest.write_bytes(self._request("GET", self._key(name)))

    def list(self, prefix: str = "") -> list[str]:
        base = self._key("x")[:-1]  # the configured prefix, with its trailing slash
        names: list[str] = []
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": base + prefix}
            if token is not None:
                query["continuation-token"] = token
            root = ET.fromstring(self._request("GET", None, query=query))  # noqa: S314
            ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            for item in root.iter(f"{ns}Contents"):
                key = item.findtext(f"{ns}Key") or ""
                names.append(key[len(base) :])
            truncated = (root.findtext(f"{ns}IsTruncated") or "").lower() == "true"
            token = root.findtext(f"{ns}NextContinuationToken")
            if not truncated or not token:
                return sorted(names)
