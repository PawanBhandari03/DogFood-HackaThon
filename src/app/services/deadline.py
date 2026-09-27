"""The submission deadline. One function, called first by every write path
that creates, edits, submits or withdraws a project or changes a team."""

from datetime import datetime

from app.models import Event, utcnow
from app.security import Forbidden


def ensure_submissions_open(event: Event, *, now: datetime | None = None,
                            actor_id: int | None = None) -> None:
    now = now or utcnow()
    if now < event.starts_at:
        raise Forbidden("submissions_not_open",
                        f"Submissions for {event.name} open at {event.starts_at:%Y-%m-%d %H:%M} UTC.",
                        event_id=event.id, actor_id=actor_id)
    if now >= event.submissions_close_at:
        raise Forbidden("submissions_closed",
                        f"Submissions for {event.name} closed at "
                        f"{event.submissions_close_at:%Y-%m-%d %H:%M} UTC.",
                        audit=True, event_id=event.id, actor_id=actor_id)


def ensure_judging_open(event: Event, *, now: datetime | None = None,
                        actor_id: int | None = None) -> None:
    now = now or utcnow()
    if event.results_published_at is not None:
        raise Forbidden("results_published", "Results are published; scores are locked.",
                        event_id=event.id, actor_id=actor_id)
    if event.judging_close_at is not None and now >= event.judging_close_at:
        raise Forbidden("judging_closed", "Judging has closed for this event.",
                        event_id=event.id, actor_id=actor_id)
