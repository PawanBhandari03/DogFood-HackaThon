"""Import an event in the fixtures.json shape. Idempotent: rows are matched
on their external ids, so running it twice changes nothing.

Decisions (DATA-MODEL.md explains them):
- The fixture has no start date. We use submissions_close minus 72 hours,
  which is earlier than every fixture submission.
- Rubric criteria are the keys found in the scores, weight 1, range 1-5.
- A team's second project is stored with duplicate_of pointing at its first
  and left out of rankings until an organizer decides (fixture: prj_41).
- Every fixture score becomes a finished assignment. Projects below the
  event's reviews_per_project then get pending assignments, which is what
  the organizer's progress view shows as outstanding work.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import grant_role
from app.models import (
    Assignment,
    AssignmentStatus,
    Event,
    JudgeTrack,
    Project,
    ProjectStatus,
    Role,
    RubricCriterion,
    Score,
    ScoreValue,
    Team,
    TeamMember,
    Track,
    User,
)
from app.security import new_token
from app.services import assignment as assign_svc
from app.util import slugify


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class ImportReport:
    event: Event | None = None
    created: dict[str, int] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def bump(self, kind: str, n: int = 1) -> None:
        self.created[kind] = self.created.get(kind, 0) + n


def _user(db: Session, email: str, name: str, external_id: str | None,
          password_hash: str | None, report: ImportReport) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name, external_id=external_id, password_hash=password_hash)
        db.add(user)
        db.flush()
        report.bump("users")
    elif external_id and not user.external_id:
        user.external_id = external_id
    return user


def _name_from_email(email: str) -> str:
    return email.split("@")[0]


def import_event(db: Session, data: dict, *, password_hash: str | None = None,
                 top_up_seed: int = 2026) -> ImportReport:
    report = ImportReport()
    ev = data["event"]
    event = db.scalar(select(Event).where(Event.external_id == ev["id"]))
    is_new = event is None
    close = parse_ts(ev["submissions_close"])
    if event is None:
        event = Event(
            external_id=ev["id"],
            slug=slugify(ev["name"]),
            name=ev["name"],
            tagline="Imported from fixtures.json",
            starts_at=close - timedelta(hours=72),
            submissions_close_at=close,
        )
        db.add(event)
        db.flush()
        report.bump("events")
    report.event = event

    tracks: dict[str, Track] = {}
    for i, t in enumerate(data.get("tracks", [])):
        track = db.scalar(select(Track).where(Track.event_id == event.id, Track.external_id == t["id"]))
        if track is None:
            track = Track(event_id=event.id, external_id=t["id"], name=t["name"], position=i)
            db.add(track)
            report.bump("tracks")
        tracks[t["id"]] = track
    db.flush()

    keys: list[str] = []
    for s in data.get("scores", []):
        for k in s.get("criteria", {}):
            if k not in keys:
                keys.append(k)
    criteria: dict[str, RubricCriterion] = {}
    for i, key in enumerate(keys):
        c = db.scalar(select(RubricCriterion).where(
            RubricCriterion.event_id == event.id, RubricCriterion.key == key))
        if c is None:
            c = RubricCriterion(event_id=event.id, key=key, label=key.replace("_", " ").capitalize(),
                                weight=1.0, min_value=1, max_value=5, position=i)
            db.add(c)
            report.bump("criteria")
        criteria[key] = c
    db.flush()

    judges: dict[str, User] = {}
    for j in data.get("judges", []):
        user = _user(db, j["email"], j["name"], j["id"], password_hash, report)
        judges[j["id"]] = user
        if grant_role(db, user, event, Role.JUDGE):
            report.bump("judge roles")
        for tid in j.get("tracks", []):
            track = tracks.get(tid)
            if track and not db.get(JudgeTrack, (event.id, user.id, track.id)):
                db.add(JudgeTrack(event_id=event.id, user_id=user.id, track_id=track.id))
    db.flush()

    teams: dict[str, Team] = {}
    for t in data.get("teams", []):
        team = db.scalar(select(Team).where(Team.event_id == event.id, Team.external_id == t["id"]))
        if team is None:
            team = Team(event_id=event.id, external_id=t["id"], name=t["name"], invite_code=new_token(12))
            db.add(team)
            db.flush()
            report.bump("teams")
        teams[t["id"]] = team
        for i, email in enumerate(t.get("members", [])):
            user = _user(db, email, _name_from_email(email), None, password_hash, report)
            existing = db.scalar(select(TeamMember).where(
                TeamMember.event_id == event.id, TeamMember.user_id == user.id))
            if existing is None:
                db.add(TeamMember(team_id=team.id, user_id=user.id, event_id=event.id, is_lead=(i == 0)))
            grant_role(db, user, event, Role.PARTICIPANT)
    db.flush()

    projects: dict[str, Project] = {}
    # Oldest first, so the first submission is the original and later ones
    # from the same team are the ones flagged.
    for p in sorted(data.get("projects", []), key=lambda p: p.get("submitted_at", "")):
        project = db.scalar(select(Project).where(
            Project.event_id == event.id, Project.external_id == p["id"]))
        if project is None:
            team = teams[p["team"]]
            first = db.scalar(select(Project).where(
                Project.team_id == team.id,
                Project.status != ProjectStatus.WITHDRAWN,
                Project.duplicate_of_id.is_(None)))
            track = tracks.get(p.get("track"))
            project = Project(
                event_id=event.id, team_id=team.id, external_id=p["id"],
                track_id=track.id if track else None,
                title=p["title"], summary=p.get("summary", ""), repo_url=p.get("repo_url", ""),
                status=ProjectStatus.SUBMITTED,
                submitted_at=parse_ts(p["submitted_at"]) if p.get("submitted_at") else None,
                duplicate_of_id=first.id if first else None,
            )
            db.add(project)
            db.flush()
            report.bump("projects")
            if first:
                same_repo = first.repo_url and first.repo_url == project.repo_url
                report.duplicates.append(
                    f"{p['id']} is a second submission by team {p['team']} "
                    f"(first: {first.external_id}{', same repo' if same_repo else ''})")
        projects[p["id"]] = project

    for s in data.get("scores", []):
        judge, project = judges.get(s["judge"]), projects.get(s["project"])
        if judge is None or project is None:
            report.notes.append(f"skipped score {s['judge']}/{s['project']}: unknown judge or project")
            continue
        a = db.scalar(select(Assignment).where(
            Assignment.judge_id == judge.id, Assignment.project_id == project.id))
        if a is None:
            a = Assignment(event_id=event.id, judge_id=judge.id, project_id=project.id,
                           status=AssignmentStatus.DONE, method="import")
            db.add(a)
            db.flush()
        if db.scalar(select(Score.id).where(Score.assignment_id == a.id)):
            continue
        score = Score(assignment_id=a.id, comment=s.get("comment", "") or "")
        score.values = [ScoreValue(criterion_id=criteria[k].id, value=int(v))
                        for k, v in s.get("criteria", {}).items() if k in criteria]
        db.add(score)
        a.status = AssignmentStatus.DONE
        report.bump("scores")
    db.flush()

    if is_new:
        plan = assign_svc.plan_assignments(db, event, event.reviews_per_project, top_up_seed)
        assign_svc.apply_plan(db, event, plan)
        report.bump("pending assignments", len(plan.created))
    return report
