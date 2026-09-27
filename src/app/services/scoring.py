"""Weighted rubric scores and cross-judge normalization.

The math is written as plain functions over plain data so it can be tested
without a database. JUDGING.md derives and defends every step; the short
version:

1. A score sheet becomes one number on a 0-100 scale: each criterion value is
   rescaled to 0..1 within its own min..max, then averaged with the
   organizer's weights.
2. Each judge has a leniency (their mean) and a spread (their standard
   deviation). Both are shrunk toward the event-wide values with a prior of
   strength k, so a judge with one or two sheets is barely corrected and a
   judge who gives everything the same score does not divide by zero.
3. A sheet's normalized score is its z-score against the judge's shrunk
   mean and spread, mapped back onto the event-wide scale.
4. A project's score is the mean of its normalized sheets.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, AssignmentStatus, Event, Project, RubricCriterion, Score


@dataclass(frozen=True)
class Criterion:
    id: int
    weight: float
    min_value: int
    max_value: int


def sheet_score(values: dict[int, int], criteria: list[Criterion]) -> float | None:
    """Weighted mean of rescaled criterion values, on 0-100.

    Criteria without a value on this sheet (added after the judge scored) are
    skipped and the remaining weights are renormalized. If every weight is
    zero, the criteria count equally.
    """
    present = [c for c in criteria if c.id in values]
    if not present:
        return None
    weights = [c.weight for c in present]
    if sum(weights) <= 0:
        weights = [1.0] * len(present)
    total = 0.0
    for c, w in zip(present, weights):
        span = c.max_value - c.min_value
        fraction = (values[c.id] - c.min_value) / span if span else 0.0
        total += w * min(max(fraction, 0.0), 1.0)
    return 100.0 * total / sum(weights)


@dataclass
class Sheet:
    judge_id: int
    project_id: int
    raw: float
    normalized: float = 0.0


@dataclass
class JudgeStats:
    judge_id: int
    n: int
    mean: float
    sd: float
    shrunk_mean: float
    shrunk_sd: float

    @property
    def leniency(self) -> float:
        """How far above (+) or below (-) the event mean this judge scores."""
        return self.mean


@dataclass
class Normalization:
    global_mean: float
    global_sd: float
    k: float
    judges: dict[int, JudgeStats] = field(default_factory=dict)
    sheets: list[Sheet] = field(default_factory=list)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pop_sd(xs: list[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def normalize(sheets: list[Sheet], k: float = 3.0) -> Normalization:
    """Fill in `normalized` on every sheet. See the module docstring."""
    if not sheets:
        return Normalization(0.0, 0.0, k)
    raws = [s.raw for s in sheets]
    big_m, big_s = _mean(raws), _pop_sd(raws)
    result = Normalization(big_m, big_s, k, sheets=sheets)
    if big_s == 0:
        # Every sheet in the event is identical: there is nothing to correct.
        for s in sheets:
            s.normalized = s.raw
        return result

    by_judge: dict[int, list[Sheet]] = defaultdict(list)
    for s in sheets:
        by_judge[s.judge_id].append(s)

    for judge_id, own in by_judge.items():
        xs = [s.raw for s in own]
        n, m, sd = len(xs), _mean(xs), _pop_sd(xs)
        shrunk_m = (n * m + k * big_m) / (n + k)
        shrunk_var = (n * sd ** 2 + k * big_s ** 2) / (n + k)
        shrunk_sd = math.sqrt(shrunk_var) if shrunk_var > 0 else big_s
        result.judges[judge_id] = JudgeStats(judge_id, n, m, sd, shrunk_m, shrunk_sd)
        for s in own:
            z = (s.raw - shrunk_m) / shrunk_sd
            s.normalized = big_m + z * big_s
    return result


@dataclass
class ProjectResult:
    project_id: int
    n: int
    raw_mean: float | None
    normalized_mean: float | None
    stderr: float | None
    rank: int | None = None
    raw_rank: int | None = None

    @property
    def movement(self) -> int | None:
        if self.rank is None or self.raw_rank is None:
            return None
        return self.raw_rank - self.rank


def rank_projects(project_ids: list[int], sheets: list[Sheet]) -> list[ProjectResult]:
    """Aggregate sheets per project and rank. Projects with no sheets come last, unranked."""
    by_project: dict[int, list[Sheet]] = defaultdict(list)
    for s in sheets:
        by_project[s.project_id].append(s)
    results = []
    for pid in project_ids:
        own = by_project.get(pid, [])
        if not own:
            results.append(ProjectResult(pid, 0, None, None, None))
            continue
        norm = [s.normalized for s in own]
        se = _pop_sd(norm) * math.sqrt(len(norm) / (len(norm) - 1)) / math.sqrt(len(norm)) \
            if len(norm) > 1 else None
        results.append(ProjectResult(pid, len(own), _mean([s.raw for s in own]), _mean(norm), se))

    scored = [r for r in results if r.n]
    for i, r in enumerate(sorted(scored, key=lambda r: (-r.raw_mean, r.project_id)), 1):
        r.raw_rank = i
    ordered = sorted(scored, key=lambda r: (-r.normalized_mean, -r.n, r.project_id))
    for i, r in enumerate(ordered, 1):
        r.rank = i
    return ordered + [r for r in results if not r.n]


# --- database side ---------------------------------------------------------

@dataclass
class EventResults:
    event: Event
    criteria: list[RubricCriterion]
    normalization: Normalization
    rows: list[ProjectResult]
    projects: dict[int, Project]
    excluded: list[Project]


def load_sheets(db: Session, event: Event) -> tuple[list[RubricCriterion], list[tuple[Assignment, Sheet]]]:
    criteria = list(db.scalars(select(RubricCriterion).where(RubricCriterion.event_id == event.id)
                               .order_by(RubricCriterion.position)))
    crit = [Criterion(c.id, c.weight, c.min_value, c.max_value) for c in criteria]
    assignments = db.scalars(
        select(Assignment)
        .where(Assignment.event_id == event.id, Assignment.status == AssignmentStatus.DONE)
        .options(selectinload(Assignment.score).selectinload(Score.values))
    ).all()
    out = []
    for a in assignments:
        if a.score is None:
            continue
        raw = sheet_score({v.criterion_id: v.value for v in a.score.values}, crit)
        if raw is not None:
            out.append((a, Sheet(a.judge_id, a.project_id, raw)))
    return criteria, out


def event_results(db: Session, event: Event) -> EventResults:
    criteria, pairs = load_sheets(db, event)
    sheets = [s for _, s in pairs]
    # Every sheet calibrates its judge, including sheets on flagged duplicates:
    # they are real evidence of how that judge scores.
    norm = normalize(sheets, event.shrinkage_k)
    projects = {p.id: p for p in db.scalars(
        select(Project).where(Project.event_id == event.id).options(
            selectinload(Project.team), selectinload(Project.track)))}
    ranked_ids = [p.id for p in projects.values() if p.in_ranking]
    excluded = [p for p in projects.values() if p.status != "draft" and not p.in_ranking]
    ranked_sheets = [s for s in sheets if s.project_id in set(ranked_ids)]
    rows = rank_projects(ranked_ids, ranked_sheets)
    return EventResults(event, criteria, norm, rows, projects, excluded)
