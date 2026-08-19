import uuid

from fastapi import APIRouter

from app.deps import DbDep, PrincipalDep, require_role
from app.errors import ForbiddenError
from app.schemas import UserCreateIn, UserOut, UserUpdateIn
from app.services import accounts

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: DbDep, principal: PrincipalDep):
    require_role(principal, "admin")
    return accounts.list_users(db, principal.tenant_id)


@router.post("", response_model=UserOut)
def create_user(body: UserCreateIn, db: DbDep, principal: PrincipalDep):
    require_role(principal, "admin")
    if body.role == "owner":
        require_role(principal, "owner")
    return accounts.create_user(
        db,
        principal.tenant_id,
        email=body.email,
        password=body.password,
        role=body.role,
        full_name=body.full_name,
    )


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdateIn, db: DbDep, principal: PrincipalDep):
    is_self = principal.user_id == user_id
    changes_privilege = body.role is not None or body.is_active is not None

    if changes_privilege or not is_self:
        require_role(principal, "owner")
    if is_self and body.is_active is False:
        raise ForbiddenError("You cannot deactivate your own account.")

    return accounts.update_user(
        db,
        principal.tenant_id,
        user_id,
        role=body.role,
        full_name=body.full_name,
        is_active=body.is_active,
        password=body.password,
    )


@router.delete("/{user_id}", response_model=UserOut)
def deactivate_user(user_id: uuid.UUID, db: DbDep, principal: PrincipalDep):
    require_role(principal, "owner")
    if principal.user_id == user_id:
        raise ForbiddenError("You cannot deactivate your own account.")
    return accounts.deactivate_user(db, principal.tenant_id, user_id)
