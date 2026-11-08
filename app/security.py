"""Password hashing and signed bearer-token helpers."""

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, status

from app.config import auth_secret


PBKDF2_ITERATIONS = 600_000
TOKEN_TTL_SECONDS = 8 * 60 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, encoded_salt, encoded_hash = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.urlsafe_b64decode(encoded_salt), int(iterations)
        )
        return hmac.compare_digest(derived, base64.urlsafe_b64decode(encoded_hash))
    except (ValueError, TypeError):
        return False


def require_auth_secret() -> bytes:
    secret = auth_secret()
    if not secret or len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    return secret.encode("utf-8")


def create_access_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(require_auth_secret(), encoded_payload, hashlib.sha256).digest()
    return f"{encoded_payload.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def decode_access_token(token: str) -> str:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected = hmac.new(require_auth_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4)))
        if not isinstance(payload.get("sub"), str) or int(payload["exp"]) < time.time():
            raise ValueError
        return payload["sub"]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token.")


