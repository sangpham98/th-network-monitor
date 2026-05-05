import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

from app.config import settings


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign(payload: str) -> str:
    return hmac.new(settings.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def auth_configured() -> bool:
    return bool(settings.admin_password) and settings.session_secret != "change-me"


def credentials_valid(username: str, password: str) -> bool:
    if not settings.auth_enabled:
        return True
    if not auth_configured():
        return False
    username_ok = secrets.compare_digest(username, settings.admin_username)
    password_ok = secrets.compare_digest(password, settings.admin_password)
    return username_ok and password_ok


def create_session_token(username: str) -> str:
    payload_data = {"username": username, "iat": int(time.time())}
    payload = _b64encode(json.dumps(payload_data, separators=(",", ":")).encode("utf-8"))
    signature = _sign(payload)
    return f"{payload}.{signature}"


def verify_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None

    if not hmac.compare_digest(_sign(payload), signature):
        return None

    try:
        data: dict[str, Any] = json.loads(_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None

    issued_at = int(data.get("iat", 0))
    if int(time.time()) - issued_at > settings.session_max_age_seconds:
        return None

    username = data.get("username")
    if not isinstance(username, str):
        return None
    return username


def get_current_user(request: Request) -> str | None:
    if not settings.auth_enabled:
        return settings.admin_username
    return verify_session_token(request.cookies.get(settings.session_cookie_name))


def require_auth(request: Request) -> str:
    user = get_current_user(request)
    if user:
        return user
    raise HTTPException(status_code=303, headers={"Location": "/login"})


def set_login_cookie(response, username: str):
    response.set_cookie(
        settings.session_cookie_name,
        create_session_token(username),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
    )


def clear_login_cookie(response):
    response.delete_cookie(settings.session_cookie_name)
