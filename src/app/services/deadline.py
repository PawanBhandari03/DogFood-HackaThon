"""The submission deadline. One function, called first by every write path
that creates, edits, submits or withdraws a project or changes a team."""

from datetime import datetime, timezone

from app.models import Event, utcnow
from app.security import Forbidden


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def ensure_submissions_open(event: Event, *, now: datetime | None = None,
                            actor_id: int | None = None) -> None:
    now = _as_utc(now) or utcnow()
    starts_at = _as_utc(event.starts_at)
    close_at = _as_utc(event.submissions_close_at)
    if starts_at and now < starts_at:
        raise Forbidden("submissions_not_open",
                        f"Submissions for {event.name} open at {starts_at:%Y-%m-%d %H:%M} UTC.",
                        event_id=event.id, actor_id=actor_id)
    if close_at and now >= close_at:
        raise Forbidden("submissions_closed",
                        f"Submissions for {event.name} closed at "
                        f"{close_at:%Y-%m-%d %H:%M} UTC.",
                        audit=True, event_id=event.id, actor_id=actor_id)


def ensure_judging_open(event: Event, *, now: datetime | None = None,
                        actor_id: int | None = None) -> None:
    now = _as_utc(now) or utcnow()
    judging_close = _as_utc(event.judging_close_at)
    if event.results_published_at is not None:
        raise Forbidden("results_published", "Results are published; scores are locked.",
                        event_id=event.id, actor_id=actor_id)
    if judging_close is not None and now >= judging_close:
        raise Forbidden("judging_closed", "Judging has closed for this event.",
                        event_id=event.id, actor_id=actor_id)
