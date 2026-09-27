"""CSV and JSON exports. Every export is organizer-only; the routes check that."""

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Assignment,
    Event,
    EventRole,
    JudgeTrack,
    Project,
    Role,
    Score,
    Team,
    TeamMember,
    Track,
    User,
)
from app.services.scoring import event_results, load_sheets, normalize


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}"


def results_csv(db: Session, event: Event) -> str:
    res = event_results(db, event)
    rows = []
    for r in res.rows:
        p = res.projects[r.project_id]
        rows.append([r.rank or "", p.external_id or p.id, p.title, p.team.name,
                     p.track.name if p.track else "", r.n, _fmt(r.raw_mean), _fmt(r.normalized_mean),
                     _fmt(r.stderr), r.raw_rank or "", "" if r.movement is None else r.movement])
    for p in res.excluded:
        reason = "duplicate" if p.duplicate_of_id else p.status
        rows.append([f"excluded:{reason}", p.external_id or p.id, p.title, p.team.name,
                     p.track.name if p.track else "", "", "", "", "", "", ""])
    return _csv(["rank", "project_id", "title", "team", "track", "reviews", "raw_score",
                 "normalized_score", "std_error", "raw_rank", "rank_change"], rows)


def scores_csv(db: Session, event: Event) -> str:
    criteria, pairs = load_sheets(db, event)
    normalize([s for _, s in pairs], event.shrinkage_k)
    sheet_by_assignment = {a.id: s for a, s in pairs}
    assignments = db.scalars(
        select(Assignment).where(Assignment.event_id == event.id)
        .options(selectinload(Assignment.judge), selectinload(Assignment.project),
                 selectinload(Assignment.score).selectinload(Score.values))
        .order_by(Assignment.project_id, Assignment.judge_id)).all()
    rows = []
    for a in assignments:
        values = {v.criterion_id: v.value for v in a.score.values} if a.score else {}
        sheet = sheet_by_assignment.get(a.id)
        rows.append([a.judge.handle, a.judge.name, a.project.external_id or a.project.id,
                     a.project.title, a.status, a.method,
                     *[values.get(c.id, "") for c in criteria],
                     _fmt(sheet.raw if sheet else None), _fmt(sheet.normalized if sheet else None),
                     a.score.comment if a.score else ""])
    return _csv(["judge_id", "judge_name", "project_id", "project_title", "status", "assigned_by",
                 *[c.key for c in criteria], "weighted_raw", "normalized", "comment"], rows)


def projects_csv(db: Session, event: Event) -> str:
    projects = db.scalars(
        select(Project).where(Project.event_id == event.id)
        .options(selectinload(Project.team).selectinload(Team.members).selectinload(TeamMember.user),
                 selectinload(Project.track), selectinload(Project.duplicate_of))
        .order_by(Project.id)).all()
    rows = [[p.external_id or p.id, p.title, p.status, p.team.external_id or p.team.id, p.team.name,
             " ".join(m.user.email for m in p.team.members), p.track.name if p.track else "",
             p.repo_url, p.demo_url, p.submitted_at.isoformat() if p.submitted_at else "",
             (p.duplicate_of.external_id or p.duplicate_of.id) if p.duplicate_of else "", p.summary]
            for p in projects]
    return _csv(["project_id", "title", "status", "team_id", "team_name", "members", "track",
                 "repo_url", "demo_url", "submitted_at", "duplicate_of", "summary"], rows)


def event_json(db: Session, event: Event) -> dict:
    """The whole event in the fixtures.json shape, so it can be re-imported."""
    def ext(obj, prefix):
        return obj.external_id or f"{prefix}_{obj.id}"

    teams = db.scalars(select(Team).where(Team.event_id == event.id)
                       .options(selectinload(Team.members).selectinload(TeamMember.user))).all()
    projects = db.scalars(select(Project).where(Project.event_id == event.id)
                          .options(selectinload(Project.team), selectinload(Project.track))).all()
    judge_users = db.scalars(select(User).join(EventRole, EventRole.user_id == User.id).where(
        EventRole.event_id == event.id, EventRole.role == Role.JUDGE).order_by(User.id)).all()
    track_of: dict[int, list[str]] = {}
    for uid, t in db.execute(select(JudgeTrack.user_id, Track).join(Track, Track.id == JudgeTrack.track_id)
                             .where(JudgeTrack.event_id == event.id)):
        track_of.setdefault(uid, []).append(ext(t, "trk"))
    assignments = db.scalars(select(Assignment).where(Assignment.event_id == event.id)
                             .options(selectinload(Assignment.judge), selectinload(Assignment.project),
                                      selectinload(Assignment.score).selectinload(Score.values))).all()
    crit_key = {c.id: c.key for c in event.criteria}
    return {
        "event": {"id": ext(event, "evt"), "name": event.name,
                  "submissions_close": event.submissions_close_at.isoformat().replace("+00:00", "Z")},
        "tracks": [{"id": ext(t, "trk"), "name": t.name} for t in event.tracks],
        "judges": [{"id": u.handle if u.external_id else f"jdg_{u.id}", "name": u.name, "email": u.email,
                    "tracks": track_of.get(u.id, [])}
                   for u in judge_users],
        "teams": [{"id": ext(t, "tm"), "name": t.name, "members": [m.user.email for m in t.members]}
                  for t in teams],
        "projects": [{"id": ext(p, "prj"), "team": ext(p.team, "tm"),
                      "track": ext(p.track, "trk") if p.track else None, "title": p.title,
                      "summary": p.summary, "repo_url": p.repo_url, "status": p.status,
                      "submitted_at": p.submitted_at.isoformat().replace("+00:00", "Z") if p.submitted_at else None}
                     for p in projects],
        "scores": [{"judge": a.judge.external_id or f"jdg_{a.judge.id}", "project": ext(a.project, "prj"),
                    "criteria": {crit_key[v.criterion_id]: v.value for v in a.score.values},
                    "comment": a.score.comment}
                   for a in assignments if a.score],
    }
