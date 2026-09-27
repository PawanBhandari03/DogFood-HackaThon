"""Role isolation, tested the way the checker (and an attacker) does it:
plain HTTP requests with someone else's session. Nothing here goes through a
template, so a passing test means the backend refuses."""

from sqlalchemy import select

from app.models import Assignment, AuditEntry, Project, ProjectStatus, Role, Score, ScoreValue, User
from conftest import as_user


def _setup(make, db):
    event = make.event("hack", open_=False)
    judge_a, judge_b = make.user("a@x.org"), make.user("b@x.org")
    for j in (judge_a, judge_b):
        make.role(j, event, Role.JUDGE)
    organizer, admin = make.user("org@x.org"), make.user("admin@x.org", is_admin=True)
    make.role(organizer, event, Role.ORGANIZER)
    participant = make.user("p@x.org")
    team = make.team(event, participant)
    project = Project(event_id=event.id, team_id=team.id, title="Thing", status=ProjectStatus.SUBMITTED)
    db.add(project)
    db.flush()
    a = Assignment(event_id=event.id, judge_id=judge_a.id, project_id=project.id, status="done")
    db.add(a)
    db.flush()
    score = Score(assignment_id=a.id, comment="secret comment")
    score.values = [ScoreValue(criterion_id=c.id, value=4) for c in event.criteria]
    db.add(score)
    db.commit()
    return dict(event=event, judge_a=judge_a, judge_b=judge_b, organizer=organizer, admin=admin,
                participant=participant, assignment=a)


def test_judge_reads_own_scores(client, make, db):
    s = _setup(make, db)
    r = as_user(client, make, s["judge_a"]).get("/api/judge/scores")
    assert r.status_code == 200
    assert r.json()["scores"][0]["comment"] == "secret comment"


def test_other_judge_is_refused_and_audited(client, make, db):
    s = _setup(make, db)
    ref = s["judge_a"].handle
    r = as_user(client, make, s["judge_b"]).get(f"/api/judges/{ref}/scores")
    assert r.status_code == 403
    assert "secret comment" not in r.text
    denied = db.scalars(select(AuditEntry).where(AuditEntry.action == "denied.peer_scores")).all()
    assert len(denied) == 1 and denied[0].actor_id == s["judge_b"].id


def test_own_scores_do_not_include_peers(client, make, db):
    s = _setup(make, db)
    r = as_user(client, make, s["judge_b"]).get("/api/judge/scores")
    assert r.status_code == 200
    assert r.json()["scores"] == []


def test_participant_and_visitor_blocked(client, make, db):
    s = _setup(make, db)
    assert as_user(client, make, s["participant"]).get("/api/judge/scores").status_code == 403
    client.cookies.clear()
    assert client.get("/api/judge/scores").status_code == 401
    assert client.get(f"/api/judges/{s['judge_a'].handle}/scores").status_code == 401


def test_unknown_judge_does_not_leak_existence_to_judges(client, make, db):
    s = _setup(make, db)
    assert as_user(client, make, s["judge_b"]).get("/api/judges/nobody/scores").status_code == 403


def test_organizer_and_admin_can_read_a_judge(client, make, db):
    s = _setup(make, db)
    assert as_user(client, make, s["organizer"]).get(f"/api/judges/{s['judge_a'].handle}/scores").status_code == 200
    assert as_user(client, make, s["admin"]).get(f"/api/judges/{s['judge_a'].handle}/scores").status_code == 200


def test_organizer_of_another_event_sees_nothing_from_this_one(client, make, db):
    s = _setup(make, db)
    other = make.event("other")
    stranger = make.user("stranger@x.org")
    make.role(stranger, other, Role.ORGANIZER)
    r = as_user(client, make, stranger).get(f"/api/judges/{s['judge_a'].handle}/scores")
    assert r.status_code == 200 and r.json()["scores"] == []


def test_score_form_of_another_judge_is_refused(client, make, db):
    s = _setup(make, db)
    c = as_user(client, make, s["judge_b"])
    assert c.get(f"/judge/assignments/{s['assignment'].id}").status_code == 403
    r = c.post(f"/judge/assignments/{s['assignment'].id}", data={"comment": "overwrite"})
    assert r.status_code == 403
    db.expire_all()
    assert db.get(Score, 1).comment == "secret comment"


def test_exports_are_organizer_only(client, make, db):
    s = _setup(make, db)
    url = "/api/events/hack/export/results.csv"
    for who in ("judge_a", "participant"):
        assert as_user(client, make, s[who]).get(url).status_code == 403
    r = as_user(client, make, s["organizer"]).get(url)
    assert r.status_code == 200 and r.text.splitlines()[0].startswith("rank,")


def test_organizer_pages_refuse_non_organizers(client, make, db):
    s = _setup(make, db)
    for who in ("judge_a", "participant"):
        c = as_user(client, make, s[who])
        for page in ("", "/results", "/audit", "/assignments"):
            assert c.get(f"/events/hack/manage{page}").status_code == 403
        assert c.post("/events/hack/manage/results/publish", data={"action": "publish"}).status_code == 403


def test_results_hidden_until_published(client, make, db):
    s = _setup(make, db)
    client.cookies.clear()
    assert client.get("/api/events/hack/results").status_code == 403
    assert as_user(client, make, s["judge_a"]).get("/api/events/hack/results").status_code == 403
    org = as_user(client, make, s["organizer"])
    assert org.post("/events/hack/manage/results/publish", data={"action": "publish"},
                    follow_redirects=False).status_code == 303
    client.cookies.clear()
    assert client.get("/api/events/hack/results").status_code == 200


def test_judging_locked_after_publish(client, make, db):
    s = _setup(make, db)
    as_user(client, make, s["organizer"]).post("/events/hack/manage/results/publish", data={"action": "publish"})
    c = as_user(client, make, s["judge_a"])
    form = {f"c{crit.id}": "5" for crit in s["event"].criteria}
    assert c.post(f"/judge/assignments/{s['assignment'].id}", data=form).status_code == 403


def test_bearer_token_works_like_the_cookie(client, make, db):
    s = _setup(make, db)
    client.cookies.clear()
    token = make.token(s["judge_a"])
    r = client.get("/api/judge/scores", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_expired_or_forged_session_is_anonymous(client, make, db):
    _setup(make, db)
    client.cookies.clear()
    client.cookies.set("session", "forged-token")
    assert client.get("/api/judge/scores").status_code == 401
    assert db.scalar(select(User).where(User.email == "a@x.org")) is not None
