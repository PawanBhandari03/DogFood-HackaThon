"""Organizer pages. Every route loads the event and calls require_organizer
before reading or changing anything."""

import random

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, grant_role, require_organizer, require_user
from app.db import get_db
from app.models import (
    Assignment,
    AssignmentStatus,
    AuditEntry,
    Comment,
    Event,
    EventRole,
    JudgeInvite,
    JudgeTrack,
    Prize,
    Project,
    ProjectStatus,
    Role,
    RubricCriterion,
    ScoreValue,
    Team,
    TeamMember,
    Track,
    User,
    Vote,
    utcnow,
)
from app.security import Forbidden, new_token
from app.services import assignment as assign_svc
from app.services import audit
from app.services.progress import event_progress
from app.services.scoring import event_results
from app.services.voting import abuse_signals, tallies, voting_open
from app.util import parse_local_datetime, slugify
from app.web import event_or_404, redirect, render

router = APIRouter()


def _manage(db: Session, viewer: Viewer, slug: str) -> Event:
    event = event_or_404(db, slug)
    require_organizer(viewer, event)
    return event


def _url(event: Event, page: str = "") -> str:
    return f"/events/{event.slug}/manage" + (f"/{page}" if page else "")


def _log(db, viewer, event, action, request, **kw):
    audit.record(db, action, actor=viewer.user, event_id=event.id, request=request, **kw)


# --- create an event ------------------------------------------------------------

@router.get("/events/new")
def new_event_form(request: Request, viewer: Viewer = Depends(require_user)):
    if not (viewer.is_admin or viewer.user.can_create_events):
        raise Forbidden("cannot_create_events", "Ask an admin to let you create events.")
    return render(request, "event_new.html", error=None)


