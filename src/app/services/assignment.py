"""Judge assignment: track-matched, conflict-free, least-loaded first.

Deterministic for a given seed, so an organizer can rerun it and explain
exactly why a judge got a project. JUDGING.md has the full rules.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    Event,
    EventRole,
    JudgeTrack,
    Project,
    ProjectStatus,
    Role,
    TeamMember,
    User,
)


@dataclass
class AssignmentPlan:
    created: list[tuple[int, int]] = field(default_factory=list)      # (judge_id, project_id)
    cross_track: list[tuple[int, int]] = field(default_factory=list)  # subset of created
    unfilled: dict[int, int] = field(default_factory=dict)            # project_id -> missing


def judges_of(db: Session, event: Event) -> list[User]:
    return list(db.scalars(
        select(User).join(EventRole, EventRole.user_id == User.id)
        .where(EventRole.event_id == event.id, EventRole.role == Role.JUDGE)
        .order_by(User.name)))


def conflicted_judges(db: Session, project: Project) -> set[int]:
    """Judges who are members of the project's own team."""
    return set(db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == project.team_id)))


def plan_assignments(db: Session, event: Event, per_project: int, seed: int) -> AssignmentPlan:
    rng = random.Random(seed)
    judges = [j.id for j in judges_of(db, event)]
    tracks: dict[int, set[int]] = defaultdict(set)
    for user_id, track_id in db.execute(
            select(JudgeTrack.user_id, JudgeTrack.track_id).where(JudgeTrack.event_id == event.id)):
        tracks[user_id].add(track_id)

    projects = list(db.scalars(select(Project).where(
        Project.event_id == event.id,
        Project.status == ProjectStatus.SUBMITTED,
        Project.duplicate_of_id.is_(None),
    ).order_by(Project.id)))

    existing: dict[int, set[int]] = defaultdict(set)   # project -> judges
    load: dict[int, int] = defaultdict(int)             # judge -> assignments
    for judge_id, project_id in db.execute(
            select(Assignment.judge_id, Assignment.project_id).where(Assignment.event_id == event.id)):
        existing[project_id].add(judge_id)
        load[judge_id] += 1

    members: dict[int, set[int]] = defaultdict(set)
    for team_id, user_id in db.execute(
            select(TeamMember.team_id, TeamMember.user_id).where(TeamMember.event_id == event.id)):
        members[team_id].add(user_id)

    plan = AssignmentPlan()
    # Neediest projects first so a short supply of judges is spread evenly.
    for project in sorted(projects, key=lambda p: (len(existing[p.id]), p.id)):
        need = per_project - len(existing[project.id])
        if need <= 0:
            continue
        eligible = [j for j in judges
                    if j not in existing[project.id] and j not in members[project.team_id]]
        on_track = [j for j in eligible if project.track_id is None or project.track_id in tracks[j]]
        off_track = [j for j in eligible if j not in on_track]
        tiebreak = {j: rng.random() for j in eligible}

        def by_load(pool):
            return sorted(pool, key=lambda j: (load[j], tiebreak[j]))

        # Track-matched judges first; fall back to any judge only when the
        # track has run out, and report it so the organizer can see it.
        for judge_id, cross in [(j, False) for j in by_load(on_track)] + \
                               [(j, True) for j in by_load(off_track)]:
            if need == 0:
                break
            plan.created.append((judge_id, project.id))
            if cross:
                plan.cross_track.append((judge_id, project.id))
            existing[project.id].add(judge_id)
            load[judge_id] += 1
            need -= 1
        if need:
            plan.unfilled[project.id] = need
    return plan


def apply_plan(db: Session, event: Event, plan: AssignmentPlan) -> None:
    for judge_id, project_id in plan.created:
        db.add(Assignment(event_id=event.id, judge_id=judge_id, project_id=project_id, method="auto"))


def judge_load(db: Session, event: Event) -> dict[int, tuple[int, int]]:
    """judge_id -> (done, total)."""
    rows = db.execute(
        select(Assignment.judge_id, Assignment.status, func.count())
        .where(Assignment.event_id == event.id)
        .group_by(Assignment.judge_id, Assignment.status))
    out: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for judge_id, status, count in rows:
        out[judge_id][1] += count
        if status == "done":
            out[judge_id][0] += count
    return {k: (v[0], v[1]) for k, v in out.items()}
