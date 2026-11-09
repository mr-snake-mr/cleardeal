"""Auth routes. Thin: parse, call a service, set or clear a cookie.

Cookie choices, and why:

- `HttpOnly` — a token readable by JavaScript is a token stealable by any XSS.
- `SameSite=Lax` — the CSRF defence chosen in docs/10 §2. A double-submit token
  was rejected because the SPA has no server-rendered form to embed one in.
- `Secure` unless debug — over plain HTTP a Secure cookie is silently dropped,
  which looks like "login did nothing".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut
from app.security import issue_session, read_session
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]

COOKIE_NAME = "cleardeal_session"


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHENTICATED", "message": "Sign in to continue."},
    )


def session_user_id(
    token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> uuid.UUID:
    """Dependency for routes that need an identity but not the full row.

    Every authenticated route goes through this, so there is exactly one place
    where a token becomes a user id — and one place to audit.
    """
    user_id = read_session(token)
    if user_id is None:
        raise _unauthenticated()
    return user_id


SessionUserId = Annotated[uuid.UUID, Depends(session_user_id)]


def require_user(db: DbSession, user_id: SessionUserId) -> UserOut:
    """Depends on `session_user_id`, so a forged token never reaches this."""
    user = db.get(User, user_id)
    if user is None:
        # Valid signature, deleted account: treated as signed out rather than as
        # a 500 further down the request.
        raise _unauthenticated()
    return UserOut.model_validate(user)


CurrentUser = Annotated[UserOut, Depends(require_user)]


def _set_session(response: Response, user_id: uuid.UUID) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_session(user_id),
        max_age=3600 * settings.session_ttl_hours,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession, response: Response) -> UserOut:
    try:
        user = auth_service.register(
            db, email=payload.email, name=payload.name, password=payload.password
        )
    except auth_service.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "EMAIL_ALREADY_REGISTERED",
                "message": "That address is already registered.",
            },
        ) from None

    _set_session(response, user.id)
    return UserOut.model_validate(user)


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, db: DbSession, response: Response) -> UserOut:
    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    if user is None:
        # One message for both "no such account" and "wrong password".
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect."},
        )
    _set_session(response, user.id)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user
