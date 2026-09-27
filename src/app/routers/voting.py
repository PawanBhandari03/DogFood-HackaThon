"""Community voting router (T3).

Ballots, vote/unvote actions, and community choice tallies.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import Viewer, get_viewer, require_user, team_membership
from app.db import get_db
from app.models import Project, utcnow
from app.security import Forbidden
from app.services.voting import (
    ballot,
    cast_vote,
    retract_vote,
    tallies,
    user_votes,
    voting_open,
)
from app.web import event_or_404, redirect, render

router = APIRouter(tags=["voting"])


@router.get("/events/{slug}/vote", response_class=HTMLResponse)
def ballot_page(
    slug: str,
    request: Request,
    viewer: Viewer = Depends(require_user),
    db: Session = Depends(get_db),
):
    event = event_or_404(db, slug)
    now = utcnow()
    is_open = voting_open(event, now)

    votes = user_votes(db, event, viewer.user)
    voted_project_ids = {v.project_id for v in votes}
    voted_count = len(votes)
    projects = ballot(db, event, viewer.user)

    membership = team_membership(db, viewer, event)
    own_team_id = membership.team_id if membership else None
    is_judge_or_organizer = viewer.is_organizer(event) or viewer.is_judge(event)

    return render(
        request,
        "vote.html",
        event=event,
        is_open=is_open,
        projects=projects,
        voted_project_ids=voted_project_ids,
        voted_count=voted_count,
        max_votes=event.max_votes,
        own_team_id=own_team_id,
        is_judge_or_organizer=is_judge_or_organizer,
    )


@router.post("/events/{slug}/vote/{project_id}")
def vote_action(
    slug: str,
    project_id: int,
    request: Request,
    viewer: Viewer = Depends(require_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    event = event_or_404(db, slug)
    project = db.get(Project, project_id)
    if project is None or project.event_id != event.id:
        raise HTTPException(404, "Project not found in this event.")

    try:
        cast_vote(db, viewer, event, project, request=request)
        db.commit()
        return redirect(f"/events/{event.slug}/vote", flash=f"Voted for {project.title}.")
    except Forbidden as exc:
        return redirect(f"/events/{event.slug}/vote", flash=exc.message)


@router.post("/events/{slug}/unvote/{project_id}")
def unvote_action(
    slug: str,
    project_id: int,
    request: Request,
    viewer: Viewer = Depends(require_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    event = event_or_404(db, slug)
    project = db.get(Project, project_id)
    if project is None or project.event_id != event.id:
        raise HTTPException(404, "Project not found in this event.")

    try:
        retract_vote(db, viewer, event, project, request=request)
        db.commit()
        return redirect(f"/events/{event.slug}/vote", flash=f"Retracted vote for {project.title}.")
    except Forbidden as exc:
        return redirect(f"/events/{event.slug}/vote", flash=exc.message)


@router.get("/events/{slug}/community", response_class=HTMLResponse)
def community_results_page(
    slug: str,
    request: Request,
    viewer: Viewer = Depends(get_viewer),
    db: Session = Depends(get_db),
):
    event = event_or_404(db, slug)
    now = utcnow()
    is_closed = event.voting_close_at is not None and now >= event.voting_close_at
    is_organizer = viewer.is_organizer(event)

    if not is_closed and not is_organizer:
        return render(request, "community_hidden.html", status_code=403, event=event)

    tally_list = tallies(db, event)
    total_votes = sum(c for _, c in tally_list)

    return render(
        request,
        "community.html",
        event=event,
        tallies=tally_list,
        total_votes=total_votes,
        preview=not is_closed,
    )
