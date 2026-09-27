"""The schema. DATA-MODEL.md explains every table and why it looks this way.

Conventions:
- Integer surrogate keys everywhere; `external_id` keeps the id a row had in
  an imported file (fixtures.json uses "evt_01", "prj_07", ...), so imports are
  idempotent and exports can round-trip those ids.
- Every timestamp is timezone-aware and stored in UTC.
- Roles are per event (event_roles). Only `users.is_admin` is global.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TZ = DateTime(timezone=True)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Role:
    ORGANIZER = "organizer"
    JUDGE = "judge"
    PARTICIPANT = "participant"
    ALL = (ORGANIZER, JUDGE, PARTICIPANT)


class ProjectStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    WITHDRAWN = "withdrawn"


class AssignmentStatus:
    PENDING = "pending"
    DONE = "done"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    can_create_events: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    @property
    def handle(self) -> str:
        """The id used in URLs: the imported id when there is one."""
        return self.external_id or str(self.id)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    label: Mapped[Optional[str]] = mapped_column(String(100))

    user: Mapped[User] = relationship()


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    tagline: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime] = mapped_column(TZ)
    submissions_close_at: Mapped[datetime] = mapped_column(TZ)
    judging_close_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    results_published_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    max_team_size: Mapped[int] = mapped_column(Integer, default=4)
    reviews_per_project: Mapped[int] = mapped_column(Integer, default=3)
    # Prior strength for normalization shrinkage; see JUDGING.md.
    shrinkage_k: Mapped[float] = mapped_column(Float, default=3.0)
    # Community voting (T3). "off" or "authenticated".
    voting_mode: Mapped[str] = mapped_column(String(20), default="off", server_default="off")
    voting_open_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    voting_close_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    max_votes: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    tracks: Mapped[list["Track"]] = relationship(back_populates="event", order_by="Track.position")
    prizes: Mapped[list["Prize"]] = relationship(back_populates="event", order_by="Prize.position")
    criteria: Mapped[list["RubricCriterion"]] = relationship(
        back_populates="event", order_by="RubricCriterion.position")

    __table_args__ = (
        CheckConstraint("submissions_close_at > starts_at", name="close_after_start"),
        CheckConstraint("max_team_size between 1 and 20", name="team_size_range"),
        CheckConstraint("reviews_per_project between 1 and 20", name="reviews_range"),
    )

    def submissions_open(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.starts_at <= now < self.submissions_close_at

    def phase(self, now: datetime | None = None) -> str:
        now = now or utcnow()
        if now < self.starts_at:
            return "upcoming"
        if now < self.submissions_close_at:
            return "open"
        if self.results_published_at:
            return "results"
        return "judging"


class EventRole(Base):
    __tablename__ = "event_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    user: Mapped[User] = relationship()
    event: Mapped[Event] = relationship()

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", "role"),
        CheckConstraint("role in ('organizer', 'judge', 'participant')", name="known_role"),
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    event: Mapped[Event] = relationship(back_populates="tracks")

    __table_args__ = (UniqueConstraint("event_id", "external_id"),)


class Prize(Base):
    __tablename__ = "prizes"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    reward: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    event: Mapped[Event] = relationship(back_populates="prizes")
    track: Mapped[Optional[Track]] = relationship()


class JudgeTrack(Base):
    """Which tracks a judge covers in an event. Drives assignment."""

    __tablename__ = "judge_tracks"

    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)


class JudgeInvite(Base):
    __tablename__ = "judge_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    track_ids: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    accepted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    event: Mapped[Event] = relationship()


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(64))
    # Deliberately not unique: fixtures.json has three teams called "StillTrail".
    name: Mapped[str] = mapped_column(String(120))
    invite_code: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    event: Mapped[Event] = relationship()
    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", order_by="TeamMember.joined_at")

    __table_args__ = (UniqueConstraint("event_id", "external_id"),)


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Copied from the team so the database can enforce one team per person per event.
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship()

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="one_team_per_event"),)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    external_id: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    repo_url: Mapped[str] = mapped_column(String(500), default="")
    demo_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default=ProjectStatus.DRAFT)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)
    # Set when this looks like a second submission of another project.
    # Flagged projects are left out of rankings until an organizer decides.
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    duplicate_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    event: Mapped[Event] = relationship()
    team: Mapped[Team] = relationship()
    track: Mapped[Optional[Track]] = relationship()
    duplicate_of: Mapped[Optional["Project"]] = relationship(remote_side="Project.id")

    __table_args__ = (
        UniqueConstraint("event_id", "external_id"),
        CheckConstraint("status in ('draft', 'submitted', 'withdrawn')", name="known_status"),
        # One live project per team. A flagged duplicate does not count, which
        # is how fixtures.json's prj_41 can exist next to prj_07.
        Index("one_live_project_per_team", "team_id", unique=True,
              postgresql_where=text("status <> 'withdrawn' AND duplicate_of_id IS NULL")),
    )

    @property
    def in_ranking(self) -> bool:
        return self.status == ProjectStatus.SUBMITTED and self.duplicate_of_id is None


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    min_value: Mapped[int] = mapped_column(Integer, default=1)
    max_value: Mapped[int] = mapped_column(Integer, default=5)
    position: Mapped[int] = mapped_column(Integer, default=0)

    event: Mapped[Event] = relationship(back_populates="criteria")

    __table_args__ = (
        UniqueConstraint("event_id", "key"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        CheckConstraint("max_value > min_value", name="range_valid"),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=AssignmentStatus.PENDING)
    method: Mapped[str] = mapped_column(String(20), default="manual")  # import | auto | manual
    assigned_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    judge: Mapped[User] = relationship()
    project: Mapped[Project] = relationship()
    score: Mapped[Optional["Score"]] = relationship(back_populates="assignment", uselist=False)

    __table_args__ = (
        UniqueConstraint("judge_id", "project_id"),
        CheckConstraint("status in ('pending', 'done')", name="known_assignment_status"),
    )


class Score(Base):
    """One judge's score sheet for one project. Values live in score_values."""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), unique=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    assignment: Mapped[Assignment] = relationship(back_populates="score")
    values: Mapped[list["ScoreValue"]] = relationship(
        back_populates="score", cascade="all, delete-orphan")


class ScoreValue(Base):
    __tablename__ = "score_values"

    score_id: Mapped[int] = mapped_column(ForeignKey("scores.id", ondelete="CASCADE"), primary_key=True)
    criterion_id: Mapped[int] = mapped_column(
        ForeignKey("rubric_criteria.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)

    score: Mapped[Score] = relationship(back_populates="values")
    criterion: Mapped[RubricCriterion] = relationship()


class AuditEntry(Base):
    """Append-only. Nothing in the app updates or deletes these rows."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64))

    actor: Mapped[Optional[User]] = relationship()


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    ip: Mapped[Optional[str]] = mapped_column(String(64))

    event: Mapped[Event] = relationship()
    project: Mapped[Project] = relationship()
    user: Mapped[User] = relationship()

    __table_args__ = (UniqueConstraint("user_id", "project_id"),)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    hidden_at: Mapped[Optional[datetime]] = mapped_column(TZ)
    hidden_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    project: Mapped[Project] = relationship()
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    hidden_by: Mapped[Optional[User]] = relationship(foreign_keys=[hidden_by_id])

