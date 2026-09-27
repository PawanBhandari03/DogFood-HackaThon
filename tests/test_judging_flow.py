"""Organizer and judge flows end to end: invite, assign, score, publish."""

from sqlalchemy import select

from app.models import Assignment, AssignmentStatus, JudgeTrack, Project, ProjectStatus, Role
from app.services.assignment import plan_assignments
from conftest import as_user


def _event_with_projects(make, db, n=4):
    event = make.event("hack", open_=False)
    projects = []
    for i in range(n):
        team = make.team(event, make.user(f"p{i}@x.org"), name=f"T{i}")
        p = Project(event_id=event.id, team_id=team.id, title=f"P{i}", status=ProjectStatus.SUBMITTED,
                    track_id=event.tracks[i % 2].id)
        db.add(p)
        projects.append(p)
    db.commit()
    return event, projects


def test_invite_is_bound_to_its_email(client, make, db):
    event, _ = _event_with_projects(make, db)
    org, judge, other = make.user("org@x.org"), make.user("judge@x.org"), make.user("other@x.org")
    make.role(org, event, Role.ORGANIZER)
    as_user(client, make, org).post("/events/hack/manage/judges/invite",
                                    data={"email": "judge@x.org", "tracks": [str(event.tracks[0].id)]})
    from app.models import JudgeInvite
    token = db.scalar(select(JudgeInvite.token))
    assert as_user(client, make, other).post(f"/judge/invite/{token}").status_code == 403
    assert as_user(client, make, judge).post(f"/judge/invite/{token}", follow_redirects=False).status_code == 303
    assert db.scalar(select(JudgeTrack.track_id).where(JudgeTrack.user_id == judge.id)) == event.tracks[0].id


def test_auto_assignment_respects_tracks_conflicts_and_balance(make, db):
    event, projects = _event_with_projects(make, db, n=6)
    judges = [make.user(f"j{i}@x.org") for i in range(4)]
    for i, j in enumerate(judges):
        make.role(j, event, Role.JUDGE)
        db.add(JudgeTrack(event_id=event.id, user_id=j.id, track_id=event.tracks[i % 2].id))
    db.commit()
    plan = plan_assignments(db, event, per_project=2, seed=7)
    assert len(plan.created) == 12 and not plan.unfilled and not plan.cross_track
    track_of = {j.id: event.tracks[i % 2].id for i, j in enumerate(judges)}
    by_project = {p.id: p for p in projects}
    for judge_id, project_id in plan.created:
        assert track_of[judge_id] == by_project[project_id].track_id
    load = {}
    for judge_id, _ in plan.created:
        load[judge_id] = load.get(judge_id, 0) + 1
    assert max(load.values()) - min(load.values()) <= 1
    # same seed, same plan
    assert plan_assignments(db, event, per_project=2, seed=7).created == plan.created


def test_judge_never_assigned_to_own_team(make, db):
    event, projects = _event_with_projects(make, db, n=1)
    member = db.scalar(select(Project).where(Project.id == projects[0].id)).team.members[0].user
    make.role(member, event, Role.JUDGE)
    plan = plan_assignments(db, event, per_project=1, seed=1)
    assert (member.id, projects[0].id) not in plan.created


def test_score_submission_validates_and_marks_done(client, make, db):
    event, projects = _event_with_projects(make, db, n=1)
    judge = make.user("judge@x.org")
    make.role(judge, event, Role.JUDGE)
    a = Assignment(event_id=event.id, judge_id=judge.id, project_id=projects[0].id)
    db.add(a)
    db.commit()
    c = as_user(client, make, judge)
    crit = event.criteria
    bad = {f"c{crit[0].id}": "9", f"c{crit[1].id}": "3"}
    assert c.post(f"/judge/assignments/{a.id}", data=bad).status_code == 422
    good = {f"c{crit[0].id}": "5", f"c{crit[1].id}": "3", "comment": "nice"}
    assert c.post(f"/judge/assignments/{a.id}", data=good, follow_redirects=False).status_code == 303
    db.expire_all()
    a = db.get(Assignment, a.id)
    assert a.status == AssignmentStatus.DONE
    assert sorted(v.value for v in a.score.values) == [3, 5]


def test_rubric_weights_change_results(client, make, db):
    event, projects = _event_with_projects(make, db, n=2)
    org = make.user("org@x.org")
    make.role(org, event, Role.ORGANIZER)
    c = as_user(client, make, org)
    f, q = event.criteria
    r = c.post("/events/hack/manage/rubric", data={f"w{f.id}": "3", f"w{q.id}": "1"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert [c.weight for c in db.get(type(event), event.id).criteria] == [3.0, 1.0]
    r = c.post("/events/hack/manage/rubric", data={f"w{f.id}": "0", f"w{q.id}": "0"})
    assert r.status_code == 422
