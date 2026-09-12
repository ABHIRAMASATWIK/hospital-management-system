"""Authentication endpoints: login and current-user lookup."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from titan.api.auth import (
    CurrentUser,
    create_token,
    get_current_user,
    verify_password,
)
from titan.db import connection as db_connection

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: CurrentUser


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    conn = db_connection.get_db()
    row = conn.execute(
        "SELECT id, email, password_hash, full_name, role, business_id, is_active"
        " FROM users WHERE email = ?",
        (body.email.lower(),),
    ).fetchone()
    if (
        row is None
        or not row["is_active"]
        or not verify_password(body.password, row["password_hash"])
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    user = CurrentUser(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        business_id=row["business_id"],
    )
    token = create_token(user.id, user.role, user.business_id)
    return LoginResponse(access_token=token, user=user)


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user
