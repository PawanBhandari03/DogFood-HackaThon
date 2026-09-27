from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import SessionLocal
from app.routers import account, admin, api, judge, organizer, public, teams, voting
from app.security import Forbidden, NotAuthenticated
from app.services import audit
from app.web import render

app = FastAPI(
    title="Binary Builders · DOGFOOD portal",
    description="Self-hostable hackathon submission and judging platform.",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

# organizer first: its /events/new must win over public's /events/{slug}.
for module in (organizer, account, admin, teams, judge, voting, public, api):
    app.include_router(module.router)


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(NotAuthenticated)
async def not_authenticated(request: Request, exc: NotAuthenticated):
    if _wants_json(request):
        return JSONResponse({"error": "not_authenticated", "message": "Log in first."}, status_code=401)
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(Forbidden)
async def forbidden(request: Request, exc: Forbidden):
    if exc.audit:
        with SessionLocal() as db:
            audit.record(db, f"denied.{exc.code}", actor_id=exc.actor_id, event_id=exc.event_id,
                         entity_type="request", entity_id=request.url.path[:64],
                         detail={"method": request.method, "path": str(request.url.path), **exc.detail},
                         request=request)
            db.commit()
    if _wants_json(request):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=403)
    return render(request, "error.html", status_code=403, title="Not allowed", message=exc.message)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse({"error": "http_error", "message": exc.detail}, status_code=exc.status_code)
    title = {404: "Not found"}.get(exc.status_code, "Something went wrong")
    return render(request, "error.html", status_code=exc.status_code, title=title, message=exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if _wants_json(request):
        return JSONResponse({"error": "invalid_request", "details": exc.errors()}, status_code=422)
    return render(request, "error.html", status_code=422, title="Check the form",
                  message="Some fields were missing or invalid.")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
