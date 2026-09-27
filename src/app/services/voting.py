"""Community voting service (T3).

Handles voting window checks, stable randomized ballots, rate-limited voting,
retractions, tallies, and organizer abuse signals.
"""

from datetime import datetime, timedelta
import random

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Viewer
from app.models import Event, Project, ProjectStatus, TeamMember, User, Vote, utcnow
from app.security import Forbidden, NotAuthenticated
from app.services import audit
from app.services.ratelimit import vote_limiter


def voting_open(event: Event, now: datetime | None = None) -> bool:
    """Returns True if authenticated community voting is currently open."""
    if event.voting_mode != "authenticated":
        return False
    if event.voting_open_at is None or event.voting_close_at is None:
        return False
    now = now or utcnow()
    return event.voting_open_at <= now < event.voting_close_at


def ensure_voting_open(event: Event, actor_id: int | None = None, now: datetime | None = None) -> None:
    """Raises Forbidden('voting_closed') if voting is not currently open."""
    if not voting_open(event, now):
        raise Forbidden(
            "voting_closed",
            f"Voting for {event.name} is closed or not configured.",
            audit=True,
            event_id=event.id,
            actor_id=actor_id,
        )


def ballot(db: Session, event: Event, user: User) -> list[Project]:
    """Returns the event's ranked projects shuffled deterministically per voter."""
    projects = list(
        db.scalars(
            select(Project)
            .where(
                Project.event_id == event.id,
                Project.status == ProjectStatus.SUBMITTED,
                Project.duplicate_of_id.is_(None),
            )
            .order_by(Project.id)
        ).all()
    )
    rng = random.Random(f"{event.id}:{user.id}")
    rng.shuffle(projects)
    return projects


def user_votes(db: Session, event: Event, user: User) -> list[Vote]:
    """Returns all votes cast by a user for a given event."""
    return list(
        db.scalars(
            select(Vote)
            .where(Vote.event_id == event.id, Vote.user_id == user.id)
            .order_by(Vote.created_at.asc())
        ).all()
    )


def cast_vote(
    db: Session,
    viewer: Viewer,
    event: Event,
    project: Project,
    request: Request | None = None,
) -> None:
    """Casts a vote for a project by an authenticated user.
    
    Checks in exact order:
    1. voting open
    2. rate limit (30 per min per user)
    3. project belongs to event and is in ranking
    4. voter not on project's team
    5. voter not judge or organizer of event
    6. voter has fewer than max_votes
    7. insert with duplicate detection
    """
    if not viewer.user:
        raise NotAuthenticated()

    user = viewer.user

    # 1. Voting open
    ensure_voting_open(event, actor_id=user.id)

    # 2. Rate limiter
    if not vote_limiter.allow(f"{user.id}"):
        raise Forbidden("rate_limited", "Too many voting actions. Please wait a minute.",
                        event_id=event.id, actor_id=user.id)

    # 3. Project belongs to event and is in ranking
    if project.event_id != event.id or not project.in_ranking:
        raise Forbidden("project_not_votable", "This project is not eligible for voting.",
                        event_id=event.id, actor_id=user.id)

    # 4. Voter is not on the project's team
    if project.team_id:
        on_team = db.scalar(
            select(TeamMember.team_id).where(
                TeamMember.team_id == project.team_id,
                TeamMember.user_id == user.id,
            )
        )
        if on_team:
            raise Forbidden("cannot_vote_own_team", "You cannot vote for your own team's project.",
                            event_id=event.id, actor_id=user.id)

    # 5. Voter is not a judge or organizer of the event
    if viewer.is_organizer(event) or viewer.is_judge(event):
        raise Forbidden("role_cannot_vote", "Judges and organizers of the event cannot vote.",
                        event_id=event.id, actor_id=user.id)

    # 6. Voter has fewer than event.max_votes
    existing_vote = db.scalar(
        select(Vote).where(Vote.event_id == event.id, Vote.project_id == project.id, Vote.user_id == user.id)
    )
    if existing_vote:
        # Already voted for this project; idempotent success
        return

    current_vote_count = db.scalar(
        select(func.count(Vote.id)).where(Vote.event_id == event.id, Vote.user_id == user.id)
    ) or 0

    if current_vote_count >= event.max_votes:
        raise Forbidden(
            "max_votes_reached",
            f"You have already used all {event.max_votes} votes for this event.",
            event_id=event.id,
            actor_id=user.id,
        )

    # 7. Insert with IntegrityError duplicate catch
    vote = Vote(
        event_id=event.id,
        project_id=project.id,
        user_id=user.id,
        ip=audit.client_ip(request),
    )
    try:
        with db.begin_nested():
            db.add(vote)
            db.flush()
        audit.record(
            db,
            "vote.cast",
            actor=user,
            event_id=event.id,
            entity_type="vote",
            entity_id=project.id,
            detail={"project_id": project.id, "project_title": project.title},
            request=request,
        )
    except IntegrityError:
        # Concurrent vote duplicate detected; treat as already voted
        pass


