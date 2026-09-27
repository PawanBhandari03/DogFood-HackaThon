"""Template rendering, flash messages and small helpers shared by page routes."""

from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Event, utcnow
from app.util import fmt_dt, input_dt

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["dt"] = fmt_dt
templates.env.filters["input_dt"] = input_dt
templates.env.filters["num"] = lambda v, d=1: "—" if v is None else f"{v:.{d}f}"
templates.env.globals["demo_mode"] = settings.is_demo

FLASH_COOKIE = "flash"


def render(request: Request, template: str, /, status_code: int = 200, **ctx) -> HTMLResponse:
    raw_flash = request.cookies.get(FLASH_COOKIE)
    flash = unquote(raw_flash) if raw_flash else None
    ctx.setdefault("viewer", getattr(request.state, "viewer", None))
    response = templates.TemplateResponse(
        request, template, {"now": utcnow(), "flash": flash, **ctx}, status_code=status_code)
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect(url: str, flash: str | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash:
        response.set_cookie(FLASH_COOKIE, quote(flash), max_age=30, httponly=True, samesite="lax")
    return response


def event_or_404(db: Session, slug: str) -> Event:
    event = db.scalar(select(Event).where((Event.slug == slug) | (Event.external_id == slug)))
    if event is None:
        raise HTTPException(404, "No such event.")
    return event
