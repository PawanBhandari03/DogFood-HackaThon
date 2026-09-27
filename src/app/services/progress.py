"""Numbers for the organizer's live progress view."""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, AssignmentStatus, Event, Project, ProjectStatus, Score, User
from app.services.assignment import judges_of


@dataclass
class ProjectProgress:
    project: Project
    done: int
    assigned: int


@dataclass
class JudgeProgress:
    judge: User
    done: int
    assigned: int
    flat: bool  # every finished sheet identical: a low-information judge


@dataclass
class Progress:
    projects: list[ProjectProgress]
    judges: list[JudgeProgress]
    done: int
    assigned: int
    under_reviewed: int  # projects with fewer finished reviews than the event target

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.assigned) if self.assigned else 0


def event_progress(db: Session, event: Event) -> Progress:
    assignments = db.scalars(select(Assignment).where(Assignment.event_id == event.id)
                             .options(selectinload(Assignment.score).selectinload(Score.values))).all()
    per_project: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    per_judge: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    sheets: dict[int, set] = defaultdict(set)
    for a in assignments:
        done = a.status == AssignmentStatus.DONE
        per_project[a.project_id][1] += 1
        per_judge[a.judge_id][1] += 1
        if done:
            per_project[a.project_id][0] += 1
            per_judge[a.judge_id][0] += 1
            if a.score:
                sheets[a.judge_id].add(tuple(sorted((v.criterion_id, v.value) for v in a.score.values)))
    projects = db.scalars(select(Project).where(
        Project.event_id == event.id, Project.status == ProjectStatus.SUBMITTED)
        .options(selectinload(Project.team), selectinload(Project.track), selectinload(Project.duplicate_of))
        .order_by(Project.title)).all()
    project_rows = [ProjectProgress(p, *per_project[p.id]) for p in projects]
    project_rows.sort(key=lambda r: (r.done - event.reviews_per_project, r.project.title))
    judge_rows = []
    for j in judges_of(db, event):
        done, assigned = per_judge[j.id]
        # With finished sheets that include identical values across every
        # sheet, the judge is not separating projects at all.
        judge_rows.append(JudgeProgress(j, done, assigned, flat=done >= 2 and len(sheets[j.id]) == 1))
    judge_rows.sort(key=lambda r: (r.done - r.assigned, r.judge.name))
    return Progress(
        projects=project_rows, judges=judge_rows,
        done=sum(r.done for r in project_rows), assigned=sum(r.assigned for r in project_rows),
        under_reviewed=sum(1 for r in project_rows
                           if r.project.duplicate_of_id is None and r.done < event.reviews_per_project))
