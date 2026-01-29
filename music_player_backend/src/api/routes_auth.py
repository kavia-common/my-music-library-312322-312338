"""
Auth endpoints:
- POST /auth/register
- POST /auth/login

The frontend expects a JSON response containing { token, token_type }.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.auth import create_access_token, hash_password, verify_password
from src.api.db import get_db_session
from src.api.models import User
from src.api.schemas import AuthLoginRequest, AuthRegisterRequest, AuthTokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=AuthTokenResponse,
    summary="Register a new user",
    description="Creates a new user and returns a JWT token.",
    operation_id="register_user",
)
def register(req: AuthRegisterRequest) -> AuthTokenResponse:
    """Register a new user with email/password."""
    email = req.email.lower().strip()

    with get_db_session() as db:
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Email is already registered.")

        user = User(
            # rely on DB default for uuid/created_at when schema is used, but here we set none and let DB fill?
            # However model requires values; we'll fetch them by round-trip if needed. Easiest: set in app.
            # The schema sets default gen_random_uuid() and now(), but SQLAlchemy won't omit by default unless nullable.
            # We'll explicitly omit by using server_default is not set in model; so set in app here.
            id=__import__("uuid").uuid4(),
            email=email,
            password_hash=hash_password(req.password),
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(user)
        try:
            db.flush()  # assign to user
        except IntegrityError:
            raise HTTPException(status_code=400, detail="Email is already registered.")

        token = create_access_token(user_id=user.id, email=user.email)
        return AuthTokenResponse(token=token, token_type="bearer")


@router.post(
    "/login",
    response_model=AuthTokenResponse,
    summary="Login",
    description="Validates credentials and returns a JWT token.",
    operation_id="login_user",
)
def login(req: AuthLoginRequest) -> AuthTokenResponse:
    """Login an existing user."""
    email = req.email.lower().strip()

    with get_db_session() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

        token = create_access_token(user_id=user.id, email=user.email)
        return AuthTokenResponse(token=token, token_type="bearer")
