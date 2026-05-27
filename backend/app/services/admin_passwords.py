import base64
import hashlib
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_MIN_PASSWORD_LENGTH = 14


def hash_admin_password(password: str) -> str:
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(f"Admin password must be at least {_MIN_PASSWORD_LENGTH} characters.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, _ITERATIONS)
    return "$".join([
        _ALGORITHM,
        str(_ITERATIONS),
        _b64(salt),
        _b64(digest),
    ])


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (ValueError, TypeError):
        return False

    actual = _derive(password, salt, iterations)
    return secrets.compare_digest(actual, expected)


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
