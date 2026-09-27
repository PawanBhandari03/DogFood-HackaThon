"""Instance administration: who is an admin and who may create events."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import Viewer, require_admin
from app.db import get_db
from app.models import AuditEntry, User
from app.services import audit
from app.web import redirect, render

router = APIRouter()


@router.get("/admin")
def admin_page(request: Request, q: str = "", viewer: Viewer = Depends(require_admin),
               db: Session = Depends(get_db)):
    query = select(User).order_by(User.is_admin.desc(), User.can_create_events.desc(), User.name)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(User.name.ilike(like), User.email.ilike(like)))
    users = db.scalars(query.limit(200)).all()
    total = db.scalar(select(func.count()).select_from(User))
    recent = db.scalars(select(AuditEntry).where(AuditEntry.event_id.is_(None))
                        .options(selectinload(AuditEntry.actor)).order_by(AuditEntry.id.desc()).limit(30)).all()
    return render(request, "admin.html", users=users, total=total, q=q, recent=recent)


@router.post("/admin/users/{user_id}")
def update_user(user_id: int, request: Request, flag: str = Form(...), value: str = Form(...),
                viewer: Viewer = Depends(require_admin), db: Session = Depends(get_db)):
    if flag not in ("is_admin", "can_create_events"):
        raise HTTPException(422, "Unknown setting.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such user.")
    on = value == "1"
    if flag == "is_admin" and not on and user.id == viewer.user.id:
        raise HTTPException(409, "You cannot remove your own admin rights.")
    setattr(user, flag, on)
    audit.record(db, f"admin.{flag}.{'granted' if on else 'revoked'}", actor=viewer.user,
                 entity_type="user", entity_id=user.id, detail={"email": user.email}, request=request)
    db.commit()
    return redirect("/admin", f"Updated {user.name}.")
