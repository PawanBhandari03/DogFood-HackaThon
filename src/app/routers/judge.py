"""The judge console. Every query here is filtered by the caller's user id;
an assignment id that belongs to someone else is refused and audited.

Public routes (no auth):
  GET /judges/{ref}/record          — signed participation record HTML page
  GET /judges/{ref}/record/verify   — JSON signature verifier
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, grant_role, require_user, team_membership
from app.db import get_db
from app.models import (
    Assignment,
    AssignmentStatus,
    Event,
    JudgeInvite,
    JudgeTrack,
    Project,
    Role,
    Score,
    ScoreValue,
    Team,
    TeamMember,
    User,
    utcnow,
)
from app.security import Forbidden
from app.services import audit
from app.services.deadline import ensure_judging_open
from app.services.judge_records import load_record, verify_signature
from app.web import redirect, render

router = APIRouter()


def _my_assignments(db: Session, viewer: Viewer, event_id: int | None = None) -> list[Assignment]:
    q = (select(Assignment).where(Assignment.judge_id == viewer.user.id,
                                  Assignment.event_id.in_(viewer.judge_event_ids))
         .options(selectinload(Assignment.project).selectinload(Project.team),
                  selectinload(Assignment.project).selectinload(Project.track),
                  selectinload(Assignment.score))
         .order_by(Assignment.event_id, Assignment.status.desc(), Assignment.id))
    if event_id is not None:
        q = q.where(Assignment.event_id == event_id)
    return list(db.scalars(q))


def _own_assignment(db: Session, viewer: Viewer, assignment_id: int) -> Assignment:
    a = db.scalar(select(Assignment).where(Assignment.id == assignment_id)
                  .options(selectinload(Assignment.project).selectinload(Project.team)
                           .selectinload(Team.members).selectinload(TeamMember.user),
                           selectinload(Assignment.project).selectinload(Project.track),
                           selectinload(Assignment.project).selectinload(Project.event)
                           .selectinload(Event.criteria),
                           selectinload(Assignment.score).selectinload(Score.values)))
    if a is None:
        raise HTTPException(404, "No such assignment.")
    if a.judge_id != viewer.user.id or not viewer.is_judge(a.event_id):
        raise Forbidden("not_your_assignment", "This assignment belongs to another judge.",
                        audit=True, event_id=a.event_id, actor_id=viewer.user.id,
                        detail={"assignment": a.id})
    return a


@router.get("/judge")
def console(request: Request, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    if not viewer.judge_event_ids:
        raise Forbidden("judge_only", "You are not a judge in any event.", actor_id=viewer.user.id)
    events = db.scalars(select(Event).where(Event.id.in_(viewer.judge_event_ids))
                        .order_by(Event.submissions_close_at.desc())).all()
    rows = _my_assignments(db, viewer)
    by_event = {e.id: [a for a in rows if a.event_id == e.id] for e in events}
    return render(request, "judge_console.html", events=events, by_event=by_event)


@router.get("/judge/assignments/{assignment_id}")
def score_form(assignment_id: int, request: Request, viewer: Viewer = Depends(require_user),
               db: Session = Depends(get_db)):
    a = _own_assignment(db, viewer, assignment_id)
    siblings = _my_assignments(db, viewer, a.event_id)
    ids = [s.id for s in siblings]
    i = ids.index(a.id)
    values = {v.criterion_id: v.value for v in a.score.values} if a.score else {}
    return render(request, "score_form.html", a=a, event=a.project.event, project=a.project,
                  criteria=a.project.event.criteria, values=values,
                  prev_id=ids[i - 1] if i > 0 else None, next_id=ids[i + 1] if i + 1 < len(ids) else None,
                  position=i + 1, total=len(ids), done=sum(1 for s in siblings if s.status == "done"),
                  error=None)


@router.post("/judge/assignments/{assignment_id}")
async def save_score(assignment_id: int, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    a = _own_assignment(db, viewer, assignment_id)
    event = a.project.event
    ensure_judging_open(event, actor_id=viewer.user.id)
    form = await request.form()
    values: dict[int, int] = {}
    for c in event.criteria:
        raw = str(form.get(f"c{c.id}", "")).strip()
        if not raw.lstrip("-").isdigit() or not (c.min_value <= int(raw) <= c.max_value):
            raise HTTPException(422, f"Give {c.label} a whole number from {c.min_value} to {c.max_value}.")
        values[c.id] = int(raw)
    comment = str(form.get("comment", "")).strip()[:5000]

    created = a.score is None
    if created:
        a.score = Score(assignment_id=a.id, comment=comment)
    a.score.comment = comment
    a.score.updated_at = utcnow()
    existing = {v.criterion_id: v for v in a.score.values}
    for cid, value in values.items():
        if cid in existing:
            existing[cid].value = value
        else:
            a.score.values.append(ScoreValue(criterion_id=cid, value=value))
    a.status = AssignmentStatus.DONE
    audit.record(db, "score.submitted" if created else "score.updated", actor=viewer.user,
                 event_id=event.id, entity_type="assignment", entity_id=a.id,
                 detail={"project": a.project.external_id or a.project.id}, request=request)
    db.commit()

    from app.services.webhooks import safe_fire
    safe_fire(db, event, "score.submitted", {
        "project_id": a.project.external_id or str(a.project.id),
        "judge_id": viewer.user.handle,
    })

    if form.get("then") == "next":
        pending = [s for s in _my_assignments(db, viewer, event.id) if s.status == AssignmentStatus.PENDING]
        if pending:
            return redirect(f"/judge/assignments/{pending[0].id}", "Saved. Next project.")
        return redirect("/judge", "Saved. That was your last pending review.")
    return redirect(f"/judge/assignments/{a.id}", "Score saved.")


# --- invitations --------------------------------------------------------------

def _invite(db: Session, token: str) -> JudgeInvite:
    invite = db.scalar(select(JudgeInvite).where(JudgeInvite.token == token)
                       .options(selectinload(JudgeInvite.event)))
    if invite is None:
        raise HTTPException(404, "This judge invitation is not valid.")
    return invite


@router.get("/judge/invite/{token}")
def invite_page(token: str, request: Request, viewer: Viewer = Depends(require_user),
                db: Session = Depends(get_db)):
    invite = _invite(db, token)
    return render(request, "judge_invite.html", invite=invite, event=invite.event)


@router.post("/judge/invite/{token}")
def accept_invite(token: str, request: Request, viewer: Viewer = Depends(require_user),
                  db: Session = Depends(get_db)):
    invite = _invite(db, token)
    event = invite.event
    if invite.accepted_at:
        return redirect("/judge", "This invitation was already used.")
    if invite.email != viewer.user.email:
        raise Forbidden("invite_email_mismatch",
                        f"This invitation is for {invite.email}. Log in with that account to accept it.",
                        audit=True, event_id=event.id, actor_id=viewer.user.id)
    if team_membership(db, viewer, event):
        raise Forbidden("conflict_of_interest", "You are on a team in this event, so you cannot judge it.",
                        audit=True, event_id=event.id, actor_id=viewer.user.id)
    grant_role(db, viewer.user, event, Role.JUDGE)
    for track_id in invite.track_ids or []:
        if not db.get(JudgeTrack, (event.id, viewer.user.id, track_id)):
            db.add(JudgeTrack(event_id=event.id, user_id=viewer.user.id, track_id=track_id))
    invite.accepted_at = utcnow()
    invite.accepted_by_id = viewer.user.id
    audit.record(db, "judge.invite_accepted", actor=viewer.user, event_id=event.id,
                 entity_type="invite", entity_id=invite.id, request=request)
    db.commit()
    return redirect("/judge", f"You are now a judge for {event.name}.")


# --- public judge participation records (T4-6) --------------------------------

def _lookup_judge(db: Session, ref: str) -> User:
    """Resolve a judge by external_id or numeric id.  Raises 404 if not found."""
    user = db.scalar(
        select(User).where(
            (User.external_id == ref) | (User.id == (int(ref) if ref.isdigit() else -1))
        )
    )
    if user is None:
        raise HTTPException(404, "No such judge.")
    return user


@router.get("/judges/{ref}/record", include_in_schema=False)
def judge_record_page(ref: str, request: Request, db: Session = Depends(get_db)):
    """Public HTML page showing a judge's signed participation record.

    No login required — records are designed to be shared and independently
    verified.  Never includes score values, comments, or project titles.
    """
    from urllib.parse import quote
    user = _lookup_judge(db, ref)
    record = load_record(db, user)
    return render(request, "judge_record.html", record=record, sig_encoded=quote(record.signature))


@router.get("/judges/{ref}/record/verify", include_in_schema=False)
def judge_record_verify(ref: str, sig: str, db: Session = Depends(get_db)):
    """Verify a judge-record signature.

    Recomputes the HMAC-SHA256 signature from current data and compares it
    with *sig*.  Returns ``{"valid": true}`` when they match.  Anyone can
    call this endpoint without logging in — that is what makes the records
    independently verifiable.

    Responds 200 in both cases (valid and invalid) so callers can always
    parse the JSON.
    """
    user = _lookup_judge(db, ref)
    record = load_record(db, user)
    valid = verify_signature(record.judge_ref, record.events, sig)
    return JSONResponse({"valid": valid, "judge": user.name, "judge_ref": ref})

