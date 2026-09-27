"""Passwords, session tokens and the two access errors every route raises."""

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, User, utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    # Session tokens are random and high entropy, so a fast hash is enough.
    # Storing only the hash means a database leak does not leak live sessions.
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, *, label: str = "login",
                   token: str | None = None, days: int | None = None) -> str:
    token = token or new_token()
    db.add(AuthSession(
        token_hash=hash_token(token),
        user_id=user.id,
        label=label,
        expires_at=utcnow() + timedelta(days=days or settings.session_days),
    ))
    return token


class NotAuthenticated(Exception):
    """No valid session. API routes answer 401, pages redirect to /login."""


class Forbidden(Exception):
    """Logged in, but not allowed. Always a 403 from the backend.

    `code` is machine-readable. When `audit` is true the refusal is written to
    the audit log, which is how an organizer sees someone probing for scores.
    """

    def __init__(self, code: str, message: str, *, audit: bool = False,
                 event_id: int | None = None, actor_id: int | None = None,
                 detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.audit = audit
        self.event_id = event_id
        self.actor_id = actor_id
        self.detail = detail or {}
