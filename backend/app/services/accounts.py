import logging
import uuid
from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import (
    ConflictError,
    NotFoundError,
    UnauthenticatedError,
    ValidationError,
)
from app.models import ROLES, ApiKey, Site, Tenant, User, UserSession
from app.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_api_key,
    new_refresh_token,
    password_needs_rehash,
    refresh_expiry,
    verify_password,
)

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128


def normalize_email(email: str) -> str:
    try:
        result = validate_email(email.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationError(f"{email.strip()!r} is not a valid email address: {exc}") from exc
    return result.normalized.lower()


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(f"The password must be at most {MAX_PASSWORD_LENGTH} characters.")


def slugify(name: str) -> str:
    kept = [c if c.isalnum() else "-" for c in name.strip().lower()]
    slug = "".join(kept).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:60] or "tenant"


def unique_slug(db: Session, base: str) -> str:
    slug = base
    for suffix in range(1, 100):
        if db.scalar(select(Tenant.id).where(Tenant.slug == slug)) is None:
            return slug
        slug = f"{base}-{suffix}"
    return f"{base}-{uuid.uuid4().hex[:8]}"


def register_tenant(
    db: Session,
    *,
    email: str,
    password: str,
    company_name: str,
    full_name: str | None = None,
    site_name: str = "Main Site",
) -> User:
    email = normalize_email(email)
    validate_password(password)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("That email address is already registered.")

    tenant = Tenant(name=company_name.strip() or email, slug=unique_slug(db, slugify(company_name)))
    db.add(tenant)
    db.flush()
    db.add(Site(tenant_id=tenant.id, name=site_name))
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role="owner",
    )
    db.add(user)
    db.commit()
    log.info("Registered tenant %s (slug=%s) with owner %s", tenant.id, tenant.slug, email)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalars(select(User).where(User.email == normalize_email(email))).first()
    ok = verify_password(password, user.password_hash if user else None)
    if not ok or user is None:
        raise UnauthenticatedError("Incorrect email or password.")
    if not user.is_active:
        raise UnauthenticatedError("Incorrect email or password.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return user


def start_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[UserSession, str, str]:
    raw_refresh, refresh_hash = new_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=refresh_expiry(),
        user_agent=(user_agent or "")[:400] or None,
        ip=ip,
    )
    db.add(session)
    db.flush()
    access = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        session_id=session.id,
        role=user.role,
    )
    db.commit()
    return session, access, raw_refresh


def _revoke_chain(db: Session, session: UserSession) -> int:
    revoked = 0
    seen: set[uuid.UUID] = set()
    current: UserSession | None = session
    while current is not None and current.id not in seen:
        seen.add(current.id)
        if current.revoked_at is None:
            current.revoked_at = datetime.now(UTC)
            revoked += 1
        current = db.get(UserSession, current.replaced_by_id) if current.replaced_by_id else None
    return revoked


def rotate_session(
    db: Session,
    raw_refresh: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[UserSession, str, str]:
    session = db.scalars(
        select(UserSession).where(UserSession.refresh_token_hash == hash_token(raw_refresh))
    ).first()
    if session is None:
        raise UnauthenticatedError("Your session has expired. Sign in again.")

    if session.replaced_by_id is not None or session.revoked_at is not None:
        revoked = _revoke_chain(db, session)
        db.commit()
        log.warning(
            "Refresh token replay for user %s — revoked %d session(s) in the chain.",
            session.user_id,
            revoked,
        )
        raise UnauthenticatedError("Your session has expired. Sign in again.")

    if session.expires_at <= datetime.now(UTC):
        raise UnauthenticatedError("Your session has expired. Sign in again.")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        _revoke_chain(db, session)
        db.commit()
        raise UnauthenticatedError("Your session has expired. Sign in again.")

    new_session, access, new_raw = start_session(db, user, user_agent=user_agent, ip=ip)
    session.replaced_by_id = new_session.id
    session.revoked_at = datetime.now(UTC)
    session.last_used_at = datetime.now(UTC)
    db.commit()
    return new_session, access, new_raw


def end_session(db: Session, session_id: uuid.UUID | None) -> None:
    if session_id is None:
        return
    session = db.get(UserSession, session_id)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.commit()


def end_session_by_refresh(db: Session, raw_refresh: str | None) -> None:
    if not raw_refresh:
        return
    session = db.scalars(
        select(UserSession).where(UserSession.refresh_token_hash == hash_token(raw_refresh))
    ).first()
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.commit()


def list_users(db: Session, tenant_id: uuid.UUID) -> list[User]:
    return list(
        db.scalars(
            select(User).where(User.tenant_id == tenant_id).order_by(User.created_at, User.id)
        )
    )


def get_user(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.scalars(select(User).where(User.id == user_id, User.tenant_id == tenant_id)).first()
    if user is None:
        raise NotFoundError("User not found.")
    return user


def create_user(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    password: str,
    role: str = "viewer",
    full_name: str | None = None,
) -> User:
    email = normalize_email(email)
    validate_password(password)
    if role not in ROLES:
        raise ValidationError(f"Unknown role {role!r}. Expected one of {', '.join(ROLES)}.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("That email address is already registered.")

    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _count_active_owners(db: Session, tenant_id: uuid.UUID, excluding: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == tenant_id,
                User.role == "owner",
                User.is_active.is_(True),
                User.id != excluding,
            )
        )
        or 0
    )


def update_user(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    role: str | None = None,
    full_name: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> User:
    user = get_user(db, tenant_id, user_id)

    losing_owner = (role is not None and role != "owner") or is_active is False
    if user.role == "owner" and losing_owner and _count_active_owners(db, tenant_id, user.id) == 0:
        raise ConflictError("This is the last owner — promote somebody else first.")

    if role is not None:
        if role not in ROLES:
            raise ValidationError(f"Unknown role {role!r}. Expected one of {', '.join(ROLES)}.")
        user.role = role
    if full_name is not None:
        user.full_name = full_name
    if password is not None:
        validate_password(password)
        user.password_hash = hash_password(password)
    if is_active is not None:
        user.is_active = is_active

    if role is not None or is_active is False or password is not None:
        revoke_user_sessions(db, user.id)

    db.commit()
    return user


def deactivate_user(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    return update_user(db, tenant_id, user_id, is_active=False)


def revoke_user_sessions(db: Session, user_id: uuid.UUID) -> int:
    now = datetime.now(UTC)
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    ).all()
    for session in sessions:
        session.revoked_at = now
    return len(sessions)


def list_api_keys(db: Session, tenant_id: uuid.UUID) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id, ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.created_at, ApiKey.id)
        )
    )


def issue_api_key(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    role: str = "admin",
    site_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    if role not in ROLES:
        raise ValidationError(f"Unknown role {role!r}. Expected one of {', '.join(ROLES)}.")
    if site_id is not None:
        owned = db.scalar(select(Site.id).where(Site.id == site_id, Site.tenant_id == tenant_id))
        if owned is None:
            raise NotFoundError("Site not found.")

    full, prefix, key_hash = new_api_key()
    key = ApiKey(
        tenant_id=tenant_id,
        site_id=site_id,
        name=name.strip() or "API key",
        prefix=prefix,
        key_hash=key_hash,
        role=role,
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
    )
    db.add(key)
    db.commit()
    log.info("Issued API key %s (prefix=%s) for tenant %s", key.id, prefix, tenant_id)
    return key, full


def revoke_api_key(db: Session, tenant_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = db.scalars(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
    ).first()
    if key is None:
        raise NotFoundError("API key not found.")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        db.commit()
    return key


