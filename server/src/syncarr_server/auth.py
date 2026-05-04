from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.config import Settings, get_settings
from syncarr_server.db import get_session
from syncarr_server.models import Client

bearer_scheme = HTTPBearer(auto_error=False)


def create_agent_token(client_id: str) -> str:
    return f"agent-{client_id}-{secrets.token_urlsafe(24)}"


def agent_bearer_token(client_id: str) -> str:
    return f"Bearer {create_agent_token(client_id)}"


def _extract_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return credentials.credentials


def _ui_token(settings: Settings) -> str:
    return settings.ui_token


async def authenticate_client(session: AsyncSession, token: str) -> Client:
    result = await session.execute(select(Client).where(Client.auth_token == token))
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )
    return client


async def require_agent_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Client:
    token = _extract_bearer_token(credentials)
    return await authenticate_client(session, token)


def require_ui_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    token = _extract_bearer_token(credentials)
    ui_token = _ui_token(settings)
    if token != ui_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid UI token",
        )


def require_admin(token: str) -> None:
    settings = get_settings()
    if token != _ui_token(settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid UI token",
        )
