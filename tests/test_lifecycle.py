"""One full event lifecycle through the real pages, as the demo video shows it:
create, submit, judge, publish."""

from datetime import timedelta

from sqlalchemy import select

from app.models import Assignment, Event, JudgeInvite, Project, User, utcnow
from app.util import input_dt


def _login(client, email, password="password123"):
    client.cookies.clear()
    r = client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text


def test_create_submit_judge_publish(client, make, db):
    make.user("org@x.org", "Olu", can_create_events=True)

    # 1. The organizer creates an event that is open now.
    _login(client, "org@x.org")
    now = utcnow()
    r = client.post("/events/new", data={
        "name": "Weekend Jam", "tagline": "t",
        "starts_at": input_dt(now - timedelta(hours=1)),
        "submissions_close_at": input_dt(now + timedelta(days=1)),
        "tracks": "Tools\nClimate"}, follow_redirects=False)
    assert r.status_code == 303
    event = db.scalar(select(Event).where(Event.slug == "weekend-jam"))
    track_id = event.tracks[0].id

    # 2. Two participants register; one starts a team, the other joins by link.
    for email, name in (("ana@x.org", "Ana"), ("ben@x.org", "Ben")):
        client.cookies.clear()
        assert client.post("/register", data={"name": name, "email": email, "password": "password123"},
                           follow_redirects=False).status_code == 303
    _login(client, "ana@x.org")
    client.post("/events/weekend-jam/team", data={"name": "Owls"})
    code = client.get("/events/weekend-jam/team").text.split("/join/")[1].split("<")[0].strip()
    _login(client, "ben@x.org")
    assert client.post(f"/join/{code}", follow_redirects=False).status_code == 303

    # 3. Ben drafts and submits; the project appears in the gallery.
    client.post("/events/weekend-jam/project", data={"title": "Night Owl", "summary": "Sleep tracker",
                                                     "repo_url": "https://example.org/owl",
                                                     "track_id": str(track_id)})
    assert "Night Owl" not in client.get("/projects").text          # still a draft
    client.post("/events/weekend-jam/project/submit")
    assert "Night Owl" in client.get("/projects?q=owl").text

    # 4. The organizer invites a judge, who accepts; assignment runs.
    make.user("judy@x.org", "Judy")
    _login(client, "org@x.org")
    client.post("/events/weekend-jam/manage/judges/invite", data={"email": "judy@x.org", "tracks": [str(track_id)]})
    token = db.scalar(select(JudgeInvite.token))
    _login(client, "judy@x.org")
    client.post(f"/judge/invite/{token}")
    _login(client, "org@x.org")
    client.post("/events/weekend-jam/manage/assignments/auto", data={"per_project": "1", "seed": "1"})
    assignment = db.scalar(select(Assignment))
    assert assignment is not None

    # 5. Publishing is refused while submissions are open.
    assert client.post("/events/weekend-jam/manage/results/publish", data={"action": "publish"}).status_code == 409

    # 6. The judge scores.
    _login(client, "judy@x.org")
    db.expire_all()
    crit = db.get(Event, event.id).criteria
    form = {f"c{c.id}": "4" for c in crit} | {"comment": "Good", "then": "next"}
    assert client.post(f"/judge/assignments/{assignment.id}", data=form, follow_redirects=False).status_code == 303

    # 7. The deadline passes (organizer moves it), nothing can change any more,
    #    results are published and become public.
    _login(client, "org@x.org")
    past = utcnow() - timedelta(minutes=1)
    client.post("/events/weekend-jam/manage/settings", data={
        "name": "Weekend Jam", "starts_at": input_dt(past - timedelta(hours=2)),
        "submissions_close_at": input_dt(past), "max_team_size": "4", "reviews_per_project": "1",
        "shrinkage_k": "3"})
    _login(client, "ben@x.org")
    assert client.post("/events/weekend-jam/project", data={"title": "Too late"}).status_code == 403
    client.cookies.clear()
    assert client.get("/events/weekend-jam/results").status_code == 403
    _login(client, "org@x.org")
    client.post("/events/weekend-jam/manage/results/publish", data={"action": "publish"})
    client.cookies.clear()
    page = client.get("/events/weekend-jam/results")
    assert page.status_code == 200 and "Night Owl" in page.text

    # The audit log recorded the story.
    _login(client, "org@x.org")
    log = client.get("/events/weekend-jam/manage/audit").text
    for action in ("event.created", "team.created", "team.joined", "project.submitted",
                   "judge.invited", "assignments.auto", "score.submitted", "results.published"):
        assert action in log, action
    assert db.scalar(select(Project.title)) == "Night Owl"
    assert db.scalar(select(User).where(User.email == "judy@x.org")) is not None
