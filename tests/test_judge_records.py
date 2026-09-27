"""Signed judge participation record tests (T4-6).

Covers:
- Record page loads without login (public).
- Valid signature verifies as true.
- Tampered signature (wrong count, wrong event) verifies as false.
- Record contains event names and review counts but NOT score values or project titles.
- 404 for unknown judge ref.
- Verify endpoint returns JSON with "valid" key.
"""

import json

from app.models import (
    Assignment,
    AssignmentStatus,
    Project,
    ProjectStatus,
    Role,
    Score,
    ScoreValue,
    utcnow,
)
from app.services.judge_records import compute_signature, load_record, EventParticipation
from conftest import as_user


def _make_scored_assignment(db, event, judge, project):
    """Helper: create a DONE assignment with one score value."""
    a = Assignment(
        event_id=event.id,
        judge_id=judge.id,
        project_id=project.id,
        status=AssignmentStatus.DONE,
    )
    db.add(a)
    db.flush()
    score = Score(assignment_id=a.id, comment="private thoughts")
    db.add(score)
    db.flush()
    crit = event.criteria[0]
    db.add(ScoreValue(score_id=score.id, criterion_id=crit.id, value=4))
    db.commit()
    return a


def test_judge_record_page_no_login_required(client, make, db):
    """Public endpoint — no session cookie."""
    event = make.event("rec-pub", open_=True)
    judge = make.user("rec_judge@x.org")
    make.role(judge, event, Role.JUDGE)

    client.cookies.clear()
    r = client.get(f"/judges/{judge.id}/record")
    assert r.status_code == 200
    assert judge.name in r.text


def test_judge_record_shows_event_counts(client, make, db):
    event = make.event("rec-counts", open_=True)
    judge = make.user("counts_judge@x.org")
    make.role(judge, event, Role.JUDGE)

    owner = make.user("counts_owner@x.org")
    team = make.team(event, owner, name="CountTeam")
    project = Project(event_id=event.id, team_id=team.id, title="TopSecret",
                      status=ProjectStatus.SUBMITTED, submitted_at=utcnow())
    db.add(project)
    db.flush()
    _make_scored_assignment(db, event, judge, project)

    r = client.get(f"/judges/{judge.id}/record")
    assert r.status_code == 200
    assert event.name in r.text
    assert "1" in r.text          # review count
    # Must NOT expose score values, criteria, or the project title/comment.
    # (Searching the page for a bare "4" is unreliable: CSS/layout numbers
    # like "640px" or a font size always contain stray digits unrelated to
    # any score. Checking for the criterion's own label is precise instead.)
    assert event.criteria[0].label not in r.text
    assert "TopSecret" not in r.text
    assert "private thoughts" not in r.text


def test_valid_signature_verifies_true(client, make, db):
    event = make.event("rec-sig", open_=True)
    judge = make.user("sig_judge@x.org")
    make.role(judge, event, Role.JUDGE)

    owner = make.user("sig_owner@x.org")
    team = make.team(event, owner, name="SigTeam")
    project = Project(event_id=event.id, team_id=team.id, title="SigProject",
                      status=ProjectStatus.SUBMITTED, submitted_at=utcnow())
    db.add(project)
    db.flush()
    _make_scored_assignment(db, event, judge, project)

    # Fetch the record page to get the canonical signature
    from app.db import SessionLocal
    with SessionLocal() as s:
        from app.services.judge_records import load_record
        from app.models import User
        from sqlalchemy import select
        u = s.scalar(select(User).where(User.id == judge.id))
        rec = load_record(s, u)
        sig = rec.signature

    r = client.get(f"/judges/{judge.id}/record/verify?sig={sig}")
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True


def test_tampered_count_fails_verification(make, db):
    """If the done count in the signature doesn't match reality, verify → false."""
    event = make.event("rec-tamper", open_=True)
    judge = make.user("tamper_judge@x.org")
    make.role(judge, event, Role.JUDGE)

    judge_ref = judge.external_id or str(judge.id)
    # Build a fake participation with wrong count
    fake_events = [EventParticipation(
        event_name=event.name,
        event_slug=event.slug,
        reviews_done=99,  # wrong count
    )]
    fake_sig = compute_signature(judge_ref, fake_events)

    # Real record has 0 reviews, so signature will differ
    from app.db import SessionLocal
    with SessionLocal() as s:
        from app.models import User
        from sqlalchemy import select
        u = s.scalar(select(User).where(User.id == judge.id))
        real_rec = load_record(s, u)

    from app.services.judge_records import verify_signature
    assert verify_signature(real_rec.judge_ref, real_rec.events, fake_sig) is False


def test_wrong_event_fails_verification(make, db):
    """Signature over a different event slug is rejected."""
    event = make.event("rec-wrong", open_=True)
    judge = make.user("wrong_judge@x.org")
    make.role(judge, event, Role.JUDGE)

    judge_ref = judge.external_id or str(judge.id)
    fake_events = [EventParticipation(
        event_name="Imposter Hackathon",
        event_slug="imposter-slug",
        reviews_done=0,
    )]
    fake_sig = compute_signature(judge_ref, fake_events)

    from app.db import SessionLocal
    with SessionLocal() as s:
        from app.models import User
        from sqlalchemy import select
        u = s.scalar(select(User).where(User.id == judge.id))
        real_rec = load_record(s, u)

    from app.services.judge_records import verify_signature
    assert verify_signature(real_rec.judge_ref, real_rec.events, fake_sig) is False


def test_judge_record_404_unknown_ref(client, make, db):
    r = client.get("/judges/99999/record")
    assert r.status_code == 404


def test_verify_endpoint_returns_json(client, make, db):
    judge = make.user("json_judge@x.org")
    r = client.get(f"/judges/{judge.id}/record/verify?sig=sha256=deadbeef")
    assert r.status_code == 200
    data = r.json()
    assert "valid" in data
    assert data["valid"] is False
