"""Authentication and user-management routes."""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import bootstrap_token
from app.database import get_session
from app.dependencies import current_user, require_roles
from app.models import User, UserRole
from app.schemas import AccessToken, CreateUser, Credentials, PublicUser
from app.security import create_access_token, hash_password, require_auth_secret, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])
admin_router = APIRouter(prefix="/admin", tags=["administration"])


def public_user(user: User) -> PublicUser:
    return PublicUser(id=user.id, username=user.username, role=UserRole(user.role))


@router.post("/setup", response_model=PublicUser, status_code=status.HTTP_201_CREATED)
def setup_first_administrator(
    payload: Credentials,
    x_bootstrap_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> PublicUser:
    require_auth_secret()
    expected_token = bootstrap_token()
    if not expected_token or not x_bootstrap_token or not hmac.compare_digest(expected_token, x_bootstrap_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Valid bootstrap token required.")
    if session.scalar(select(User.id).limit(1)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Initial administrator already exists.")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role=UserRole.ADMIN.value)
    session.add(user)
    session.commit()
    session.refresh(user)
    return public_user(user)


@router.post("/login", response_model=AccessToken)
def login(payload: Credentials, session: Session = Depends(get_session)) -> AccessToken:
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    return AccessToken(access_token=create_access_token(user.id))


@router.get("/me", response_model=PublicUser)
def me(user: User = Depends(current_user)) -> PublicUser:
    return public_user(user)


@admin_router.post("/users", response_model=PublicUser, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUser,
    session: Session = Depends(get_session),
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> PublicUser:
    user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role.value)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists.")
    session.refresh(user)
    return public_user(user)
