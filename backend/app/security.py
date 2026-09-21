"""Password hashing and signed access tokens without third-party dependencies.

We deliberately avoid PyJWT/bcrypt so the auth layer runs on the stock
standard library. Passwords use PBKDF2-HMAC-SHA256 with a per-row random
salt; access tokens are JSON payloads signed with HMAC-SHA256. Both are
constant-time compared to avoid leaking timing information.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16

_STORAGE_PREFIX = f"pbkdf2_{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}$"


class InvalidToken(Exception):
    """Raised when a token is malformed, tampered, or expired."""


def hash_password(password: str) -> str:
    """Return a self-describing storage string for ``password``."""

    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"{_STORAGE_PREFIX}{_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of ``password`` against ``stored``."""

    try:
        algorithm, iterations_s, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if not algorithm.startswith("pbkdf2_"):
        return False
    alg = algorithm[len("pbkdf2_"):]
    try:
        iterations = int(iterations_s)
        salt = _b64d(salt_b64)
        expected = _b64d(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac(alg, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def create_access_token(
    secret: str,
    subject: str,
    role: str,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> str:
    """Create a signed, URL-safe access token for ``subject``."""

    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(secret, body)
    return f"{body}.{signature}"


def decode_access_token(secret: str, token: str) -> dict:
    """Validate ``token`` and return its claims, raising ``InvalidToken``."""

    try:
        body, signature = token.split(".", 1)
    except ValueError:
        raise InvalidToken("malformed token")
    expected = _sign(secret, body)
    if not hmac.compare_digest(expected, signature):
        raise InvalidToken("signature mismatch")
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError):
        raise InvalidToken("invalid payload")
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise InvalidToken("token expired")
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        raise InvalidToken("missing subject")
    return payload


def _sign(secret: str, body: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).digest()
    return _b64(digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)
