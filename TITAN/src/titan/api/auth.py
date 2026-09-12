"""Authentication and tenancy for the dashboard API.

- Passwords: argon2id via ``argon2-cffi``.
- Sessions: stateless HS256 JWTs (claims: ``sub`` user id, ``role``,
  ``business_id``, ``exp``). No refresh tokens in the MVP — 12h expiry.
- Tenancy: :func:`get_tenant_id` is the single enforcement point. Business
  users are locked to their own ``business_id`` (a mismatching ``?business_id=``
  is a 403); a ``super_admin`` must pass ``?business_id=`` to address a tenant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from titan.api.permissions import has_permission
from titan.config import get_settings
from titan.db import connection as db_connection

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    """The authenticated principal attached to each request."""

    id: int
    email: str
    full_name: str
    role: str
    business_id: int | None = None


def hash_password(password: str) -> str:
    """Hash ``password`` with argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True if ``password`` matches ``password_hash``."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_token(user_id: int, role: str, business_id: int | None) -> str:
    """Mint a signed session JWT for the user."""
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "role": role,
        "business_id": business_id,
        "exp": datetime.now(UTC) + timedelta(minutes=settings.jwt_expiry_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and verify a session JWT; raises 401 on any failure."""
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired token."
        ) from exc


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    """Resolve the bearer token to an active user row."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    payload = decode_token(credentials.credentials)
    conn = db_connection.get_db()
    row = conn.execute(
        "SELECT id, email, full_name, role, business_id, is_active"
        " FROM users WHERE id = ?",
        (int(payload["sub"]),),
    ).fetchone()
    if row is None or not row["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        business_id=row["business_id"],
    )


def require_permission(permission: str):
    """Dependency factory: 403 unless the current user's role grants ``permission``."""

    def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=403, detail="Permission denied.")
        return user

    return _check


def get_tenant_id(
    user: CurrentUser = Depends(get_current_user),
    business_id: int | None = Query(default=None),
) -> int:
    """Resolve the business the request operates on (tenancy enforcement point)."""
    if user.role == "super_admin":
        if business_id is None:
            raise HTTPException(
                status_code=400,
                detail="super_admin must specify ?business_id= for business routes.",
            )
        return business_id
    if user.business_id is None:
        raise HTTPException(status_code=403, detail="User has no business.")
    if business_id is not None and business_id != user.business_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied.")
    return user.business_id
