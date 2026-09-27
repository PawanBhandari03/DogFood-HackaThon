"""JSON and CSV API. Same rules as the pages: every route decides access
through app.auth before touching data, and judge data is always filtered by
the caller's own user id."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, get_viewer, require_organizer, require_user
from app.db import get_db
from app.models import Assignment, Event, Project, ProjectStatus, Score, ScoreValue, User
from app.security import Forbidden
from app.services import exports, projects as project_svc
from app.services.scoring import event_results
from app.web import event_or_404

router = APIRouter(prefix="/api", tags=["api"])


# --- shapes -----------------------------------------------------------------

def project_out(p: Project) -> dict:
    return {
        "id": p.external_id or str(p.id), "title": p.title, "summary": p.summary,
        "team": p.team.name, "track": p.track.name if p.track else None,
        "repo_url": p.repo_url, "demo_url": p.demo_url, "status": p.status,
        "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
    }


def assignment_out(a: Assignment) -> dict:
    return {
        "judge": a.judge.handle,
        "project": a.project.external_id or str(a.project.id),
        "project_title": a.project.title,
        "event": a.project.event.slug,
        "status": a.status,
        "criteria": {v.criterion.key: v.value for v in a.score.values} if a.score else None,
        "comment": a.score.comment if a.score else None,
        "submitted_at": a.score.submitted_at.isoformat() if a.score else None,
    }


def _assignments(db: Session, judge_id: int, event_ids: set[int] | None = None) -> list[Assignment]:
    q = (select(Assignment).where(Assignment.judge_id == judge_id)
         .options(selectinload(Assignment.judge),
                  selectinload(Assignment.project).selectinload(Project.event),
                  selectinload(Assignment.score).selectinload(Score.values).selectinload(ScoreValue.criterion))
         .order_by(Assignment.event_id, Assignment.project_id))
    if event_ids is not None:
        q = q.where(Assignment.event_id.in_(event_ids))
    return list(db.scalars(q))


# --- who am I -----------------------------------------------------------------

@router.get("/me")
def me(viewer: Viewer = Depends(require_user)):
    u = viewer.user
    return {"id": u.handle, "email": u.email, "name": u.name, "is_admin": u.is_admin,
            "roles": {str(eid): sorted(r) for eid, r in viewer._roles.items()}}


# --- judge scores (checked by run.py) -----------------------------------------

@router.get("/judge/scores")
def my_scores(viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    """The caller's own score sheets. Only judges get an answer."""
    if not viewer.judge_event_ids:
        raise Forbidden("judge_only", "Only judges have scores.", actor_id=viewer.user.id)
    rows = _assignments(db, viewer.user.id, viewer.judge_event_ids)
    return {"judge": viewer.user.handle, "scores": [assignment_out(a) for a in rows]}


