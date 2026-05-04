from __future__ import annotations

from sqlalchemy.orm import Session

from syncarr_server.models import Client


def authenticate_client(session: Session, token: str) -> Client:
    raise NotImplementedError


def require_admin(token: str) -> None:
    raise NotImplementedError

