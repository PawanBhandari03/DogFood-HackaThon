"""Who is asking, and what they may do. Every access decision lives here.

Routes never check roles by reading templates or flags on the page: they call
`require_*` from this module, and queries for judge data are always filtered
by the caller's own user id (see services/scoring.py).
"""

from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE
from app.db import get_db
from app.models import AuthSession, Event, EventRole, Role, TeamMember, User, utcnow
from app.security import Forbidden, NotAuthenticated, hash_token


@dataclass
class Viewer:
    """The person making the request. `user` is None for a visitor."""

    user: User | None
    _roles: dict[int, set[str]] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.is_admin)

    def roles(self, event: Event | int) -> set[str]:
        event_id = event if isinstance(event, int) else event.id
        return self._roles.get(event_id, set())

    def has(self, event: Event | int, role: str) -> bool:
        return role in self.roles(event)

    def is_organizer(self, event: Event | int) -> bool:
        return self.is_admin or self.has(event, Role.ORGANIZER)

    def is_judge(self, event: Event | int) -> bool:
        return self.has(event, Role.JUDGE)

    @property
    def judge_event_ids(self) -> set[int]:
        return {eid for eid, roles in self._roles.items() if Role.JUDGE in roles}

    @property
    def organizer_event_ids(self) -> set[int]:
        return {eid for eid, roles in self._roles.items() if Role.ORGANIZER in roles}


def _token_from(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def load_viewer(request: Request, db: Session) -> Viewer:
    token = _token_from(request)
    if not token:
        return Viewer(None)
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.expires_at > utcnow(),
        )
    )
    if session is None:
        return Viewer(None)
    roles: dict[int, set[str]] = {}
    for event_id, role in db.execute(
        select(EventRole.event_id, EventRole.role).where(EventRole.user_id == session.user_id)
    ):
        roles.setdefault(event_id, set()).add(role)
    return Viewer(session.user, roles)


def get_viewer(request: Request, db: Session = Depends(get_db)) -> Viewer:
    viewer = load_viewer(request, db)
    request.state.viewer = viewer
    return viewer


def require_user(viewer: Viewer = Depends(get_viewer)) -> Viewer:
    if not viewer.is_authenticated:
        raise NotAuthenticated()
    return viewer


def require_admin(viewer: Viewer = Depends(require_user)) -> Viewer:
    if not viewer.is_admin:
        raise Forbidden("admin_only", "Only an admin can do this.")
    return viewer


def require_organizer(viewer: Viewer, event: Event) -> None:
    if not viewer.is_organizer(event):
        raise Forbidden("organizer_only", "Only an organizer of this event can do this.",
                        audit=True, event_id=event.id,
                        actor_id=viewer.user.id if viewer.user else None)


def require_judge(viewer: Viewer, event: Event) -> None:
    if not viewer.is_judge(event):
        raise Forbidden("judge_only", "Only a judge of this event can do this.",
                        event_id=event.id, actor_id=viewer.user.id if viewer.user else None)


def team_membership(db: Session, viewer: Viewer, event: Event) -> TeamMember | None:
    if not viewer.user:
        return None
    return db.scalar(select(TeamMember).where(
        TeamMember.event_id == event.id, TeamMember.user_id == viewer.user.id))


def grant_role(db: Session, user: User, event: Event, role: str) -> bool:
    """Idempotent. Returns True when the role was newly granted."""
    exists = db.scalar(select(EventRole.id).where(
        EventRole.event_id == event.id, EventRole.user_id == user.id, EventRole.role == role))
    if exists:
        return False
    db.add(EventRole(event_id=event.id, user_id=user.id, role=role))
    return True
