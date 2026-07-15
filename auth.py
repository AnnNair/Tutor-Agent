"""
Password hashing and session token generation. Uses hashlib's built-in PBKDF2-HMAC
(no new dependency like bcrypt needed -- Python's standard library implementation
is a legitimate, widely-used choice) and cryptographically random session tokens.
"""
import hashlib
import os
import secrets

PBKDF2_ITERATIONS = 260_000  # OWASP's 2023+ minimum recommendation for PBKDF2-SHA256


def hash_password(password: str) -> tuple[str, str]:
    """Returns (hash_hex, salt_hex)."""
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hashed.hex(), salt


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return secrets.compare_digest(candidate.hex(), hash_hex)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)