def retract_vote(
    db: Session,
    viewer: Viewer,
    event: Event,
    project: Project,
    request: Request | None = None,
) -> None:
    """Retracts a previously cast vote."""
    if not viewer.user:
        raise NotAuthenticated()

    user = viewer.user
    ensure_voting_open(event, actor_id=user.id)

    vote = db.scalar(
        select(Vote).where(
            Vote.event_id == event.id,
            Vote.project_id == project.id,
            Vote.user_id == user.id,
        )
    )
    if vote:
        db.delete(vote)
        db.flush()
        audit.record(
            db,
            "vote.retracted",
            actor=user,
            event_id=event.id,
            entity_type="vote",
            entity_id=project.id,
            detail={"project_id": project.id, "project_title": project.title},
            request=request,
        )


def tallies(db: Session, event: Event) -> list[tuple[Project, int]]:
    """Returns all ranked projects with their vote counts, sorted by count descending."""
    stmt = (
        select(Project, func.count(Vote.id).label("vote_count"))
        .outerjoin(Vote, Vote.project_id == Project.id)
        .where(
            Project.event_id == event.id,
            Project.status == ProjectStatus.SUBMITTED,
            Project.duplicate_of_id.is_(None),
        )
        .group_by(Project.id)
        .order_by(func.count(Vote.id).desc(), Project.title.asc())
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def abuse_signals(db: Session, event: Event) -> dict:
    """Generates abuse detection signals for organizers."""
    # 1. Votes from accounts created after voting opened
    new_account_votes = []
    if event.voting_open_at:
        rows = db.execute(
            select(Vote, User, Project)
            .join(User, User.id == Vote.user_id)
            .join(Project, Project.id == Vote.project_id)
            .where(Vote.event_id == event.id, User.created_at > event.voting_open_at)
            .order_by(Vote.created_at.desc())
        ).all()
        for v, u, p in rows:
            new_account_votes.append({
                "vote_id": v.id,
                "user": u,
                "project": p,
                "user_created_at": u.created_at,
                "voted_at": v.created_at,
                "ip": v.ip,
            })

    # 2. IP addresses shared by 3 or more distinct voters
    shared_ip_rows = db.execute(
        select(
            Vote.ip,
            func.count(Vote.user_id.distinct()).label("user_count"),
            func.count(Vote.id).label("vote_count"),
        )
        .where(Vote.event_id == event.id, Vote.ip.isnot(None), Vote.ip != "")
        .group_by(Vote.ip)
        .having(func.count(Vote.user_id.distinct()) >= 3)
        .order_by(func.count(Vote.user_id.distinct()).desc())
    ).all()
    shared_ips = [
        {"ip": r[0], "user_count": r[1], "vote_count": r[2]}
        for r in shared_ip_rows
    ]

    # 3. Voters who cast all their votes within 10 seconds
    user_vote_times = db.execute(
        select(
            Vote.user_id,
            func.min(Vote.created_at).label("first_vote"),
            func.max(Vote.created_at).label("last_vote"),
            func.count(Vote.id).label("vote_count"),
        )
        .where(Vote.event_id == event.id)
        .group_by(Vote.user_id)
        .having(func.count(Vote.id) >= 2)
    ).all()

    fast_voters = []
    for row in user_vote_times:
        delta = (row.last_vote - row.first_vote).total_seconds()
        if delta <= 10.0:
            u = db.get(User, row.user_id)
            fast_voters.append({
                "user": u,
                "user_id": row.user_id,
                "vote_count": row.vote_count,
                "duration_seconds": round(delta, 2),
                "first_vote": row.first_vote,
                "last_vote": row.last_vote,
            })

    return {
        "new_accounts": new_account_votes,
        "shared_ips": shared_ips,
        "fast_voters": fast_voters,
    }
