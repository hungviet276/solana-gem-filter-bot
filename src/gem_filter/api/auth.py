from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gem_filter.infrastructure.database import UserAccount

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    actual_salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=actual_salt, n=2**14, r=8, p=1, dklen=32)
    return actual_salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(subject: str, role: str, secret: str, ttl_seconds: int = 3600) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": subject,
                "role": role,
                "iat": int(time.time()),
                "exp": int(time.time()) + ttl_seconds,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str) -> dict[str, object]:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(_decode(signature), expected):
            raise ValueError("invalid signature")
        claims = json.loads(_decode(payload))
        if int(claims["exp"]) < int(time.time()):
            raise ValueError("expired")
        return claims
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token"
        ) from exc


@dataclass(frozen=True, slots=True)
class UserContext:
    id: str
    email: str
    role: str


async def get_session(request: Request):
    async with request.app.state.sessions() as session:
        yield session


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserContext:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    claims = decode_token(
        credentials.credentials, request.app.state.settings.auth_signing_secret.get_secret_value()
    )
    account = await session.scalar(
        select(UserAccount).where(
            UserAccount.email == str(claims["sub"]), UserAccount.active.is_(True)
        )
    )
    if account is None or account.role != claims.get("role"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled or role changed"
        )
    return UserContext(id=str(account.id), email=account.email, role=account.role)


def require_roles(*roles: str) -> Callable[..., UserContext]:
    async def dependency(user: Annotated[UserContext, Depends(current_user)]) -> UserContext:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency
