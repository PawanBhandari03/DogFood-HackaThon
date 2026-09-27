"""Pages anyone can see without logging in."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, get_viewer, team_membership
from app.db import get_db
from app.models import Event, Project, ProjectStatus, Team, TeamMember, Track
from app.services.scoring import event_results
from app.web import event_or_404, render

router = APIRouter()

PAGE_SIZE = 60


@router.get("/")
def home(request: Request, viewer: Viewer = Depends(get_viewer), db: Session = Depends(get_db)):
    events = db.scalars(select(Event).order_by(Event.submissions_close_at.desc())).all()
    counts = dict(db.execute(
        select(Project.event_id, func.count()).where(Project.status == ProjectStatus.SUBMITTED)
        .group_by(Project.event_id)).all())
    return render(request, "home.html", events=events, counts=counts)


@router.get("/projects")
def gallery(request: Request, q: str = "", event: str = "", track: str = "", page: int = 1,
            viewer: Viewer = Depends(get_viewer), db: Session = Depends(get_db)):
    query = (select(Project).join(Team, Team.id == Project.team_id).join(Event, Event.id == Project.event_id)
             .outerjoin(Track, Track.id == Project.track_id)
             .where(Project.status == ProjectStatus.SUBMITTED, Project.duplicate_of_id.is_(None)))
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(Project.title.ilike(like), Project.summary.ilike(like), Team.name.ilike(like)))
    if event:
        query = query.where(Event.slug == event)
    if track:
        query = query.where(Track.name == track)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(page, 1)
    projects = db.scalars(
        query.options(selectinload(Project.team), selectinload(Project.track), selectinload(Project.event))
        .order_by(Event.submissions_close_at.desc(), Project.title, Project.id)
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    events = db.scalars(select(Event).order_by(Event.submissions_close_at.desc())).all()
    tracks = sorted(set(db.scalars(select(Track.name))))
    return render(request, "gallery.html", projects=projects, total=total, page=page,
                  pages=max(1, -(-total // PAGE_SIZE)), q=q, event=event, track=track,
                  events=events, tracks=tracks)


@router.get("/projects/{ref}")
def project_page(ref: str, request: Request, viewer: Viewer = Depends(get_viewer),
                 db: Session = Depends(get_db)):
    project = db.scalar(select(Project).where(
        (Project.external_id == ref) | (Project.id == (int(ref) if ref.isdigit() else -1)))
        .options(selectinload(Project.team).selectinload(Team.members).selectinload(TeamMember.user),
                 selectinload(Project.track), selectinload(Project.event)))
    if project is None:
        raise HTTPException(404, "No such project.")
    member = team_membership(db, viewer, project.event)
    own = member is not None and member.team_id == project.team_id
    if project.status != ProjectStatus.SUBMITTED and not (own or viewer.is_organizer(project.event)):
        raise HTTPException(404, "No such project.")
    return render(request, "project.html", project=project, own=own)


@router.get("/events/{slug}")
def event_page(slug: str, request: Request, viewer: Viewer = Depends(get_viewer),
               db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    submitted = db.scalar(select(func.count()).select_from(Project).where(
        Project.event_id == event.id, Project.status == ProjectStatus.SUBMITTED))
    teams = db.scalar(select(func.count()).select_from(Team).where(Team.event_id == event.id))
    member = team_membership(db, viewer, event)
    return render(request, "event.html", event=event, submitted=submitted, teams=teams, member=member)


@router.get("/events/{slug}/results")
def results_page(slug: str, request: Request, viewer: Viewer = Depends(get_viewer),
                 db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    preview = False
    if not event.results_published_at:
        if not viewer.is_organizer(event):
            return render(request, "results_hidden.html", status_code=403, event=event)
        preview = True
    res = event_results(db, event)
    return render(request, "results.html", event=event, res=res, preview=preview)
