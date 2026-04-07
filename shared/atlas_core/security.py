import hashlib
import hmac
import secrets
from typing import Optional


PBKDF2_ITERATIONS = 120000


def hash_password(password: str, salt: Optional[str] = None) -> str:
    effective_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        effective_salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return "{0}${1}".format(effective_salt, digest.hex())


def verify_password(password: str, encoded_value: str) -> bool:
    try:
        salt, expected_digest = encoded_value.split("$", 1)
    except ValueError:
        return False

    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, "{0}${1}".format(salt, expected_digest))


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def read_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return None
    return authorization_header[len(prefix) :].strip()
