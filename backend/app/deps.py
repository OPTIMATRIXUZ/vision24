import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.errors import (
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
)
from app.i18n import LOCALE_COOKIE, resolve_locale
from app.models import ApiKey, Site, Tenant
from app.scoping import resolve_site
from app.security import (
    ACCESS_COOKIE,
    API_KEY_PREFIX,
    TokenError,
    decode_access_token,
    hash_token,
    parse_api_key,
    tokens_equal,
)

log = logging.getLogger(__name__)

DEPRECATION_HEADER = "X-Auth-Deprecated"
DEPRECATION_NOTICE = "legacy static token; migrate to /api/auth/login"

_DUMMY_KEY_HASH = hash_token("no-such-key")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class Principal:

    tenant_id: uuid.UUID
    role: str
    kind: str
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None


def _from_jwt(token: str) -> Principal:
    if not settings.auth_secret_key:
        log.error(
            "A signed token was presented but AUTH_SECRET_KEY is not set; "
            "no token can be verified. Generate one and restart."
        )
        raise UnauthenticatedError("Token authentication is not configured on this server.")
    try:
        claims = decode_access_token(token)
    except TokenError as exc:
        raise UnauthenticatedError("Your session has expired or is not valid.") from exc
    return Principal(
        tenant_id=claims.tenant_id,
        role=claims.role,
        kind="user",
        user_id=claims.user_id,
        session_id=claims.session_id,
    )


def _from_api_key(db: Session, raw: str) -> Principal:
    parsed = parse_api_key(raw)
    if parsed is None:
        raise UnauthenticatedError("Invalid credentials.")
    prefix, secret = parsed

    key = db.scalars(select(ApiKey).where(ApiKey.prefix == prefix)).first()
    expected = key.key_hash if key is not None else _DUMMY_KEY_HASH
    matches = tokens_equal(hash_token(secret), expected)
    if key is None or not matches:
        raise UnauthenticatedError("Invalid credentials.")

    now = datetime.now(UTC)
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        raise UnauthenticatedError("This API key is no longer valid.")

    return Principal(
        tenant_id=key.tenant_id,
        role=key.role,
        kind="api_key",
        api_key_id=key.id,
        site_id=key.site_id,
    )


def _from_authorization(db: Session, header: str) -> Principal:
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise UnauthenticatedError("Expected an `Authorization: Bearer <token>` header.")

    if token.startswith(API_KEY_PREFIX):
        return _from_api_key(db, token)
    if token.count(".") == 2:
        return _from_jwt(token)
    raise UnauthenticatedError("Invalid credentials.")


def _demo_principal(db: Session) -> Principal:
    tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "demo"))
    if tenant_id is None:
        tenant_id = db.scalar(
            select(Tenant.id).order_by(Tenant.created_at, Tenant.id).limit(1)
        )
    if tenant_id is None:
        raise UnavailableError(
            "No tenant exists yet — run `alembic upgrade head` then `python -m app.seed`."
        )
    return Principal(tenant_id=tenant_id, role="owner", kind="user")


def get_principal(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        try:
            return _from_jwt(cookie)
        except UnauthenticatedError:
            pass

    if authorization:
        principal = _from_authorization(db, authorization)
        if principal.kind == "legacy":
            response.headers[DEPRECATION_HEADER] = DEPRECATION_NOTICE
        return principal

    return _demo_principal(db)


DbDep = Annotated[Session, Depends(get_db)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_current_site(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(get_principal)],
    site_id: uuid.UUID | None = None,
) -> Site:
    requested = site_id
    if principal.site_id is not None:
        if requested is not None and requested != principal.site_id:
            raise NotFoundError("Site not found.")
        requested = principal.site_id
    return resolve_site(db, principal.tenant_id, requested)


SiteDep = Annotated[Site, Depends(get_current_site)]


def get_locale(
    request: Request,
    accept_language: Annotated[str | None, Header()] = None,
) -> str:
    return resolve_locale(request.cookies.get(LOCALE_COOKIE), accept_language)


LocaleDep = Annotated[str, Depends(get_locale)]


_RANK = {"viewer": 0, "admin": 1, "owner": 2}


def require_role(principal: Principal, minimum: str) -> None:
    if _RANK.get(principal.role, -1) < _RANK[minimum]:
        raise ForbiddenError(f"This action requires the {minimum} role.")


def require_admin(principal: PrincipalDep) -> Principal:
    require_role(principal, "admin")
    return principal


def require_owner(principal: PrincipalDep) -> Principal:
    require_role(principal, "owner")
    return principal


def check_reset_config() -> None:
    if settings.allow_reset and settings.environment == "prod":
        log.warning(
            "ALLOW_RESET is on in production. POST /api/reset destroys every "
            "camera, zone, event, clip and stored object this tenant owns, with "
            "no undo. Set ALLOW_RESET=false unless you need it."
        )


def check_auth_config() -> None:
    if settings.auth_secret_key:
        return

    message = (
        "AUTH_SECRET_KEY is not set, so no access token can be signed and "
        "nobody can sign in. Generate one with "
        "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`."
    )
    if settings.environment == "prod":
        raise ConfigurationError(message)
    log.warning(message)
