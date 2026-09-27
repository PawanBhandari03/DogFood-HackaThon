"""Creating, editing, submitting and withdrawing a team's project.

Every function checks the deadline before doing anything else, so a closed
event refuses writes for the right reason no matter which route called it.
"""

from dataclasses import dataclass
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Viewer, grant_role, team_membership
from app.models import Event, Project, ProjectStatus, Role, Team, Track, utcnow
from app.security import Forbidden
from app.services import audit
from app.services.deadline import ensure_submissions_open


@dataclass
class ProjectFields:
    title: str = ""
    summary: str = ""
    description: str = ""
    repo_url: str = ""
    demo_url: str = ""
    track_id: int | None = None

    def cleaned(self) -> "ProjectFields":
        return ProjectFields(
            title=self.title.strip()[:200], summary=self.summary.strip()[:500],
            description=self.description.strip()[:20000], repo_url=self.repo_url.strip()[:500],
            demo_url=self.demo_url.strip()[:500], track_id=self.track_id)


def live_project(db: Session, team: Team) -> Project | None:
    return db.scalar(select(Project).where(
        Project.team_id == team.id,
        Project.status != ProjectStatus.WITHDRAWN,
        Project.duplicate_of_id.is_(None)))


def _require_team(db: Session, viewer: Viewer, event: Event) -> Team:
    member = team_membership(db, viewer, event)
    if member is None:
        raise Forbidden("no_team", "Join or create a team in this event before submitting a project.",
                        event_id=event.id, actor_id=viewer.user.id)
    return member.team


def _validate(db: Session, event: Event, f: ProjectFields) -> None:
    if not f.title:
        raise HTTPException(422, "A project needs a title.")
    for url in (f.repo_url, f.demo_url):
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(422, "Links must start with http:// or https://.")
    if f.track_id is not None and not db.scalar(
            select(Track.id).where(Track.id == f.track_id, Track.event_id == event.id)):
        raise HTTPException(422, "That track is not part of this event.")


def save_project(db: Session, viewer: Viewer, event: Event, fields: ProjectFields,
                 request: Request | None = None) -> Project:
    ensure_submissions_open(event, actor_id=viewer.user.id)
    team = _require_team(db, viewer, event)
    f = fields.cleaned()
    _validate(db, event, f)
    project = live_project(db, team)
    created = project is None
    if created:
        project = Project(event_id=event.id, team_id=team.id, status=ProjectStatus.DRAFT)
        db.add(project)
    before = None if created else {"title": project.title, "status": project.status}
    for key in ("title", "summary", "description", "repo_url", "demo_url", "track_id"):
        setattr(project, key, getattr(f, key))
    project.updated_at = utcnow()
    db.flush()
    grant_role(db, viewer.user, event, Role.PARTICIPANT)
    audit.record(db, "project.created" if created else "project.updated", actor=viewer.user,
                 event_id=event.id, entity_type="project", entity_id=project.id,
                 detail={"title": project.title, "before": before}, request=request)
    return project


def set_status(db: Session, viewer: Viewer, event: Event, status: str,
               request: Request | None = None) -> Project:
    ensure_submissions_open(event, actor_id=viewer.user.id)
    team = _require_team(db, viewer, event)
    project = live_project(db, team)
    if project is None:
        raise HTTPException(404, "Your team has no project yet.")
    if status == ProjectStatus.SUBMITTED:
        _validate(db, event, ProjectFields(title=project.title, repo_url=project.repo_url,
                                           demo_url=project.demo_url, track_id=project.track_id))
        if event.tracks and project.track_id is None:
            raise HTTPException(422, "Pick a track before submitting.")
        project.submitted_at = utcnow()
    previous = project.status
    project.status = status
    db.flush()
    audit.record(db, f"project.{status}", actor=viewer.user, event_id=event.id,
                 entity_type="project", entity_id=project.id,
                 detail={"title": project.title, "from": previous}, request=request)
    return project
