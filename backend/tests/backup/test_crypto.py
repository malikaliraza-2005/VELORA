"""P11-T1: backup encryption. A dump file is confirmed encrypted, and any change to it, or the
wrong key, is refused."""

from __future__ import annotations

import io

import pytest

from velora.backup.crypto import (
    CHUNK,
    HEADER,
    MAGIC,
    TAG,
    BackupCryptoError,
    Encryptor,
    decrypt_bytes,
    decrypt_stream,
    encrypt_bytes,
    generate_keypair,
    public_key_from_text,
    secret_key_from_text,
)

PUBLIC, SECRET = generate_keypair()
RECIPIENT, KEY = public_key_from_text(PUBLIC), secret_key_from_text(SECRET)


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK + 5])
def test_round_trip(size: int) -> None:
    data = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    sealed = encrypt_bytes(data, RECIPIENT)
    assert sealed.startswith(MAGIC)
    assert decrypt_bytes(sealed, KEY) == data


def test_streaming_in_small_pieces_gives_the_same_plaintext() -> None:
    data = b"PGDMP" + b"row data " * 30_000
    enc = Encryptor(RECIPIENT)
    sealed = b"".join(enc.update(data[i : i + 1000]) for i in range(0, len(data), 1000))
    sealed += enc.finish()
    assert decrypt_bytes(sealed, KEY) == data


def test_the_ciphertext_reveals_nothing_of_the_dump() -> None:
    data = b"PGDMP Karachi Grill +923001234567 " * 1000
    sealed = encrypt_bytes(data, RECIPIENT)
    for needle in (b"PGDMP", b"Karachi", b"+92300"):
        assert needle not in sealed
    # Two encryptions of the same dump differ (a fresh key each time).
    assert encrypt_bytes(data, RECIPIENT) != sealed


def test_the_wrong_key_is_refused() -> None:
    _, other = generate_keypair()
    with pytest.raises(BackupCryptoError, match="wrong key"):
        decrypt_bytes(encrypt_bytes(b"secret rows", RECIPIENT), secret_key_from_text(other))


def test_a_changed_byte_is_refused() -> None:
    sealed = bytearray(encrypt_bytes(b"x" * (2 * CHUNK), RECIPIENT))
    sealed[HEADER + 10] ^= 1
    with pytest.raises(BackupCryptoError):
        decrypt_bytes(bytes(sealed), KEY)


def test_a_truncated_file_is_refused() -> None:
    sealed = encrypt_bytes(b"x" * (2 * CHUNK + 10), RECIPIENT)
    # Drop the last chunk: the one before it wasn't sealed as the last.
    with pytest.raises(BackupCryptoError):
        decrypt_bytes(sealed[: HEADER + 2 * (CHUNK + TAG)], KEY)


def test_reordered_chunks_are_refused() -> None:
    sealed = encrypt_bytes(bytes(3 * CHUNK + 1), RECIPIENT)
    one = sealed[HEADER : HEADER + CHUNK + TAG]
    two = sealed[HEADER + CHUNK + TAG : HEADER + 2 * (CHUNK + TAG)]
    swapped = sealed[:HEADER] + two + one + sealed[HEADER + 2 * (CHUNK + TAG) :]
    with pytest.raises(BackupCryptoError):
        b"".join(decrypt_stream(io.BytesIO(swapped), KEY))


def test_a_file_that_is_not_a_backup_is_refused() -> None:
    with pytest.raises(BackupCryptoError, match="not a VELORA backup"):
        decrypt_bytes(b"PGDMP plain dump", KEY)


def test_keys_are_labelled_and_checked() -> None:
    assert PUBLIC.startswith("velora-backup-public-1:")
    assert SECRET.startswith("velora-backup-secret-1:")
    with pytest.raises(BackupCryptoError):
        public_key_from_text(SECRET)  # a secret key where the public one belongs
    with pytest.raises(BackupCryptoError):
        secret_key_from_text("velora-backup-secret-1:dG9vc2hvcnQ")