@router.get("/judges/{judge_ref}/scores")
def judge_scores(judge_ref: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    """One judge's score sheets. Allowed for that judge, an admin, or an
    organizer (who sees only the events they organize). Anyone else, including
    every other judge, gets 403 and the attempt is written to the audit log."""
    target = db.scalar(select(User).where(
        (User.external_id == judge_ref) | (User.id == (int(judge_ref) if judge_ref.isdigit() else -1))))
    me_ = viewer.user
    if target is not None and target.id == me_.id:
        event_ids = None
    elif viewer.is_admin:
        event_ids = None
    elif viewer.organizer_event_ids:
        event_ids = viewer.organizer_event_ids
    else:
        raise Forbidden("peer_scores", "Judges can only see their own scores.", audit=True,
                        actor_id=me_.id, detail={"target": judge_ref})
    if target is None:
        raise HTTPException(404, "No such judge.")
    rows = _assignments(db, target.id, event_ids)
    return {"judge": target.handle, "scores": [assignment_out(a) for a in rows]}


# --- events and projects ------------------------------------------------------

@router.get("/events")
def list_events(db: Session = Depends(get_db)):
    events = db.scalars(select(Event).order_by(Event.starts_at.desc())).all()
    return [{"slug": e.slug, "name": e.name, "phase": e.phase(),
             "starts_at": e.starts_at.isoformat(), "submissions_close_at": e.submissions_close_at.isoformat()}
            for e in events]


@router.get("/events/{slug}/projects")
def list_projects(slug: str, db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    rows = db.scalars(select(Project).where(
        Project.event_id == event.id, Project.status == ProjectStatus.SUBMITTED)
        .options(selectinload(Project.team), selectinload(Project.track)).order_by(Project.title))
    return [project_out(p) for p in rows]


class ProjectIn(BaseModel):
    # Everything optional at the schema level so the deadline is checked
    # before content: a late request is refused as late, not as malformed.
    title: str = ""
    summary: str = ""
    description: str = ""
    repo_url: str = ""
    demo_url: str = ""
    track_id: int | None = None
    submit: bool = False


@router.post("/events/{slug}/projects", status_code=201)
def create_project(slug: str, payload: ProjectIn, request: Request,
                   viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    """Create or update the caller's team project; `submit: true` also submits it."""
    event = event_or_404(db, slug)
    fields = project_svc.ProjectFields(payload.title, payload.summary, payload.description,
                                       payload.repo_url, payload.demo_url, payload.track_id)
    project = project_svc.save_project(db, viewer, event, fields, request)
    if payload.submit:
        project = project_svc.set_status(db, viewer, event, ProjectStatus.SUBMITTED, request)
    db.commit()
    db.refresh(project)
    return project_out(project)


# --- organizer data -----------------------------------------------------------

from app.services.voting import voting_open


@router.get("/events/{slug}/results")
def results(slug: str, viewer: Viewer = Depends(get_viewer), db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    if not event.results_published_at or voting_open(event):
        if not viewer.is_authenticated:
            raise Forbidden("results_hidden", "Results are not published yet.")
        require_organizer(viewer, event)
    res = event_results(db, event)
    return {
        "event": event.slug,
        "published": event.results_published_at is not None,
        "normalization": {"method": "shrunk per-judge z-score", "k": res.normalization.k,
                          "global_mean": res.normalization.global_mean,
                          "global_sd": res.normalization.global_sd},
        "ranking": [{"rank": r.rank, "project": res.projects[r.project_id].external_id or r.project_id,
                     "title": res.projects[r.project_id].title, "reviews": r.n,
                     "raw": r.raw_mean, "normalized": r.normalized_mean, "stderr": r.stderr,
                     "raw_rank": r.raw_rank} for r in res.rows],
    }


def _organizer_event(slug: str, viewer: Viewer, db: Session) -> Event:
    event = event_or_404(db, slug)
    require_organizer(viewer, event)
    return event


def _csv_response(body: str, filename: str) -> Response:
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/events/{slug}/export/results.csv")
def export_results(slug: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _organizer_event(slug, viewer, db)
    return _csv_response(exports.results_csv(db, event), f"{event.slug}-results.csv")


@router.get("/events/{slug}/export/scores.csv")
def export_scores(slug: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _organizer_event(slug, viewer, db)
    return _csv_response(exports.scores_csv(db, event), f"{event.slug}-scores.csv")


@router.get("/events/{slug}/export/projects.csv")
def export_projects(slug: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _organizer_event(slug, viewer, db)
    return _csv_response(exports.projects_csv(db, event), f"{event.slug}-projects.csv")


@router.get("/events/{slug}/export/votes.csv")
def export_votes(slug: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _organizer_event(slug, viewer, db)
    return _csv_response(exports.votes_csv(db, event), f"{event.slug}-votes.csv")


@router.get("/events/{slug}/export.json")
def export_json(slug: str, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _organizer_event(slug, viewer, db)
    return exports.event_json(db, event)
