"""Signed, publicly verifiable judge participation records (T4-6).

A record covers one judge across all events they judged.  It contains:
- the judge's display name
- per-event: event name, slug, and number of completed reviews

The signature is HMAC-SHA256 over a canonical string that encodes every
field in the record, keyed with INSTANCE_SECRET.  Anyone can re-derive the
signature from the public fields and call /verify to check it was not tampered
with, without needing database access.

Design constraints (from the T4 guide):
- No project titles, score values, or comments may appear.
- Only review *counts* and *event names*.
"""

import hashlib
import hmac
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Assignment, AssignmentStatus, Event, Project, User


@dataclass
class EventParticipation:
    event_name: str
    event_slug: str
    reviews_done: int


@dataclass
class JudgeRecord:
    judge_ref: str          # external_id or str(id)
    judge_name: str
    events: list[EventParticipation]
    signature: str


def _canonical(judge_ref: str, events: list[EventParticipation]) -> str:
    """Deterministic string that encodes the full record for signing.

    Format: ``<judge_ref>|<slug>:<done>|<slug>:<done>|...``
    Events are sorted by slug so insertion order doesn't affect the signature.
    """
    parts = [judge_ref]
    for ep in sorted(events, key=lambda e: e.event_slug):
        parts.append(f"{ep.event_slug}:{ep.reviews_done}")
    return "|".join(parts)


def compute_signature(judge_ref: str, events: list[EventParticipation]) -> str:
    """HMAC-SHA256 signature over the canonical record string."""
    canonical = _canonical(judge_ref, events)
    digest = hmac.new(
        settings.instance_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def load_record(db: Session, user: User) -> JudgeRecord:
    """Build a JudgeRecord for *user* from current database state."""
    judge_ref = user.external_id or str(user.id)

    # Load all DONE assignments for this judge, with the event reached
    # through the project (Assignment has no direct `event` relationship,
    # only `event_id`; the real path to the Event row is project.event).
    assignments = db.scalars(
        select(Assignment)
        .where(
            Assignment.judge_id == user.id,
            Assignment.status == AssignmentStatus.DONE,
        )
        .options(selectinload(Assignment.project).selectinload(Project.event))
    ).all()

    # Group by event
    by_event: dict[int, tuple[Event, int]] = {}
    for a in assignments:
        ev = a.project.event
        if a.event_id not in by_event:
            by_event[a.event_id] = (ev, 0)
        ev, count = by_event[a.event_id]
        by_event[a.event_id] = (ev, count + 1)

    events = [
        EventParticipation(
            event_name=ev.name,
            event_slug=ev.slug,
            reviews_done=count,
        )
        for ev, count in sorted(by_event.values(), key=lambda t: t[0].slug)
    ]

    signature = compute_signature(judge_ref, events)
    return JudgeRecord(
        judge_ref=judge_ref,
        judge_name=user.name,
        events=events,
        signature=signature,
    )


def verify_signature(judge_ref: str, events: list[EventParticipation], sig: str) -> bool:
    """Return True if *sig* matches a freshly-computed signature for these inputs."""
    expected = compute_signature(judge_ref, events)
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected, sig)
