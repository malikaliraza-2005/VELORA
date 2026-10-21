"""Personal payment PINs (P3-T5).

A PIN is 6 digits, stored only as a salted scrypt hash. Six digits are a small space, so the
hash alone does not stop an offline attack; the online control is the lockout in
`velora.identity.staff` (5 failures lock the PIN for 15 minutes).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

PIN_LENGTH = 6
_PIN = re.compile(rf"^[0-9]{{{PIN_LENGTH}}}$")

# scrypt cost: about 16 MiB and a few tens of milliseconds per check.
_N, _R, _P = 2**14, 8, 1
_DKLEN = 32


class WeakPinError(ValueError):
    pass


def validate_new_pin(pin: str) -> None:
    if not _PIN.match(pin):
        raise WeakPinError(f"a PIN is exactly {PIN_LENGTH} digits")
    if len(set(pin)) == 1:
        raise WeakPinError("a PIN can't be one repeated digit")
    digits = [int(d) for d in pin]
    steps = {b - a for a, b in zip(digits, digits[1:], strict=False)}
    if steps in ({1}, {-1}):
        raise WeakPinError("a PIN can't be a run of consecutive digits")


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode("ascii"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_pin(pin: str) -> str:
    validate_new_pin(pin)
    salt = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(_derive(pin, salt, _N, _R, _P))}"


def verify_pin(pin: str, stored: str | None) -> bool:
    if stored is None or not _PIN.match(pin):
        return False
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _derive(pin, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except ValueError:
        return False
