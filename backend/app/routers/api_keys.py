import uuid

from fastapi import APIRouter

from app.deps import DbDep, PrincipalDep, require_role
from app.errors import ForbiddenError
from app.schemas import ApiKeyCreatedOut, ApiKeyCreateIn, ApiKeyOut
from app.services import accounts

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyOut])
def list_api_keys(db: DbDep, principal: PrincipalDep):
    require_role(principal, "admin")
    return accounts.list_api_keys(db, principal.tenant_id)


@router.post("", response_model=ApiKeyCreatedOut)
def create_api_key(body: ApiKeyCreateIn, db: DbDep, principal: PrincipalDep):
    require_role(principal, "owner")
    if principal.kind == "api_key":
        raise ForbiddenError("API keys cannot issue other API keys.")

    key, secret = accounts.issue_api_key(
        db,
        principal.tenant_id,
        name=body.name,
        role=body.role,
        site_id=body.site_id,
        created_by_user_id=principal.user_id,
        expires_at=body.expires_at,
    )
    return ApiKeyCreatedOut(key=ApiKeyOut.model_validate(key), secret=secret)


@router.delete("/{key_id}", response_model=ApiKeyOut)
def revoke_api_key(key_id: uuid.UUID, db: DbDep, principal: PrincipalDep):
    require_role(principal, "owner")
    return accounts.revoke_api_key(db, principal.tenant_id, key_id)
