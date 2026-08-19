import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.config import settings
from app.deps import DbDep, PrincipalDep
from app.errors import UnauthenticatedError, UnavailableError
from app.models import Tenant, User
from app.schemas import LoginIn, RegisterIn, SessionOut, UserOut, WhoAmIOut
from app.security import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.services import accounts

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), (request.client.host if request.client else None)


def _session_response(db, user: User, request: Request, response: Response) -> SessionOut:
    ua, ip = _client(request)
    _session, access, refresh = accounts.start_session(db, user, user_agent=ua, ip=ip)
    set_auth_cookies(response, access, refresh)
    return SessionOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=SessionOut)
def register(body: RegisterIn, db: DbDep, request: Request, response: Response):
    if not settings.allow_public_registration:
        raise UnavailableError("Public registration is disabled on this deployment.")
    user = accounts.register_tenant(
        db,
        email=body.email,
        password=body.password,
        company_name=body.company_name,
        full_name=body.full_name,
    )
    return _session_response(db, user, request, response)


@router.post("/login", response_model=SessionOut)
def login(body: LoginIn, db: DbDep, request: Request, response: Response):
    user = accounts.authenticate(db, body.email, body.password)
    log.info("Sign-in for user %s (tenant %s)", user.id, user.tenant_id)
    return _session_response(db, user, request, response)


@router.post("/refresh", response_model=SessionOut)
def refresh(db: DbDep, request: Request, response: Response):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise UnauthenticatedError("No session to refresh.")
    ua, ip = _client(request)
    session, access, new_refresh = accounts.rotate_session(db, raw, user_agent=ua, ip=ip)
    set_auth_cookies(response, access, new_refresh)
    user = db.get(User, session.user_id)
    return SessionOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/logout")
def logout(db: DbDep, request: Request, response: Response, principal: PrincipalDep):
    accounts.end_session_by_refresh(db, request.cookies.get(REFRESH_COOKIE))
    accounts.end_session(db, principal.session_id)
    clear_auth_cookies(response)
    return {"status": "signed out"}


@router.get("/me", response_model=WhoAmIOut)
def me(db: DbDep, principal: PrincipalDep):
    user = db.get(User, principal.user_id) if principal.user_id else None
    slug = db.scalar(select(Tenant.slug).where(Tenant.id == principal.tenant_id))
    return WhoAmIOut(
        kind=principal.kind,
        tenant_id=principal.tenant_id,
        tenant_slug=slug or "",
        role=principal.role,
        user=UserOut.model_validate(user) if user else None,
    )
