"""Community voting and comments tests (T3)."""

from datetime import timedelta
from sqlalchemy import func, select

from app.models import AuditEntry, Comment, Event, Project, ProjectStatus, Role, Vote, utcnow
from app.services.voting import ballot
from conftest import as_user


def _make_votable_event(make, db, slug="vote-jam", max_votes=3, n_projects=5):
    now = utcnow()
    event = make.event(slug, open_=True)
    event.voting_mode = "authenticated"
    event.voting_open_at = now - timedelta(hours=1)
    event.voting_close_at = now + timedelta(days=2)
    event.max_votes = max_votes
    db.commit()

    projects = []
    for i in range(n_projects):
        owner = make.user(f"owner{i}_{slug}@x.org")
        team = make.team(event, owner, name=f"Team {i}")
        p = Project(
            event_id=event.id,
            team_id=team.id,
            title=f"Project {i}",
            summary=f"Summary {i}",
            status=ProjectStatus.SUBMITTED,
        )
        db.add(p)
        projects.append(p)
    db.commit()
    return event, projects


def test_can_vote_when_open_refused_outside_window(client, make, db):
    now = utcnow()
    event, projects = _make_votable_event(make, db, "window-test")
    voter = make.user("voter@x.org")
    c = as_user(client, make, voter)

    # 1. Vote succeeds when open
    r = c.post(f"/events/{event.slug}/vote/{projects[0].id}", follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(func.count(Vote.id)).where(Vote.event_id == event.id)) == 1

    # 2. Cannot vote before open
    event.voting_open_at = now + timedelta(hours=1)
    db.commit()
    r = c.post(f"/events/{event.slug}/vote/{projects[1].id}", follow_redirects=True)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.event_id == event.id)) == 1

    # 3. Cannot vote after close
    event.voting_open_at = now - timedelta(days=2)
    event.voting_close_at = now - timedelta(hours=1)
    db.commit()
    r = c.post(f"/events/{event.slug}/vote/{projects[1].id}", follow_redirects=True)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.event_id == event.id)) == 1


def test_voting_twice_for_same_project_creates_one_row(client, make, db):
    event, projects = _make_votable_event(make, db, "dup-test")
    voter = make.user("dup_voter@x.org")
    c = as_user(client, make, voter)

    r1 = c.post(f"/events/{event.slug}/vote/{projects[0].id}", follow_redirects=False)
    assert r1.status_code == 303
    r2 = c.post(f"/events/{event.slug}/vote/{projects[0].id}", follow_redirects=False)
    assert r2.status_code == 303

    assert db.scalar(select(func.count(Vote.id)).where(Vote.event_id == event.id, Vote.user_id == voter.id)) == 1


def test_cannot_exceed_max_votes(client, make, db):
    event, projects = _make_votable_event(make, db, "max-test", max_votes=2, n_projects=4)
    voter = make.user("max_voter@x.org")
    c = as_user(client, make, voter)

    c.post(f"/events/{event.slug}/vote/{projects[0].id}", follow_redirects=False)
    c.post(f"/events/{event.slug}/vote/{projects[1].id}", follow_redirects=False)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == voter.id)) == 2

    # 3rd vote refused
    r3 = c.post(f"/events/{event.slug}/vote/{projects[2].id}", follow_redirects=True)
    assert "used all 2 votes" in r3.text or "All votes used" in r3.text
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == voter.id)) == 2


def test_cannot_vote_own_team_or_if_judge_or_organizer(client, make, db):
    event, projects = _make_votable_event(make, db, "role-test")

    # 1. Own team member cannot vote for own project
    team_lead = db.scalar(select(Project).where(Project.id == projects[0].id)).team.members[0].user
    c_lead = as_user(client, make, team_lead)
    r = c_lead.post(f"/events/{event.slug}/vote/{projects[0].id}", follow_redirects=True)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == team_lead.id)) == 0

    # 2. Judge of the event cannot vote
    judge_user = make.user("judge@x.org")
    make.role(judge_user, event, Role.JUDGE)
    c_judge = as_user(client, make, judge_user)
    r_j = c_judge.post(f"/events/{event.slug}/vote/{projects[1].id}", follow_redirects=True)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == judge_user.id)) == 0

    # 3. Organizer of the event cannot vote
    org_user = make.user("org@x.org")
    make.role(org_user, event, Role.ORGANIZER)
    c_org = as_user(client, make, org_user)
    r_o = c_org.post(f"/events/{event.slug}/vote/{projects[1].id}", follow_redirects=True)
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == org_user.id)) == 0


def test_ballot_randomization_differs_between_users_and_stable_for_same_user(client, make, db):
    event, projects = _make_votable_event(make, db, "ballot-test", n_projects=10)
    user_a = make.user("voter_a@x.org")
    user_b = make.user("voter_b@x.org")

    ballot_a1 = [p.id for p in ballot(db, event, user_a)]
    ballot_a2 = [p.id for p in ballot(db, event, user_a)]
    ballot_b = [p.id for p in ballot(db, event, user_b)]

    # Same voter gets identical order twice
    assert ballot_a1 == ballot_a2
    # Two different voters get different orders
    assert ballot_a1 != ballot_b


