"""Login, logout, registration and the personal dashboard."""

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, get_viewer, require_user
from app.config import SESSION_COOKIE, settings
from app.db import get_db
from app.models import Assignment, AuthSession, Event, Project, ProjectStatus, Team, TeamMember, User
from app.security import create_session, hash_password, hash_token, verify_password
from app.services import audit
from app.services.ratelimit import login_limiter
from app.services.assignment import judge_load
from app.web import redirect, render

router = APIRouter()


def _safe_next(next_url: str) -> str:
    # Only same-site paths, never "//evil.example".
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/me"


def _login_response(db: Session, user: User, next_url: str, label: str = "login"):
    token = create_session(db, user, label=label)
    db.commit()
    response = redirect(_safe_next(next_url))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=settings.session_days * 86400)
    return response


@router.get("/login")
def login_form(request: Request, next: str = "/me", viewer: Viewer = Depends(get_viewer)):
    return render(request, "login.html", next=next, error=None, email="")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/me"),
          viewer: Viewer = Depends(get_viewer), db: Session = Depends(get_db)):
    email = email.strip().lower()
    key = f"{request.client.host if request.client else '-'}|{email}"
    if not login_limiter.allow(key):
        return render(request, "login.html", status_code=429, next=next, email=email,
                      error="Too many attempts. Wait a minute and try again.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(user.password_hash, password):
        audit.record(db, "login.failed", actor=user, detail={"email": email}, request=request)
        db.commit()
        return render(request, "login.html", status_code=400, next=next, email=email,
                      error="That email and password do not match.")
    audit.record(db, "login", actor=user, request=request)
    return _login_response(db, user, next)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
    response = redirect("/", "Logged out.")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/register")
def register_form(request: Request, next: str = "/me", viewer: Viewer = Depends(get_viewer)):
    return render(request, "register.html", next=next, error=None, name="", email="")


@router.post("/register")
def register(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...),
             next: str = Form("/me"), viewer: Viewer = Depends(get_viewer), db: Session = Depends(get_db)):
    name, email = name.strip(), email.strip().lower()
    error = None
    if not name or "@" not in email:
        error = "Enter your name and a valid email."
    elif len(password) < 8:
        error = "Use at least 8 characters for the password."
    elif db.scalar(select(User.id).where(User.email == email)):
        error = "An account with that email already exists. Log in instead."
    if error:
        return render(request, "register.html", status_code=400, next=next, error=error, name=name, email=email)
    user = User(email=email, name=name[:200], password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit.record(db, "user.registered", actor=user, request=request)
    return _login_response(db, user, next, label="register")


@router.get("/me")
def dashboard(request: Request, viewer: Viewer = Depends(require_user), db: Session = Depends(get_db)):
    user = viewer.user
    memberships = db.scalars(
        select(TeamMember).where(TeamMember.user_id == user.id)
        .options(selectinload(TeamMember.team).selectinload(Team.event))).all()
    team_projects = {}
    for m in memberships:
        team_projects[m.team_id] = db.scalar(select(Project).where(
            Project.team_id == m.team_id, Project.status != ProjectStatus.WITHDRAWN,
            Project.duplicate_of_id.is_(None)))
    judging = []
    for event in db.scalars(select(Event).where(Event.id.in_(viewer.judge_event_ids))):
        done, total = judge_load(db, event).get(user.id, (0, 0))
        judging.append((event, done, total))
    organizing = db.scalars(select(Event).where(Event.id.in_(viewer.organizer_event_ids))
                            .order_by(Event.submissions_close_at.desc())).all()
    if viewer.is_admin:
        organizing = db.scalars(select(Event).order_by(Event.submissions_close_at.desc())).all()
    return render(request, "me.html", memberships=memberships, team_projects=team_projects,
                  judging=judging, organizing=organizing)
