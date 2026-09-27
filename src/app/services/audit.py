from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditEntry, User


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def record(db: Session, action: str, *, actor: User | None = None, actor_id: int | None = None,
           event_id: int | None = None, entity_type: str = "", entity_id: object = None,
           detail: dict | None = None, request: Request | None = None) -> None:
    """Add an audit row to the current transaction. The caller commits."""
    db.add(AuditEntry(
        actor_id=actor.id if actor else actor_id,
        event_id=event_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        detail=detail or {},
        ip=client_ip(request),
    ))
