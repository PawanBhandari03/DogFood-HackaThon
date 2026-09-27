"""The deadline holds on every write path, and team formation rules."""

from datetime import timedelta

from sqlalchemy import select

from app.models import Project, Role, TeamMember
from conftest import as_user


def test_open_event_accepts_a_project_then_closing_freezes_it(client, make, db):
    event = make.event("hack", open_=True)
    user = make.user("p@x.org")
    make.team(event, user)
    c = as_user(client, make, user)
    track = event.tracks[0].id
    r = c.post("/api/events/hack/projects", json={"title": "Glow", "summary": "s", "track_id": track, "submit": True})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "submitted"

    event.submissions_close_at = event.starts_at + timedelta(seconds=1)
    db.commit()
    r = c.post("/api/events/hack/projects", json={"title": "Changed"})
    assert r.status_code == 403 and r.json()["error"] == "submissions_closed"
    for path in ("/events/hack/project/withdraw", "/events/hack/project/unsubmit", "/events/hack/team/leave"):
        assert c.post(path).status_code == 403, path
    assert c.post("/events/hack/project", data={"title": "Changed"}).status_code == 403
    db.expire_all()
    assert db.scalar(select(Project.title)) == "Glow"


def test_closed_event_refuses_before_looking_at_the_body(client, make, db):
    make.event("hack", open_=False)
    user = make.user("p@x.org")
    r = as_user(client, make, user).post("/api/events/hack/projects", json={})
    assert r.status_code == 403 and r.json()["error"] == "submissions_closed"


def test_upcoming_event_refuses_too(client, make, db):
    event = make.event("hack", open_=True)
    event.starts_at = event.submissions_close_at - timedelta(hours=1)
    db.commit()
    r = as_user(client, make, make.user("p@x.org")).post("/api/events/hack/projects", json={"title": "x"})
    assert r.status_code == 403 and r.json()["error"] == "submissions_not_open"


def test_project_needs_a_team(client, make, db):
    make.event("hack")
    r = as_user(client, make, make.user("p@x.org")).post("/api/events/hack/projects", json={"title": "x"})
    assert r.status_code == 403 and r.json()["error"] == "no_team"


def test_invite_link_join_and_limits(client, make, db):
    event = make.event("hack")
    event.max_team_size = 2
    db.commit()
    lead, friend, third = make.user("lead@x.org"), make.user("friend@x.org"), make.user("third@x.org")
    c = as_user(client, make, lead)
    assert c.post("/events/hack/team", data={"name": "Owls"}, follow_redirects=False).status_code == 303
    code = db.scalar(select(TeamMember).where(TeamMember.user_id == lead.id)).team.invite_code

    c = as_user(client, make, friend)
    assert c.post(f"/join/{code}", follow_redirects=False).status_code == 303
    c = as_user(client, make, third)
    r = c.post(f"/join/{code}")
    assert r.status_code == 403  # team full
    assert db.scalar(select(TeamMember).where(TeamMember.user_id == third.id)) is None


def test_one_team_per_person_per_event(client, make, db):
    event = make.event("hack")
    a, b = make.user("a@x.org"), make.user("b@x.org")
    other = make.team(event, b, name="Other")
    c = as_user(client, make, a)
    c.post("/events/hack/team", data={"name": "Mine"})
    c.post(f"/join/{other.invite_code}")
    assert db.scalar(select(TeamMember.team_id).where(TeamMember.user_id == a.id)) != other.id


def test_judges_cannot_compete_in_their_event(client, make, db):
    event = make.event("hack")
    judge, lead = make.user("j@x.org"), make.user("lead@x.org")
    make.role(judge, event, Role.JUDGE)
    team = make.team(event, lead)
    c = as_user(client, make, judge)
    assert c.post("/events/hack/team", data={"name": "Sneaky"}).status_code == 403
    assert c.post(f"/join/{team.invite_code}").status_code == 403


def test_only_the_lead_rotates_the_invite(client, make, db):
    event = make.event("hack")
    lead, member = make.user("lead@x.org"), make.user("m@x.org")
    team = make.team(event, lead, member)
    old = team.invite_code
    assert as_user(client, make, member).post("/events/hack/team/rotate-invite").status_code == 403
    as_user(client, make, lead).post("/events/hack/team/rotate-invite")
    db.expire_all()
    assert db.get(type(team), team.id).invite_code != old


def test_gallery_hides_drafts_and_flagged_duplicates(client, make, db):
    from app.models import ProjectStatus
    event = make.event("hack", open_=False)
    t1, t2 = make.team(event, make.user("a@x.org"), name="A"), make.team(event, make.user("b@x.org"), name="B")
    live = Project(event_id=event.id, team_id=t1.id, title="Visible", status=ProjectStatus.SUBMITTED)
    db.add(live)
    db.flush()
    db.add_all([
        Project(event_id=event.id, team_id=t2.id, title="Drafty", status=ProjectStatus.DRAFT),
        Project(event_id=event.id, team_id=t1.id, title="Copy", status=ProjectStatus.SUBMITTED,
                duplicate_of_id=live.id),
    ])
    db.commit()
    client.cookies.clear()
    body = client.get("/projects").text
    assert "Visible" in body and "Drafty" not in body and "Copy" not in body
    assert "Visible" in client.get("/projects?q=visi").text
    assert "Visible" not in client.get("/projects?q=nothing-like-it").text
