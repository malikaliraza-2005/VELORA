"""Backup encryption (P11-T1, AD-36).

Dumps are encrypted **to a public key** before they leave memory. The server holds only the
public key, so neither a stolen server nor the storage bucket can read a backup. The secret
key is kept offline by the founder and used only to restore.

The format follows the design of `age` (X25519, HKDF-SHA256, chunked AEAD), using standard
primitives from `cryptography`:

    header  = MAGIC || ephemeral X25519 public key (32) || salt (16)
    key     = HKDF-SHA256(X25519(ephemeral, recipient), salt,
                          info = "velora-backup-1" || ephemeral public || recipient public)
    chunk i = AES-256-GCM(key, nonce = i as 11 bytes big-endian || last flag (1 byte),
                          plaintext chunk of CHUNK bytes (the last one shorter, maybe empty),
                          associated data = header)

Each file has a fresh ephemeral key, so keys and nonces are never reused. The counter and the
"last" flag make reordered, dropped or truncated chunks fail to decrypt.
"""

from __future__ import annotations

import base64
import io
import os
from collections.abc import Iterator
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"VELORA-BACKUP-1\n"
CHUNK = 64 * 1024
TAG = 16
HEADER = len(MAGIC) + 32 + 16
PUBLIC_PREFIX = "velora-backup-public-1:"
SECRET_PREFIX = "velora-backup-secret-1:"  # noqa: S105 - a label, not a secret
_INFO = b"velora-backup-1"


class BackupCryptoError(ValueError):
    """Not a backup, the wrong key, or a damaged or altered file."""


def generate_keypair() -> tuple[str, str]:
    """(public key text, secret key text)."""
    secret = X25519PrivateKey.generate()
    return (
        PUBLIC_PREFIX + _b64(secret.public_key().public_bytes_raw()),
        SECRET_PREFIX + _b64(secret.private_bytes_raw()),
    )


def public_key_from_text(text: str) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(_key_bytes(text, PUBLIC_PREFIX))


def secret_key_from_text(text: str) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(_key_bytes(text, SECRET_PREFIX))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _key_bytes(text: str, prefix: str) -> bytes:
    text = text.strip()
    if not text.startswith(prefix):
        raise BackupCryptoError(f"not a VELORA backup key (expected {prefix}...)")
    body = text[len(prefix) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError as exc:
        raise BackupCryptoError("the key is damaged") from exc
    if len(raw) != 32:
        raise BackupCryptoError("the key is damaged")
    return raw


def _derive(shared: bytes, salt: bytes, ephemeral: bytes, recipient: bytes) -> AESGCM:
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO + ephemeral + recipient
    ).derive(shared)
    return AESGCM(key)


def _nonce(index: int, last: bool) -> bytes:
    if index >= 1 << 88:
        raise BackupCryptoError("file too large")
    return index.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


class Encryptor:
    """Encrypts a stream: `update()` returns ciphertext as it is ready, `finish()` the rest."""

    def __init__(self, recipient: X25519PublicKey) -> None:
        ephemeral = X25519PrivateKey.generate()
        eph_pub = ephemeral.public_key().public_bytes_raw()
        salt = os.urandom(16)
        self.header = MAGIC + eph_pub + salt
        self._aead = _derive(
            ephemeral.exchange(recipient), salt, eph_pub, recipient.public_bytes_raw()
        )
        self._buffer = bytearray()
        self._index = 0
        self._started = False

    def _seal(self, chunk: bytes, last: bool) -> bytes:
        sealed = self._aead.encrypt(_nonce(self._index, last), chunk, self.header)
        self._index += 1
        return sealed

    def update(self, data: bytes) -> bytes:
        out = bytearray()
        if not self._started:
            out += self.header
            self._started = True
        self._buffer += data
        # Keep at least one byte back: the last chunk must be sealed as last.
        while len(self._buffer) > CHUNK:
            out += self._seal(bytes(self._buffer[:CHUNK]), last=False)
            del self._buffer[:CHUNK]
        return bytes(out)

    def finish(self) -> bytes:
        out = self.update(b"")
        sealed = self._seal(bytes(self._buffer), last=True)
        self._buffer.clear()
        return out + sealed


def encrypt_bytes(data: bytes, recipient: X25519PublicKey) -> bytes:
    enc = Encryptor(recipient)
    return enc.update(data) + enc.finish()


def decrypt_stream(source: BinaryIO, secret: X25519PrivateKey) -> Iterator[bytes]:
    """Yield the plaintext chunk by chunk. Raises BackupCryptoError, at the latest on the
    last chunk, if anything is wrong; use the output only once the iterator has finished."""
    header = source.read(HEADER)
    if len(header) != HEADER or not header.startswith(MAGIC):
        raise BackupCryptoError("not a VELORA backup file")
    eph_pub = header[len(MAGIC) : len(MAGIC) + 32]
    salt = header[len(MAGIC) + 32 :]
    try:
        shared = secret.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    except ValueError as exc:
        raise BackupCryptoError("the backup header is damaged") from exc
    aead = _derive(shared, salt, eph_pub, secret.public_key().public_bytes_raw())
    index = 0
    current = source.read(CHUNK + TAG)
    while True:
        following = source.read(CHUNK + TAG)
        last = not following
        try:
            yield aead.decrypt(_nonce(index, last), current, header)
        except InvalidTag as exc:
            raise BackupCryptoError(
                "the backup can't be decrypted: wrong key, or the file is damaged or altered"
            ) from exc
        if last:
            return
        index += 1
        current = following


def decrypt_bytes(data: bytes, secret: X25519PrivateKey) -> bytes:
    return b"".join(decrypt_stream(io.BytesIO(data), secret))
