"""Certificate page tests (T4-5).

Covers:
- Certificate loads for a submitted project (200 OK, shows team/project).
- Shows no rank before results are published.
- Shows rank after results are published.
- Returns 404 for an unknown team ref.
- Works via numeric team id and external_id.
"""

from sqlalchemy import select

from app.models import Assignment, AssignmentStatus, Project, ProjectStatus, Role, Score, ScoreValue, Team, utcnow
from conftest import as_user


def test_certificate_loads_for_submitted_project(client, make, db):
    event = make.event("cert-basic", open_=True)
    owner = make.user("cert_owner@x.org")
    team = make.team(event, owner, name="CertTeam")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="CertProject",
        status=ProjectStatus.SUBMITTED,
        submitted_at=utcnow(),
    )
    db.add(project)
    db.commit()

    r = client.get(f"/events/{event.slug}/certificates/{team.id}")
    assert r.status_code == 200
    assert "CertTeam" in r.text
    assert "CertProject" in r.text
    assert event.name in r.text


def test_certificate_via_team_external_id(client, make, db):
    event = make.event("cert-extid", open_=True)
    owner = make.user("cert_ext@x.org")
    team = make.team(event, owner, name="ExtTeam")
    team.external_id = "ext-team-001"
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="ExtProject",
        status=ProjectStatus.SUBMITTED,
        submitted_at=utcnow(),
    )
    db.add(project)
    db.commit()

    r = client.get(f"/events/{event.slug}/certificates/ext-team-001")
    assert r.status_code == 200
    assert "ExtTeam" in r.text


def test_certificate_shows_no_rank_before_results_published(client, make, db):
    event = make.event("cert-norank", open_=True)
    owner = make.user("norank@x.org")
    team = make.team(event, owner, name="NoRankTeam")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="NoRankProject",
        status=ProjectStatus.SUBMITTED,
        submitted_at=utcnow(),
    )
    db.add(project)
    db.commit()

    assert event.results_published_at is None

    r = client.get(f"/events/{event.slug}/certificates/{team.id}")
    assert r.status_code == 200
    # Rank badge must NOT appear
    assert "Ranked #" not in r.text


def test_certificate_shows_rank_after_results_published(client, make, db):
    event = make.event("cert-rank", open_=True)
    judge = make.user("cert_judge@x.org")
    make.role(judge, event, Role.JUDGE)
    owner = make.user("cert_p@x.org")
    team = make.team(event, owner, name="RankedTeam")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="RankedProject",
        status=ProjectStatus.SUBMITTED,
        submitted_at=utcnow(),
    )
    db.add(project)
    db.flush()

    # Create a scored assignment so ranking is computed
    assignment = Assignment(
        event_id=event.id,
        judge_id=judge.id,
        project_id=project.id,
        status=AssignmentStatus.DONE,
    )
    db.add(assignment)
    db.flush()
    score = Score(assignment_id=assignment.id, comment="good")
    db.add(score)
    db.flush()
    crit = event.criteria[0]
    db.add(ScoreValue(score_id=score.id, criterion_id=crit.id, value=4))

    # Publish results
    event.results_published_at = utcnow()
    db.commit()

    r = client.get(f"/events/{event.slug}/certificates/{team.id}")
    assert r.status_code == 200
    assert "Ranked #" in r.text


def test_certificate_unavailable_for_draft_project(client, make, db):
    event = make.event("cert-draft", open_=True)
    owner = make.user("draft_cert@x.org")
    team = make.team(event, owner, name="DraftTeam")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="DraftProject",
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    db.commit()

    r = client.get(f"/events/{event.slug}/certificates/{team.id}")
    assert r.status_code == 200
    assert "not yet available" in r.text
    assert "DraftProject" not in r.text


def test_certificate_404_for_unknown_team(client, make, db):
    event = make.event("cert-404", open_=True)
    r = client.get(f"/events/{event.slug}/certificates/99999")
    assert r.status_code == 404


def test_certificate_no_login_required(client, make, db):
    """Certificate is a public URL — no session cookie needed."""
    event = make.event("cert-public", open_=True)
    owner = make.user("pub_cert@x.org")
    team = make.team(event, owner, name="PubTeam")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="PubProject",
        status=ProjectStatus.SUBMITTED,
        submitted_at=utcnow(),
    )
    db.add(project)
    db.commit()

    # Explicitly clear cookies — should still get 200
    client.cookies.clear()
    r = client.get(f"/events/{event.slug}/certificates/{team.id}")
    assert r.status_code == 200
    assert "PubProject" in r.text
