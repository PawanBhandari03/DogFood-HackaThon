"""Project journey and status pipeline service.

Computes the end-to-end path of a participant's project across four distinct levels:
1. Submission Level (Draft vs Submitted vs Withdrawn)
2. Organizer Level (Duplicate Screening & Reviewer Assignment)
3. Judging Level (Evaluation Progress & Scores Quota)
4. Selection & Outcome Level (Deliberation vs Published Rank, Awards, and Certificate)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Event, Prize, Project, ProjectStatus, Team, utcnow
from app.services.scoring import event_results
from app.util import fmt_dt


@dataclass
class Stage:
    key: str  # "submission", "organizer", "judging", "outcome"
    title: str  # Display title
    status: str  # "done" (green), "active" (accent/in progress), "pending" (muted), "flagged" (warning)
    badge: str  # Short label like "SUBMITTED", "IN REVIEW", "SELECTED"
    detail: str  # Contextual message
    hint: str | None = None


@dataclass
class ProjectJourney:
    project: Optional[Project]
    team: Team
    event: Event
    current_level_name: str
    current_badge: str
    current_badge_class: str  # for CSS styling: "done", "active", "pending", "flagged"
    stages: list[Stage]
    certificate_url: Optional[str]
    team_url: str
    project_url: Optional[str]
    rank: Optional[int]
    score: Optional[float]
    is_winner: bool
    prize_name: Optional[str]


def compute_journey(db: Session, team: Team, project: Optional[Project], event: Event) -> ProjectJourney:
    now = utcnow()
    stages: list[Stage] = []

    team_ref = team.external_id or str(team.id)
    team_url = f"/events/{event.slug}/team"
    project_url = f"/projects/{project.external_id or project.id}" if project else None
    certificate_url = f"/events/{event.slug}/certificates/{team_ref}" if (project and project.status == ProjectStatus.SUBMITTED) else None

    # Default metrics
    rank: Optional[int] = None
    score: Optional[float] = None
    is_winner = False
    prize_name: Optional[str] = None

    # ---------------------------------------------------------
    # STAGE 1: SUBMISSION LEVEL
    # ---------------------------------------------------------
    if not project:
        stages.append(Stage(
            key="submission",
            title="1. Submission Level",
            status="active",
            badge="NO PROJECT",
            detail="Your team has not drafted a project yet.",
            hint=f"Submissions close {fmt_dt(event.submissions_close_at)}" if now < event.submissions_close_at else "Submissions closed",
        ))
        stages.append(Stage(
            key="organizer",
            title="2. Organizer Screening",
            status="pending",
            badge="PENDING",
            detail="Awaiting project submission.",
        ))
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="pending",
            badge="PENDING",
            detail="Awaiting organizer assignment.",
        ))
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending",
            badge="PENDING",
            detail="Awaiting evaluation.",
        ))
        return ProjectJourney(
            project=None,
            team=team,
            event=event,
            current_level_name="Drafting Phase",
            current_badge="DRAFT NEEDED",
            current_badge_class="active",
            stages=stages,
            certificate_url=None,
            team_url=team_url,
            project_url=None,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    if project.status == ProjectStatus.DRAFT:
        stages.append(Stage(
            key="submission",
            title="1. Submission Level",
            status="active",
            badge="DRAFT",
            detail=f"Project is saved as draft. Click submit on your team page before deadline.",
            hint=f"Deadline: {fmt_dt(event.submissions_close_at)}",
        ))
        stages.append(Stage(
            key="organizer",
            title="2. Organizer Screening",
            status="pending",
            badge="PENDING",
            detail="Drafts are not sent to organizers until submitted.",
        ))
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="pending",
            badge="PENDING",
            detail="Awaiting project submission.",
        ))
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending",
            badge="PENDING",
            detail="Awaiting evaluation.",
        ))
        return ProjectJourney(
            project=project,
            team=team,
            event=event,
            current_level_name="Submission Level (Draft)",
            current_badge="DRAFT",
            current_badge_class="active",
            stages=stages,
            certificate_url=None,
            team_url=team_url,
            project_url=project_url,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    if project.status == ProjectStatus.WITHDRAWN:
        stages.append(Stage(
            key="submission",
            title="1. Submission Level",
            status="flagged",
            badge="WITHDRAWN",
            detail="This project was withdrawn from the event.",
        ))
        stages.append(Stage(
            key="organizer",
            title="2. Organizer Screening",
            status="pending",
            badge="WITHDRAWN",
            detail="Withdrawn projects are removed from organizer screening.",
        ))
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="pending",
            badge="WITHDRAWN",
            detail="Excluded from judging assignments.",
        ))
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending",
            badge="WITHDRAWN",
            detail="Not eligible for rankings or awards.",
        ))
        return ProjectJourney(
            project=project,
            team=team,
            event=event,
            current_level_name="Project Withdrawn",
            current_badge="WITHDRAWN",
            current_badge_class="flagged",
            stages=stages,
            certificate_url=None,
            team_url=team_url,
            project_url=project_url,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    # Project is SUBMITTED
    sub_detail = f"Submitted {fmt_dt(project.submitted_at)}" if project.submitted_at else "Submission verified"
    stages.append(Stage(
        key="submission",
        title="1. Submission Level",
        status="done",
        badge="SUBMITTED",
        detail=sub_detail,
        hint="Live in public gallery",
    ))

    # ---------------------------------------------------------
    # STAGE 2: ORGANIZER LEVEL
    # ---------------------------------------------------------
    assignments = list(db.scalars(select(Assignment).where(Assignment.project_id == project.id)).all())
    assigned_count = len(assignments)

    is_duplicate_flagged = bool(project.duplicate_of_id and not project.duplicate_dismissed)

    if is_duplicate_flagged:
        stages.append(Stage(
            key="organizer",
            title="2. Organizer Level",
            status="flagged",
            badge="DUPLICATE FLAGGED",
            detail="Flagged as potential duplicate submission. Under organizer review.",
            hint="Held from ranking until reviewed",
        ))
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="pending",
            badge="ON HOLD",
            detail="Judging assignments paused pending duplicate review.",
        ))
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending",
            badge="ON HOLD",
            detail="Outcome pending organizer determination.",
        ))
        return ProjectJourney(
            project=project,
            team=team,
            event=event,
            current_level_name="Organizer Review (Duplicate Flagged)",
            current_badge="DUPLICATE REVIEW",
            current_badge_class="flagged",
            stages=stages,
            certificate_url=certificate_url,
            team_url=team_url,
            project_url=project_url,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    if assigned_count == 0:
        stages.append(Stage(
            key="organizer",
            title="2. Organizer Level",
            status="active",
            badge="IN SCREENING",
            detail="Integrity check passed. In queue for judge assignment.",
            hint=f"Target: {event.reviews_per_project} reviews per project",
        ))
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="pending",
            badge="QUEUED",
            detail="Waiting for judge assignments.",
        ))
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending",
            badge="PENDING",
            detail="Awaiting review scores.",
        ))
        return ProjectJourney(
            project=project,
            team=team,
            event=event,
            current_level_name="Organizer Level (In Queue)",
            current_badge="IN SCREENING",
            current_badge_class="active",
            stages=stages,
            certificate_url=certificate_url,
            team_url=team_url,
            project_url=project_url,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    # Organizer assigned judges
    stages.append(Stage(
        key="organizer",
        title="2. Organizer Level",
        status="done",
        badge="ASSIGNED",
        detail=f"Screening passed · Assigned to {assigned_count} track judges.",
        hint="Conflict-of-interest verified",
    ))

    # ---------------------------------------------------------
    # STAGE 3: JUDGING LEVEL
    # ---------------------------------------------------------
    done_count = sum(1 for a in assignments if a.status == "done")

    if done_count < assigned_count:
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="active",
            badge=f"IN REVIEW ({done_count}/{assigned_count})",
            detail=f"{done_count} of {assigned_count} judge evaluations submitted.",
            hint="Judges score independently on weighted rubric",
        ))
    else:
        stages.append(Stage(
            key="judging",
            title="3. Judging Level",
            status="done",
            badge=f"EVALUATED ({done_count}/{assigned_count})",
            detail=f"All {assigned_count} judge score sheets submitted and finalized.",
            hint="Mathematical shrinkage normalization applied",
        ))

    # ---------------------------------------------------------
    # STAGE 4: SELECTION & OUTCOME LEVEL
    # ---------------------------------------------------------
    is_published = bool(event.results_published_at)

    if not is_published:
        stages.append(Stage(
            key="outcome",
            title="4. Selection & Outcome",
            status="pending" if done_count < assigned_count else "active",
            badge="DELIBERATION" if done_count >= assigned_count else "SEALED",
            detail="Official scores are sealed while judging & deliberation are in progress." if done_count < assigned_count else "All evaluations received. Organizers finalizing awards & rankings.",
            hint="Results will unlock once published by organizers",
        ))

        current_level = "Judging Level (In Progress)" if done_count < assigned_count else "Deliberation & Selection"
        current_badge = f"IN REVIEW ({done_count}/{assigned_count})" if done_count < assigned_count else "DELIBERATION"
        current_badge_class = "active"

        return ProjectJourney(
            project=project,
            team=team,
            event=event,
            current_level_name=current_level,
            current_badge=current_badge,
            current_badge_class=current_badge_class,
            stages=stages,
            certificate_url=certificate_url,
            team_url=team_url,
            project_url=project_url,
            rank=None,
            score=None,
            is_winner=False,
            prize_name=None,
        )

    # RESULTS ARE PUBLISHED! Calculate Rank and Selection Outcome
    res = event_results(db, event)
    for r in res.rows:
        if r.project_id == project.id:
            rank = r.rank
            score = r.normalized_mean
            break

    # Check for prizes
    if rank == 1:
        is_winner = True
        prize_name = "Grand Prize / 1st Place"
    elif rank == 2:
        is_winner = True
        prize_name = "Runner Up / 2nd Place"
    elif rank == 3:
        is_winner = True
        prize_name = "3rd Place Podium"
    elif rank and rank <= 10:
        is_winner = True
        prize_name = f"Top 10 Finalist (Rank #{rank})"

    if is_winner:
        outcome_badge = f"🏆 SELECTED ({prize_name or f'Rank #{rank}'})"
        outcome_detail = f"Congratulations! Officially selected for {prize_name or f'Rank #{rank}'} with score {score:.1f}/100."
    elif rank:
        outcome_badge = f"RANK #{rank}"
        outcome_detail = f"Finished Rank #{rank} with normalized score {score:.1f}/100."
    else:
        outcome_badge = "COMPLETED"
        outcome_detail = "Participation verified. Standings published."

    stages.append(Stage(
        key="outcome",
        title="4. Selection & Outcome",
        status="done",
        badge=outcome_badge,
        detail=outcome_detail,
        hint="Printable certificate generated",
    ))

    return ProjectJourney(
        project=project,
        team=team,
        event=event,
        current_level_name="Results Published · Selected" if is_winner else "Results Published",
        current_badge=outcome_badge,
        current_badge_class="done" if is_winner else "active",
        stages=stages,
        certificate_url=certificate_url,
        team_url=team_url,
        project_url=project_url,
        rank=rank,
        score=score,
        is_winner=is_winner,
        prize_name=prize_name,
    )
