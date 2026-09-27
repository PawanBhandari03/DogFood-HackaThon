"""Pages anyone can see without logging in."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, get_viewer, require_organizer, require_user, team_membership
from app.db import get_db
from app.models import Comment, Event, Project, ProjectStatus, Team, TeamMember, Track, utcnow
from app.security import Forbidden
from app.services import audit
from app.services.ratelimit import comment_limiter
from app.services.scoring import event_results
from app.web import event_or_404, redirect, render

router = APIRouter()


@router.get("/events/{slug}/certificates/{ref}", include_in_schema=False)
def certificate(slug: str, ref: str, request: Request, db: Session = Depends(get_db)):
    """Public printable certificate for a submitted project.

    ``ref`` is a team's external_id or numeric id.  No login required so
    certificates can be shared publicly.  Rank is shown only after
    results_published_at is set on the event.
    """
    event = event_or_404(db, slug)

    # Resolve team by external_id or numeric id
    team = db.scalar(
        select(Team)
        .where(
            Team.event_id == event.id,
            (Team.external_id == ref) | (Team.id == (int(ref) if ref.isdigit() else -1)),
        )
        .options(
            selectinload(Team.members).selectinload(TeamMember.user),
        )
    )
    if team is None:
        raise HTTPException(404, "No such team.")

    project = db.scalar(
        select(Project)
        .where(Project.team_id == team.id, Project.event_id == event.id)
        .options(selectinload(Project.team).selectinload(Team.members).selectinload(TeamMember.user))
    )

    # Only show anything useful if the project is submitted
    available = project is not None and project.status == ProjectStatus.SUBMITTED
    results_published = event.results_published_at is not None

    rank = None
    if available and results_published:
        res = event_results(db, event)
        for row in res.rows:
            if row.project_id == project.id:
                rank = row.rank
                break

    return render(
        request,
        "certificate.html",
        event=event,
        project=project,
        ref=ref,
        available=available,
        results_published=results_published,
        rank=rank,
        # certificate.html is standalone — suppress the base layout nav
        _standalone=True,
    )

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

    is_org = viewer.is_organizer(project.event)
    q = select(Comment).where(Comment.project_id == project.id).options(selectinload(Comment.user), selectinload(Comment.hidden_by))
    if not is_org:
        q = q.where(Comment.hidden_at.is_(None))
    comments = db.scalars(q.order_by(Comment.created_at.asc())).all()

    return render(request, "project.html", project=project, own=own, comments=comments, is_org=is_org)


@router.post("/projects/{ref}/comments")
def post_comment(ref: str, request: Request, body: str = Form(...),
                 viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    project = db.scalar(select(Project).where(
        (Project.external_id == ref) | (Project.id == (int(ref) if ref.isdigit() else -1)))
        .options(selectinload(Project.event)))
    if project is None or project.status != ProjectStatus.SUBMITTED:
        raise HTTPException(404, "Project not found or not submitted.")

    cleaned_body = body.strip()
    if not (1 <= len(cleaned_body) <= 2000):
        raise HTTPException(422, "Comment must be between 1 and 2000 characters.")

    if not comment_limiter.allow(f"{viewer.user.id}"):
        raise Forbidden("rate_limited", "Too many comments. Please wait a minute.",
                        event_id=project.event_id, actor_id=viewer.user.id)

    comment = Comment(
        project_id=project.id,
        user_id=viewer.user.id,
        body=cleaned_body,
    )
    db.add(comment)
    db.flush()
    audit.record(db, "comment.posted", actor=viewer.user, event_id=project.event_id,
                 entity_type="comment", entity_id=comment.id,
                 detail={"project_id": project.id, "project_title": project.title, "length": len(cleaned_body)},
                 request=request)
    db.commit()
    target_ref = project.external_id or project.id
    return redirect(f"/projects/{target_ref}", "Comment posted.")


@router.post("/comments/{id}/hide")
def hide_comment(id: int, request: Request, viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    comment = db.get(Comment, id)
    if comment is None:
        raise HTTPException(404, "No such comment.")
    project = db.get(Project, comment.project_id)
    if project is None:
        raise HTTPException(404, "No such project.")
    require_organizer(viewer, project.event)

    comment.hidden_at = utcnow()
    comment.hidden_by_id = viewer.user.id
    audit.record(db, "comment.hidden", actor=viewer.user, event_id=project.event_id,
                 entity_type="comment", entity_id=comment.id,
                 detail={"comment_id": comment.id, "project_id": project.id},
                 request=request)
    db.commit()
    target_ref = project.external_id or project.id
    return redirect(f"/projects/{target_ref}", "Comment hidden.")


@router.get("/events/{slug}")
def event_page(slug: str, request: Request, viewer: Viewer = Depends(get_viewer),
               db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    submitted = db.scalar(select(func.count()).select_from(Project).where(
        Project.event_id == event.id, Project.status == ProjectStatus.SUBMITTED))
    teams = db.scalar(select(func.count()).select_from(Team).where(Team.event_id == event.id))
    member = team_membership(db, viewer, event)
    return render(request, "event.html", event=event, submitted=submitted, teams=teams, member=member)


from app.services.voting import voting_open


@router.get("/events/{slug}/results")
def results_page(slug: str, request: Request, viewer: Viewer = Depends(get_viewer),
                 db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    preview = False
    if not event.results_published_at or voting_open(event):
        if not viewer.is_organizer(event):
            return render(request, "results_hidden.html", status_code=403, event=event)
        preview = True
    res = event_results(db, event)
    return render(request, "results.html", event=event, res=res, preview=preview)
