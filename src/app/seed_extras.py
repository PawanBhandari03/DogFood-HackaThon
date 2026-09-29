"""Small, idempotent batch of dummy data for the Practice Jam event.

Called from seed_demo(), so every boot (local or Railway) has something to
show on the organizer, judge, community and webhook screens.
"""

from datetime import timedelta

from sqlalchemy import select

from app.auth import grant_role
from app.models import (
    Assignment, Comment, Event, JudgeInvite, JudgeTrack, Project, ProjectStatus, Role,
    Team, TeamMember, Track, User, Vote, Webhook, WebhookDelivery, utcnow,
)
from app.security import new_token
from app.services import audit

PEOPLE = [
    ("alex.morgan@example.org", "Alex Morgan"),
    ("maya.patel@example.org", "Maya Patel"),
    ("chen.wei@example.org", "Chen Wei"),
    ("samira.khan@example.org", "Samira Khan"),
    ("liam.obrien@example.org", "Liam O'Brien"),
    ("zara.almansoor@example.org", "Zara Al-Mansoor"),
]
JUDGES = [("nora.field@example.org", "Nora Field"), ("kai.tanaka@example.org", "Kai Tanaka")]

PROJECTS = [
    ("PulseDev", 0, 1, "Developer tools", "OmniPulse",
     "Real-time distributed tracing with zero agent overhead.",
     "Streams trace spans from ASGI apps with nanosecond precision without hurting throughput.",
     "https://github.com/pulsedev/omnipulse", "https://omnipulse-demo.dev"),
    ("CivicAI", 2, 3, "Civic tech", "CivicLens",
     "Street hazard detector using citizen dashcam footage.",
     "Clusters road damage from dashcam clips into ranked repair maps for city departments.",
     "https://github.com/civicai/civiclens", "https://civiclens.city"),
    ("OpenBudgeteers", 4, 5, "Open data", "DataWeave",
     "Turns messy municipal budget PDFs into queryable open data.",
     "OCR and layout models extract ledger rows and expose them through a standard API.",
     "https://github.com/openbudgeteers/dataweave", "https://dataweave-open.org"),
]


def seed_practice_extras(db) -> None:
    event = db.scalar(select(Event).where(Event.slug == "practice-jam"))
    if event is None or db.scalar(select(Project.id).where(Project.event_id == event.id).limit(1)):
        return  # not there, or already populated
    now = utcnow()
    organizer = db.scalar(select(User).where(User.email == "organizer@dogfood.local"))
    pw = organizer.password_hash

    def user(email, name):
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, name=name, password_hash=pw)
            db.add(u)
            db.flush()
        return u

    people = [user(e, n) for e, n in PEOPLE]
    judges = [user(e, n) for e, n in JUDGES]
    tracks = {t.name: t for t in db.scalars(select(Track).where(Track.event_id == event.id))}

    projects = []
    for team_name, lead, member, track, title, summary, desc, repo, demo in PROJECTS:
        team = Team(event_id=event.id, name=team_name, invite_code=new_token(12))
        db.add(team)
        db.flush()
        for idx, is_lead in ((lead, True), (member, False)):
            db.add(TeamMember(team_id=team.id, user_id=people[idx].id, event_id=event.id, is_lead=is_lead))
            grant_role(db, people[idx], event, Role.PARTICIPANT)
        proj = Project(event_id=event.id, team_id=team.id, track_id=tracks[track].id, title=title,
                       summary=summary, description=desc, repo_url=repo, demo_url=demo,
                       status=ProjectStatus.SUBMITTED, submitted_at=now - timedelta(hours=5))
        db.add(proj)
        projects.append(proj)
    db.flush()

    for j, judge in enumerate(judges):
        grant_role(db, judge, event, Role.JUDGE)
        for t in tracks.values():
            db.add(JudgeTrack(event_id=event.id, user_id=judge.id, track_id=t.id))
        for proj in projects[j:j + 2]:
            db.add(Assignment(event_id=event.id, judge_id=judge.id, project_id=proj.id, method="auto"))

    for email in ("guest.judge1@example.org", "guest.judge2@example.org"):
        db.add(JudgeInvite(event_id=event.id, email=email, token=new_token(24),
                           track_ids=[t.id for t in tracks.values()]))

    for proj, author, body in [
        (projects[0], people[2], "The trace visualizations are incredibly fast. How do you handle clock drift?"),
        (projects[1], people[1], "Tried it on a 4K dashcam clip and it tagged 3 potholes. Very cool."),
        (projects[2], people[0], "Budget PDFs are the worst. Huge kudos for tackling this."),
    ]:
        db.add(Comment(project_id=proj.id, user_id=author.id, body=body, created_at=now - timedelta(minutes=30)))

    for proj, voter in [(projects[0], people[2]), (projects[0], people[3]), (projects[1], people[0]),
                        (projects[1], people[5]), (projects[2], people[1]), (projects[2], people[4])]:
        db.add(Vote(event_id=event.id, project_id=proj.id, user_id=voter.id, ip="127.0.0.1",
                    created_at=now - timedelta(minutes=15)))

    wh = Webhook(event_id=event.id, url="https://webhook.site/demo-broadsheet",
                 secret="whsec_practice_jam_demo", is_active=True,
                 subscribed_events=["project.submitted", "comment.posted", "vote.cast"],
                 created_at=now - timedelta(days=1))
    db.add(wh)
    db.flush()
    for etype, payload, hrs in [("project.submitted", {"project": "OmniPulse"}, 4),
                                ("comment.posted", {"project": "CivicLens"}, 1)]:
        db.add(WebhookDelivery(webhook_id=wh.id, event_type=etype, payload=payload, status_code=200,
                               succeeded=True, attempted_at=now - timedelta(hours=hrs)))

    audit.record(db, "demo.dummy_data_added", actor=organizer, event_id=event.id,
                 entity_type="event", entity_id=event.slug, detail={"projects": len(projects)})