def test_tallies_hidden_during_voting_open_and_public_after_close(client, make, db):
    now = utcnow()
    event, projects = _make_votable_event(make, db, "tallies-test")
    voter = make.user("voter_t@x.org")
    c_voter = as_user(client, make, voter)
    c_voter.post(f"/events/{event.slug}/vote/{projects[0].id}")

    visitor = client
    visitor.cookies.clear()

    # While voting is open: 403 for visitor
    r_open = visitor.get(f"/events/{event.slug}/community")
    assert r_open.status_code == 403

    # Organizer can preview
    org = make.user("org_preview@x.org")
    make.role(org, event, Role.ORGANIZER)
    r_org = as_user(client, make, org).get(f"/events/{event.slug}/community")
    assert r_org.status_code == 200
    assert "Preview" in r_org.text

    # After voting closes: 200 for visitor
    event.voting_close_at = now - timedelta(minutes=1)
    db.commit()

    visitor.cookies.clear()
    r_closed = visitor.get(f"/events/{event.slug}/community")
    assert r_closed.status_code == 200
    assert projects[0].title in r_closed.text


def test_publishing_results_refused_while_voting_open(client, make, db):
    event, projects = _make_votable_event(make, db, "publish-test")
    org = make.user("org_pub@x.org")
    make.role(org, event, Role.ORGANIZER)
    c_org = as_user(client, make, org)

    # Submissions closed but voting is open
    event.submissions_close_at = utcnow() - timedelta(hours=1)
    db.commit()

    r = c_org.post(f"/events/{event.slug}/manage/results/publish", data={"action": "publish"})
    assert r.status_code == 409


def test_comments_lifecycle_auth_validation_and_moderation(client, make, db):
    event, projects = _make_votable_event(make, db, "comments-test")
    p = projects[0]
    p_ref = p.external_id or p.id

    # 1. Anonymous cannot comment (redirects to login)
    client.cookies.clear()
    r_anon = client.post(f"/projects/{p_ref}/comments", data={"body": "Great project!"}, follow_redirects=False)
    assert r_anon.status_code == 303
    assert "/login" in r_anon.headers["location"]

    # 2. Logged in user can comment
    user = make.user("commenter@x.org")
    c = as_user(client, make, user)
    r_post = c.post(f"/projects/{p_ref}/comments", data={"body": "Incredible work!"}, follow_redirects=True)
    assert r_post.status_code == 200
    assert "Incredible work!" in r_post.text

    # 3. Comment exceeding 2000 chars is refused
    r_long = c.post(f"/projects/{p_ref}/comments", data={"body": "x" * 2001})
    assert r_long.status_code == 422

    comment = db.scalar(select(Comment).where(Comment.project_id == p.id))
    assert comment is not None

    # 4. Non-organizer cannot hide comment
    r_non_org = c.post(f"/comments/{comment.id}/hide")
    assert r_non_org.status_code == 403

    # 5. Organizer hides comment -> disappears for visitor/participant
    org = make.user("org_mod@x.org")
    make.role(org, event, Role.ORGANIZER)
    c_org = as_user(client, make, org)
    r_hide = c_org.post(f"/comments/{comment.id}/hide", follow_redirects=True)
    assert r_hide.status_code == 200

    # Visitor does not see the hidden comment
    client.cookies.clear()
    r_vis = client.get(f"/projects/{p_ref}")
    assert "Incredible work!" not in r_vis.text


def test_vote_rate_limiting_31st_action_refused(client, make, db):
    event, projects = _make_votable_event(make, db, "ratelimit-test", max_votes=50, n_projects=40)
    voter = make.user("fast_voter@x.org")
    c = as_user(client, make, voter)

    for i in range(30):
        r = c.post(f"/events/{event.slug}/vote/{projects[i].id}", follow_redirects=False)
        assert r.status_code == 303

    # 31st action within minute gets rate limited
    r_31 = c.post(f"/events/{event.slug}/vote/{projects[30].id}", follow_redirects=True)
    assert "Too many voting actions" in r_31.text or "Rate limit" in r_31.text
    assert db.scalar(select(func.count(Vote.id)).where(Vote.user_id == voter.id)) == 30


def test_audit_logging_for_voting_and_retraction(client, make, db):
    event, projects = _make_votable_event(make, db, "audit-vote-test")
    voter = make.user("audited_voter@x.org")
    c = as_user(client, make, voter)

    # Cast vote -> vote.cast audit row
    c.post(f"/events/{event.slug}/vote/{projects[0].id}")
    entry_cast = db.scalar(select(AuditEntry).where(AuditEntry.action == "vote.cast", AuditEntry.actor_id == voter.id))
    assert entry_cast is not None
    assert entry_cast.entity_id == str(projects[0].id)

    # Retract vote -> vote.retracted audit row
    c.post(f"/events/{event.slug}/unvote/{projects[0].id}")
    entry_retract = db.scalar(select(AuditEntry).where(AuditEntry.action == "vote.retracted", AuditEntry.actor_id == voter.id))
    assert entry_retract is not None
