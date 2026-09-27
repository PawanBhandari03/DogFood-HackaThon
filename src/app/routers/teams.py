"""Participant pages: form a team, invite by link, draft and submit a project.

Every write calls ensure_submissions_open first: after the deadline nothing
about a team or its project can change.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, grant_role, require_user, team_membership
from app.db import get_db
from app.models import Event, ProjectStatus, Role, Team, TeamMember
from app.security import Forbidden, new_token
from app.services import audit, projects as project_svc
from app.services.deadline import ensure_submissions_open
from app.web import event_or_404, redirect, render

router = APIRouter()


def _team_url(event: Event) -> str:
    return f"/events/{event.slug}/team"


def _no_judges(viewer: Viewer, event: Event) -> None:
    if viewer.is_judge(event) or viewer.has(event, Role.ORGANIZER):
        raise Forbidden("conflict_of_interest",
                        "Judges and organizers of an event cannot compete in it.",
                        event_id=event.id, actor_id=viewer.user.id)


def _my_team(db: Session, viewer: Viewer, event: Event) -> Team:
    member = team_membership(db, viewer, event)
    if member is None:
        raise HTTPException(404, "You are not in a team for this event.")
    return member.team


def _require_lead(db: Session, viewer: Viewer, team: Team) -> None:
    lead = db.scalar(select(TeamMember.is_lead).where(
        TeamMember.team_id == team.id, TeamMember.user_id == viewer.user.id))
    if not lead:
        raise Forbidden("lead_only", "Only the team lead can do this.", actor_id=viewer.user.id)


@router.get("/events/{slug}/team")
def team_page(slug: str, request: Request, viewer: Viewer = Depends(require_user),
              db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    member = team_membership(db, viewer, event)
    team = project = None
    if member:
        team = db.scalar(select(Team).where(Team.id == member.team_id)
                         .options(selectinload(Team.members).selectinload(TeamMember.user)))
        project = project_svc.live_project(db, team)
    return render(request, "team.html", event=event, member=member, team=team, project=project,
                  invite_url=str(request.base_url).rstrip("/") + f"/join/{team.invite_code}" if team else None)


@router.post("/events/{slug}/team")
def create_team(slug: str, request: Request, name: str = Form(...),
                viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    ensure_submissions_open(event, actor_id=viewer.user.id)
    _no_judges(viewer, event)
    if team_membership(db, viewer, event):
        return redirect(_team_url(event), "You are already in a team for this event.")
    name = name.strip()[:120]
    if not name:
        raise HTTPException(422, "Give your team a name.")
    team = Team(event_id=event.id, name=name, invite_code=new_token(12))
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=viewer.user.id, event_id=event.id, is_lead=True))
    grant_role(db, viewer.user, event, Role.PARTICIPANT)
    audit.record(db, "team.created", actor=viewer.user, event_id=event.id, entity_type="team",
                 entity_id=team.id, detail={"name": name}, request=request)
    db.commit()
    return redirect(_team_url(event), f"Team {name} created. Share the invite link with your teammates.")


def _team_by_code(db: Session, code: str) -> Team:
    team = db.scalar(select(Team).where(Team.invite_code == code)
                     .options(selectinload(Team.members).selectinload(TeamMember.user),
                              selectinload(Team.event)))
    if team is None:
        raise HTTPException(404, "This invite link is not valid. Ask your team lead for a new one.")
    return team


@router.get("/join/{code}")
def join_page(code: str, request: Request, viewer: Viewer = Depends(require_user),
              db: Session = Depends(get_db)):
    team = _team_by_code(db, code)
    current = team_membership(db, viewer, team.event)
    return render(request, "join.html", team=team, event=team.event, current=current)


@router.post("/join/{code}")
def join_team(code: str, request: Request, viewer: Viewer = Depends(require_user),
              db: Session = Depends(get_db)):
    team = _team_by_code(db, code)
    event = team.event
    ensure_submissions_open(event, actor_id=viewer.user.id)
    _no_judges(viewer, event)
    current = team_membership(db, viewer, event)
    if current:
        msg = "You are already in this team." if current.team_id == team.id else \
            "You are already in another team for this event. Leave it first."
        return redirect(_team_url(event), msg)
    size = db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id))
    if size >= event.max_team_size:
        raise Forbidden("team_full", f"This team already has {size} members, the event maximum.",
                        event_id=event.id, actor_id=viewer.user.id)
    db.add(TeamMember(team_id=team.id, user_id=viewer.user.id, event_id=event.id, is_lead=False))
    grant_role(db, viewer.user, event, Role.PARTICIPANT)
    audit.record(db, "team.joined", actor=viewer.user, event_id=event.id, entity_type="team",
                 entity_id=team.id, request=request)
    db.commit()
    return redirect(_team_url(event), f"You joined {team.name}.")


@router.post("/events/{slug}/team/leave")
def leave_team(slug: str, request: Request, viewer: Viewer = Depends(require_user),
               db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    ensure_submissions_open(event, actor_id=viewer.user.id)
    team = _my_team(db, viewer, event)
    members = db.scalars(select(TeamMember).where(TeamMember.team_id == team.id)
                         .order_by(TeamMember.joined_at)).all()
    me = next(m for m in members if m.user_id == viewer.user.id)
    others = [m for m in members if m.user_id != viewer.user.id]
    project = project_svc.live_project(db, team)
    if not others and project and project.status == ProjectStatus.SUBMITTED:
        raise Forbidden("last_member", "You are the last member of a team with a submitted project. "
                        "Withdraw the project first.", actor_id=viewer.user.id)
    db.delete(me)
    if others and me.is_lead:
        others[0].is_lead = True
    if not others:
        db.delete(team)
    audit.record(db, "team.left", actor=viewer.user, event_id=event.id, entity_type="team",
                 entity_id=team.id, request=request)
    db.commit()
    return redirect(f"/events/{event.slug}", "You left the team.")


@router.post("/events/{slug}/team/rotate-invite")
def rotate_invite(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                  db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    team = _my_team(db, viewer, event)
    _require_lead(db, viewer, team)
    team.invite_code = new_token(12)
    audit.record(db, "team.invite_rotated", actor=viewer.user, event_id=event.id, entity_type="team",
                 entity_id=team.id, request=request)
    db.commit()
    return redirect(_team_url(event), "New invite link created. The old one no longer works.")


@router.post("/events/{slug}/team/remove/{user_id}")
def remove_member(slug: str, user_id: int, request: Request, viewer: Viewer = Depends(require_user),
                  db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    ensure_submissions_open(event, actor_id=viewer.user.id)
    team = _my_team(db, viewer, event)
    _require_lead(db, viewer, team)
    if user_id == viewer.user.id:
        raise HTTPException(422, "Use 'Leave team' to remove yourself.")
    member = db.get(TeamMember, (team.id, user_id))
    if member is None:
        raise HTTPException(404, "That person is not in your team.")
    db.delete(member)
    audit.record(db, "team.member_removed", actor=viewer.user, event_id=event.id, entity_type="team",
                 entity_id=team.id, detail={"removed_user": user_id}, request=request)
    db.commit()
    return redirect(_team_url(event), "Member removed.")


@router.post("/events/{slug}/project")
def save_project(slug: str, request: Request, title: str = Form(""), summary: str = Form(""),
                 description: str = Form(""), repo_url: str = Form(""), demo_url: str = Form(""),
                 track_id: str = Form(""), viewer: Viewer = Depends(require_user),
                 db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    fields = project_svc.ProjectFields(title, summary, description, repo_url, demo_url,
                                       int(track_id) if track_id.isdigit() else None)
    project_svc.save_project(db, viewer, event, fields, request)
    db.commit()
    return redirect(_team_url(event), "Project saved.")


@router.post("/events/{slug}/project/submit")
def submit_project(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                   db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    project_svc.set_status(db, viewer, event, ProjectStatus.SUBMITTED, request)
    db.commit()
    return redirect(_team_url(event), "Submitted. You can keep editing until the deadline.")


@router.post("/events/{slug}/project/unsubmit")
def unsubmit_project(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    project_svc.set_status(db, viewer, event, ProjectStatus.DRAFT, request)
    db.commit()
    return redirect(_team_url(event), "Back to draft. It will not be judged unless you submit again.")


@router.post("/events/{slug}/project/withdraw")
def withdraw_project(slug: str, request: Request, viewer: Viewer = Depends(require_user),
                     db: Session = Depends(get_db)):
    event = event_or_404(db, slug)
    project_svc.set_status(db, viewer, event, ProjectStatus.WITHDRAWN, request)
    db.commit()
    return redirect(_team_url(event), "Project withdrawn.")