@router.post("/events/new")
def create_event(request: Request, name: str = Form(...), tagline: str = Form(""),
                 starts_at: str = Form(...), submissions_close_at: str = Form(...),
                 tracks: str = Form(""), viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    if not (viewer.is_admin or viewer.user.can_create_events):
        raise Forbidden("cannot_create_events", "Ask an admin to let you create events.")
    try:
        start, close = parse_local_datetime(starts_at), parse_local_datetime(submissions_close_at)
    except ValueError:
        raise HTTPException(422, "Dates must look like 2026-10-01T18:00.")
    if close <= start:
        return render(request, "event_new.html", status_code=400,
                      error="Submissions must close after the event starts.")
    base = slugify(name)
    slug, n = base, 2
    while db.scalar(select(Event.id).where(Event.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    event = Event(slug=slug, name=name.strip()[:200], tagline=tagline.strip()[:300], starts_at=start,
                  submissions_close_at=close, created_by_id=viewer.user.id)
    db.add(event)
    db.flush()
    for i, t in enumerate([t.strip() for t in tracks.splitlines() if t.strip()]):
        db.add(Track(event_id=event.id, name=t[:120], position=i))
    for i, key in enumerate(["functionality", "quality", "innovation"]):
        db.add(RubricCriterion(event_id=event.id, key=key, label=key.capitalize(), position=i))
    grant_role(db, viewer.user, event, Role.ORGANIZER)
    _log(db, viewer, event, "event.created", request, entity_type="event", entity_id=event.slug)
    db.commit()
    return redirect(_url(event), "Event created. Next: check the rubric and invite judges.")


# --- overview and live progress ----------------------------------------------------

@router.get("/events/{slug}/manage")
def overview(slug: str, request: Request, viewer: Viewer = Depends(require_user),
             db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    counts = {
        "teams": db.scalar(select(func.count()).select_from(Team).where(Team.event_id == event.id)),
        "drafts": db.scalar(select(func.count()).select_from(Project).where(
            Project.event_id == event.id, Project.status == ProjectStatus.DRAFT)),
    }
    duplicates = db.scalars(select(Project).where(Project.event_id == event.id,
                                                  Project.duplicate_of_id.is_not(None),
                                                  Project.status != ProjectStatus.WITHDRAWN)
                            .options(selectinload(Project.duplicate_of), selectinload(Project.team))).all()
    return render(request, "manage/overview.html", event=event, tab="overview",
                  progress=event_progress(db, event), counts=counts, duplicates=duplicates)


@router.get("/events/{slug}/manage/progress")
def progress_partial(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    """The fragment htmx polls every few seconds on the overview page."""
    event = _manage(db, viewer, slug)
    return render(request, "manage/_progress.html", event=event, progress=event_progress(db, event))


@router.post("/events/{slug}/manage/duplicates/{project_id}")
def resolve_duplicate(slug: str, project_id: int, request: Request, action: str = Form(...),
                      viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    project = db.get(Project, project_id)
    if project is None or project.event_id != event.id or project.duplicate_of_id is None:
        raise HTTPException(404, "No such flagged project.")
    original = db.get(Project, project.duplicate_of_id)
    if action == "withdraw":
        # The usual case: the later entry is a resubmission. Withdraw it.
        project.status = ProjectStatus.WITHDRAWN
    elif action == "replace":
        # The later entry is the real one: withdraw the original and rank this.
        # Both steps flush in order so the one-live-project index never sees two.
        original.status = ProjectStatus.WITHDRAWN
        db.flush()
        project.duplicate_of_id = None
        project.duplicate_dismissed = True
    else:
        raise HTTPException(422, "Unknown action.")
    _log(db, viewer, event, f"duplicate.{action}", request, entity_type="project", entity_id=project.id,
         detail={"duplicate_of": original.id, "title": project.title})
    db.commit()
    return redirect(_url(event), "Duplicate resolved.")


# --- settings, tracks, prizes, organizers --------------------------------------------

@router.get("/events/{slug}/manage/settings")
def settings_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                  db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    organizers = db.scalars(select(User).join(EventRole, EventRole.user_id == User.id).where(
        EventRole.event_id == event.id, EventRole.role == Role.ORGANIZER).order_by(User.name)).all()
    return render(request, "manage/settings.html", event=event, tab="settings", organizers=organizers)


@router.post("/events/{slug}/manage/settings")
def save_settings(slug: str, request: Request, name: str = Form(...), tagline: str = Form(""),
                  description: str = Form(""), starts_at: str = Form(...),
                  submissions_close_at: str = Form(...), judging_close_at: str = Form(""),
                  max_team_size: int = Form(4), reviews_per_project: int = Form(3),
                  shrinkage_k: float = Form(3.0), voting_mode: str = Form("off"),
                  voting_open_at: str = Form(""), voting_close_at: str = Form(""),
                  max_votes: int = Form(3), viewer: Viewer = Depends(require_user),
                  db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    try:
        start, close = parse_local_datetime(starts_at), parse_local_datetime(submissions_close_at)
        judging = parse_local_datetime(judging_close_at) if judging_close_at else None
        v_open = parse_local_datetime(voting_open_at) if voting_open_at else None
        v_close = parse_local_datetime(voting_close_at) if voting_close_at else None
    except ValueError:
        raise HTTPException(422, "Dates must look like 2026-10-01T18:00.")
    if close <= start:
        raise HTTPException(422, "Submissions must close after the event starts.")
    if not (1 <= max_team_size <= 20 and 1 <= reviews_per_project <= 20 and 0 <= shrinkage_k <= 50):
        raise HTTPException(422, "Team size and reviews must be 1-20; k must be 0-50.")
    if voting_mode not in ("off", "authenticated"):
        raise HTTPException(422, "Voting mode must be off or authenticated.")
    if not (1 <= max_votes <= 20):
        raise HTTPException(422, "Max votes must be 1-20.")
    if voting_mode == "authenticated" and v_open and v_close and v_close <= v_open:
        raise HTTPException(422, "Voting must close after it opens.")

    before = {"starts_at": event.starts_at.isoformat(), "close": event.submissions_close_at.isoformat(),
              "k": event.shrinkage_k}
    event.name, event.tagline, event.description = name.strip()[:200], tagline.strip()[:300], description
    event.starts_at, event.submissions_close_at, event.judging_close_at = start, close, judging
    event.max_team_size, event.reviews_per_project, event.shrinkage_k = \
        max_team_size, reviews_per_project, shrinkage_k
    event.voting_mode = voting_mode
    event.voting_open_at = v_open
    event.voting_close_at = v_close
    event.max_votes = max_votes
    _log(db, viewer, event, "event.updated", request, entity_type="event", entity_id=event.slug,
         detail={"before": before, "after": {"starts_at": start.isoformat(), "close": close.isoformat(),
                                              "k": shrinkage_k, "voting_mode": voting_mode}})
    db.commit()
    return redirect(_url(event, "settings"), "Settings saved.")


@router.post("/events/{slug}/manage/tracks")
def add_track(slug: str, request: Request, name: str = Form(...), description: str = Form(""),
              viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    position = db.scalar(select(func.count()).select_from(Track).where(Track.event_id == event.id))
    db.add(Track(event_id=event.id, name=name.strip()[:120], description=description, position=position))
    _log(db, viewer, event, "track.added", request, entity_type="track", detail={"name": name})
    db.commit()
    return redirect(_url(event, "settings"), "Track added.")


@router.post("/events/{slug}/manage/tracks/{track_id}/delete")
def delete_track(slug: str, track_id: int, request: Request, viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    track = db.get(Track, track_id)
    if track is None or track.event_id != event.id:
        raise HTTPException(404, "No such track.")
    if db.scalar(select(Project.id).where(Project.track_id == track.id).limit(1)):
        raise HTTPException(409, "Projects are entered in this track, so it cannot be deleted.")
    db.delete(track)
    _log(db, viewer, event, "track.deleted", request, entity_type="track", entity_id=track_id,
         detail={"name": track.name})
    db.commit()
    return redirect(_url(event, "settings"), "Track deleted.")


@router.post("/events/{slug}/manage/prizes")
def add_prize(slug: str, request: Request, name: str = Form(...), reward: str = Form(""),
              track_id: str = Form(""), viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    tid = int(track_id) if track_id.isdigit() else None
    if tid and not db.scalar(select(Track.id).where(Track.id == tid, Track.event_id == event.id)):
        raise HTTPException(422, "That track is not part of this event.")
    position = db.scalar(select(func.count()).select_from(Prize).where(Prize.event_id == event.id))
    db.add(Prize(event_id=event.id, name=name.strip()[:120], reward=reward.strip()[:120], track_id=tid,
                 position=position))
    _log(db, viewer, event, "prize.added", request, entity_type="prize", detail={"name": name})
    db.commit()
    return redirect(_url(event, "settings"), "Prize added.")


@router.post("/events/{slug}/manage/prizes/{prize_id}/delete")
def delete_prize(slug: str, prize_id: int, request: Request, viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    prize = db.get(Prize, prize_id)
    if prize is None or prize.event_id != event.id:
        raise HTTPException(404, "No such prize.")
    db.delete(prize)
    _log(db, viewer, event, "prize.deleted", request, entity_type="prize", entity_id=prize_id)
    db.commit()
    return redirect(_url(event, "settings"), "Prize deleted.")


@router.post("/events/{slug}/manage/organizers")
def add_organizer(slug: str, request: Request, email: str = Form(...),
                  viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise HTTPException(404, "No account with that email. Ask them to register first.")
    if db.scalar(select(TeamMember.team_id).where(TeamMember.event_id == event.id,
                                                  TeamMember.user_id == user.id)):
        raise HTTPException(409, "That person is on a team in this event.")
    grant_role(db, user, event, Role.ORGANIZER)
    _log(db, viewer, event, "organizer.added", request, entity_type="user", entity_id=user.id)
    db.commit()
    return redirect(_url(event, "settings"), f"{user.name} is now an organizer.")


# --- rubric --------------------------------------------------------------------------

@router.get("/events/{slug}/manage/rubric")
def rubric_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    used = set(db.scalars(select(ScoreValue.criterion_id).join(RubricCriterion).where(
        RubricCriterion.event_id == event.id).distinct()))
    return render(request, "manage/rubric.html", event=event, tab="rubric", used=used)


@router.post("/events/{slug}/manage/rubric")
async def save_rubric(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                      db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    form = await request.form()
    changes = {}
    for c in event.criteria:
        raw = str(form.get(f"w{c.id}", c.weight))
        try:
            weight = float(raw)
        except ValueError:
            raise HTTPException(422, f"Weight for {c.label} must be a number.")
        if weight < 0:
            raise HTTPException(422, "Weights cannot be negative.")
        label = str(form.get(f"l{c.id}", c.label)).strip()[:120] or c.label
        if weight != c.weight or label != c.label:
            changes[c.key] = {"weight": [c.weight, weight], "label": [c.label, label]}
        c.weight, c.label = weight, label
    if all(c.weight == 0 for c in event.criteria):
        raise HTTPException(422, "At least one criterion needs a weight above zero.")
    _log(db, viewer, event, "rubric.updated", request, entity_type="rubric", detail=changes)
    db.commit()
    return redirect(_url(event, "rubric"), "Rubric saved. Results are recalculated with the new weights.")


@router.post("/events/{slug}/manage/rubric/add")
def add_criterion(slug: str, request: Request, label: str = Form(...), weight: float = Form(1.0),
                  min_value: int = Form(1), max_value: int = Form(5),
                  viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    if max_value <= min_value or weight < 0:
        raise HTTPException(422, "Max must be above min and weight cannot be negative.")
    key = slugify(label).replace("-", "_")[:60]
    if any(c.key == key for c in event.criteria):
        raise HTTPException(409, "A criterion with that name already exists.")
    db.add(RubricCriterion(event_id=event.id, key=key, label=label.strip()[:120], weight=weight,
                           min_value=min_value, max_value=max_value, position=len(event.criteria)))
    _log(db, viewer, event, "rubric.criterion_added", request, entity_type="rubric", detail={"key": key})
    db.commit()
    return redirect(_url(event, "rubric"), "Criterion added.")


@router.post("/events/{slug}/manage/rubric/{criterion_id}/delete")
def delete_criterion(slug: str, criterion_id: int, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    c = db.get(RubricCriterion, criterion_id)
    if c is None or c.event_id != event.id:
        raise HTTPException(404, "No such criterion.")
    if db.scalar(select(ScoreValue.score_id).where(ScoreValue.criterion_id == c.id).limit(1)):
        raise HTTPException(409, "Judges have already scored this criterion. Set its weight to 0 instead.")
    db.delete(c)
    _log(db, viewer, event, "rubric.criterion_deleted", request, entity_type="rubric", detail={"key": c.key})
    db.commit()
    return redirect(_url(event, "rubric"), "Criterion deleted.")


# --- judges ----------------------------------------------------------------------------

@router.get("/events/{slug}/manage/judges")
def judges_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    judges = assign_svc.judges_of(db, event)
    tracks_by_judge: dict[int, list[str]] = {}
    for uid, name in db.execute(select(JudgeTrack.user_id, Track.name).join(Track, Track.id == JudgeTrack.track_id)
                                .where(JudgeTrack.event_id == event.id).order_by(Track.position)):
        tracks_by_judge.setdefault(uid, []).append(name)
    invites = db.scalars(select(JudgeInvite).where(JudgeInvite.event_id == event.id)
                         .order_by(JudgeInvite.created_at.desc())).all()
    return render(request, "manage/judges.html", event=event, tab="judges", judges=judges,
                  tracks_by_judge=tracks_by_judge, load=assign_svc.judge_load(db, event), invites=invites,
                  base_url=str(request.base_url).rstrip("/"))


@router.post("/events/{slug}/manage/judges/invite")
async def invite_judge(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                       db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Enter the judge's email.")
    valid = {t.id for t in event.tracks}
    track_ids = [int(t) for t in form.getlist("tracks") if str(t).isdigit() and int(t) in valid]
    invite = JudgeInvite(event_id=event.id, email=email, token=new_token(16), track_ids=track_ids)
    db.add(invite)
    _log(db, viewer, event, "judge.invited", request, entity_type="invite", detail={"email": email})
    db.commit()
    return redirect(_url(event, "judges"),
                    "Invitation created. Copy its link from the list below and send it to the judge.")


# --- assignments ------------------------------------------------------------------------

@router.get("/events/{slug}/manage/assignments")
def assignments_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    projects = db.scalars(select(Project).where(Project.event_id == event.id,
                                                Project.status == ProjectStatus.SUBMITTED)
                          .options(selectinload(Project.track), selectinload(Project.team))
                          .order_by(Project.title)).all()
    rows = db.scalars(select(Assignment).where(Assignment.event_id == event.id)
                      .options(selectinload(Assignment.judge))).all()
    by_project: dict[int, list[Assignment]] = {}
    for a in rows:
        by_project.setdefault(a.project_id, []).append(a)
    return render(request, "manage/assignments.html", event=event, tab="assignments", projects=projects,
                  by_project=by_project, judges=assign_svc.judges_of(db, event),
                  seed=random.randint(1000, 9999))


@router.post("/events/{slug}/manage/assignments/auto")
def auto_assign(slug: str, request: Request, per_project: int = Form(3), seed: int = Form(1),
                viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    if not 1 <= per_project <= 20:
        raise HTTPException(422, "Reviews per project must be 1-20.")
    plan = assign_svc.plan_assignments(db, event, per_project, seed)
    assign_svc.apply_plan(db, event, plan)
    _log(db, viewer, event, "assignments.auto", request, entity_type="event", entity_id=event.slug,
         detail={"per_project": per_project, "seed": seed, "created": len(plan.created),
                 "cross_track": len(plan.cross_track), "unfilled": plan.unfilled})
    db.commit()
    msg = f"Created {len(plan.created)} assignments (seed {seed})."
    if plan.cross_track:
        msg += f" {len(plan.cross_track)} went outside the judge's tracks because the track ran out of judges."
    if plan.unfilled:
        msg += f" {len(plan.unfilled)} projects still need judges: invite more."
    return redirect(_url(event, "assignments"), msg)


@router.post("/events/{slug}/manage/assignments/add")
def add_assignment(slug: str, request: Request, project_id: int = Form(...), judge_id: int = Form(...),
                   viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    project = db.get(Project, project_id)
    if project is None or project.event_id != event.id:
        raise HTTPException(404, "No such project.")
    if judge_id not in {j.id for j in assign_svc.judges_of(db, event)}:
        raise HTTPException(422, "That person is not a judge in this event.")
    if judge_id in assign_svc.conflicted_judges(db, project):
        raise HTTPException(409, "That judge is on this project's team.")
    if db.scalar(select(Assignment.id).where(Assignment.judge_id == judge_id,
                                             Assignment.project_id == project_id)):
        return redirect(_url(event, "assignments"), "Already assigned.")
    db.add(Assignment(event_id=event.id, judge_id=judge_id, project_id=project_id, method="manual"))
    _log(db, viewer, event, "assignment.added", request, entity_type="project", entity_id=project_id,
         detail={"judge": judge_id})
    db.commit()
    return redirect(_url(event, "assignments"), "Assigned.")


@router.post("/events/{slug}/manage/assignments/{assignment_id}/delete")
def delete_assignment(slug: str, assignment_id: int, request: Request, viewer: Viewer = Depends(require_user),
                      db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    a = db.get(Assignment, assignment_id)
    if a is None or a.event_id != event.id:
        raise HTTPException(404, "No such assignment.")
    if a.status == AssignmentStatus.DONE:
        raise HTTPException(409, "This review is finished; it cannot be unassigned.")
    db.delete(a)
    _log(db, viewer, event, "assignment.removed", request, entity_type="project", entity_id=a.project_id,
         detail={"judge": a.judge_id})
    db.commit()
    return redirect(_url(event, "assignments"), "Assignment removed.")


# --- results -------------------------------------------------------------------------------

@router.get("/events/{slug}/manage/results")
def results_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    res = event_results(db, event)
    judge_names = {u.id: u for u in assign_svc.judges_of(db, event)}
    return render(request, "manage/results.html", event=event, tab="results", res=res, judge_names=judge_names)


from app.services.voting import voting_open


@router.post("/events/{slug}/manage/results/publish")
def publish(slug: str, request: Request, action: str = Form(...), viewer: Viewer = Depends(require_user),
            db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    if action == "publish":
        if event.submissions_open():
            raise HTTPException(409, "Submissions are still open. Publish after the deadline.")
        if voting_open(event):
            raise HTTPException(409, "Voting is still open. Publish after voting closes.")
        event.results_published_at = utcnow()
    elif action == "unpublish":
        event.results_published_at = None
    else:
        raise HTTPException(422, "Unknown action.")
    _log(db, viewer, event, f"results.{action}ed", request, entity_type="event", entity_id=event.slug)
    db.commit()

    if action == "publish":
        from app.services.webhooks import safe_fire
        safe_fire(db, event, "results.published", {"event": event.slug})

    return redirect(_url(event, "results"), "Results published." if action == "publish" else "Results hidden.")


# --- voting --------------------------------------------------------------------------------

@router.get("/events/{slug}/manage/voting")
def manage_voting_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                       db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    tally_list = tallies(db, event)
    signals = abuse_signals(db, event)
    votes = db.scalars(select(Vote).where(Vote.event_id == event.id)
                       .options(selectinload(Vote.user), selectinload(Vote.project))
                       .order_by(Vote.created_at.desc())).all()
    return render(request, "manage/voting.html", event=event, tab="voting",
                  tallies=tally_list, signals=signals, votes=votes)


@router.post("/events/{slug}/manage/votes/{vote_id}/void")
def void_vote(slug: str, vote_id: int, request: Request, reason: str = Form("organizer voided"),
              viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    vote = db.get(Vote, vote_id)
    if vote is None or vote.event_id != event.id:
        raise HTTPException(404, "No such vote.")
    voter_id = vote.user_id
    project_id = vote.project_id
    db.delete(vote)
    _log(db, viewer, event, "vote.voided", request, entity_type="vote", entity_id=vote_id,
         detail={"voter_id": voter_id, "project_id": project_id, "reason": reason})
    db.commit()
    return redirect(_url(event, "voting"), f"Vote #{vote_id} voided.")


# --- audit log ----------------------------------------------------------------------------

@router.get("/events/{slug}/manage/audit")
def audit_page(slug: str, request: Request, action: str = "", page: int = 1,
               viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = _manage(db, viewer, slug)
    q = select(AuditEntry).where(AuditEntry.event_id == event.id)
    if action:
        q = q.where(AuditEntry.action.startswith(action))
    page = max(page, 1)
    entries = db.scalars(q.options(selectinload(AuditEntry.actor)).order_by(AuditEntry.id.desc())
                         .offset((page - 1) * 100).limit(101)).all()
    actions = sorted({a.split(".")[0] for a in db.scalars(
        select(AuditEntry.action).where(AuditEntry.event_id == event.id).distinct())})
    return render(request, "manage/audit.html", event=event, tab="audit", entries=entries[:100],
                  has_more=len(entries) > 100, page=page, action=action, actions=actions)
