import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings
from app.errors import ConfigurationError

ACCESS_COOKIE = "v24_access"
REFRESH_COOKIE = "v24_refresh"
REFRESH_COOKIE_PATH = "/api/auth"

API_KEY_PREFIX = "v24k_"
_PREFIX_LEN = 8
_ALGORITHM = "HS256"

_hasher = PasswordHasher()

_DUMMY_HASH = _hasher.hash("timing-equalizer")


class TokenError(Exception):
    pass


MIN_SECRET_BYTES = 32

_GENERATE_HINT = 'python -c "import secrets; print(secrets.token_urlsafe(48))"'


def require_auth_secret() -> str:
    secret = settings.auth_secret_key
    if not secret:
        raise ConfigurationError(
            f"AUTH_SECRET_KEY is not set — generate one with `{_GENERATE_HINT}`."
        )
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise ConfigurationError(
            f"AUTH_SECRET_KEY is too short for HS256 ({len(secret.encode())} bytes, "
            f"minimum {MIN_SECRET_BYTES}) — generate one with `{_GENERATE_HINT}`."
        )
    return secret


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    try:
        _hasher.verify(hashed or _DUMMY_HASH, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return bool(hashed)


def password_needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    role: str


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "sid": str(session_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, require_auth_secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            require_auth_secret(),
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub", "tid", "sid"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("typ") != "access":
        raise TokenError("not an access token")
    try:
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            session_id=uuid.UUID(payload["sid"]),
            role=payload.get("role", "viewer"),
        )
    except (ValueError, KeyError) as exc:
        raise TokenError("malformed claims") from exc


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)


def new_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(_PREFIX_LEN // 2)
    secret = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}{prefix}_{secret}"
    return full, prefix, hash_token(secret)


def parse_api_key(value: str) -> tuple[str, str] | None:
    if not value.startswith(API_KEY_PREFIX):
        return None
    rest = value[len(API_KEY_PREFIX) :]
    prefix, _, secret = rest.partition("_")
    if not prefix or not secret:
        return None
    return prefix, secret


def set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain or None,
        path=REFRESH_COOKIE_PATH,
    )


def clear_auth_cookies(response) -> None:
    response.delete_cookie(ACCESS_COOKIE, domain=settings.cookie_domain or None, path="/")
    response.delete_cookie(
        REFRESH_COOKIE, domain=settings.cookie_domain or None, path=REFRESH_COOKIE_PATH
    )
