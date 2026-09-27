"""Script to populate rich dummy data into the portal:
- Projects & teams for Practice Jam
- Comments on projects
- Community votes
- Sample webhook configuration
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.db import SessionLocal
from app.models import (
    Event,
    Project,
    ProjectStatus,
    Team,
    TeamMember,
    Track,
    User,
    Comment,
    Vote,
    Webhook,
    WebhookDelivery,
    Role,
    utcnow,
)
from app.auth import grant_role
from app.security import hash_password, new_token


def seed():
    with SessionLocal() as db:
        now = utcnow()
        pw_hash = hash_password("dogfood-demo")

        # 1. Create or get dummy users
        dummy_users_data = [
            ("alex.morgan@example.org", "Alex Morgan"),
            ("maya.patel@example.org", "Maya Patel"),
            ("chen.wei@example.org", "Chen Wei"),
            ("samira.khan@example.org", "Samira Khan"),
            ("liam.obrien@example.org", "Liam O'Brien"),
            ("zara.al-mansoor@example.org", "Zara Al-Mansoor"),
            ("elena.rostova@example.org", "Elena Rostova"),
            ("marcus.thorne@example.org", "Marcus Thorne"),
        ]

        users = {}
        for email, name in dummy_users_data:
            user = db.query(User).filter_by(email=email).first()
            if not user:
                user = User(email=email, name=name, password_hash=pw_hash)
                db.add(user)
                db.flush()
            users[email] = user

        # 2. Get Practice Jam Event
        pj = db.query(Event).filter_by(slug="practice-jam").first()
        if not pj:
            print("Practice jam not found!")
            return

        tracks = {t.name: t for t in db.query(Track).filter_by(event_id=pj.id).all()}

        # 3. Create dummy projects in Practice Jam
        sample_projects = [
            {
                "team": "PulseDev",
                "lead": users["alex.morgan@example.org"],
                "member": users["maya.patel@example.org"],
                "track": tracks.get("Developer tools"),
                "title": "OmniPulse",
                "summary": "Real-time distributed tracing with zero agent overhead.",
                "desc": "OmniPulse integrates with ASGI web frameworks to stream trace spans with nanosecond precision without impacting throughput. Built with Rust and Python.",
                "repo": "https://github.com/pulsedev/omnipulse",
                "demo": "https://omnipulse-demo.dev",
            },
            {
                "team": "CivicAI",
                "lead": users["chen.wei@example.org"],
                "member": users["samira.khan@example.org"],
                "track": tracks.get("Civic tech"),
                "title": "CivicLens",
                "summary": "AI-powered urban pothole and street hazard detector using citizen dashcam footage.",
                "desc": "CivicLens automatically processes street cameras and dashcam clips to cluster roadway damage, providing municipal departments with prioritize-ranked repair maps.",
                "repo": "https://github.com/civicai/civiclens",
                "demo": "https://civiclens.city",
            },
            {
                "team": "OpenBudgeteers",
                "lead": users["liam.obrien@example.org"],
                "member": users["zara.al-mansoor@example.org"],
                "track": tracks.get("Open data"),
                "title": "DataWeave",
                "summary": "Pipeline for turning messy municipal budget PDFs into queryable open data.",
                "desc": "Uses OCR and layout transformers to extract tabular ledger rows from scanned city council budget reports, exposing a standardized GraphQL endpoint.",
                "repo": "https://github.com/openbudgeteers/dataweave",
                "demo": "https://dataweave-open.org",
            },
            {
                "team": "CommitCraft",
                "lead": users["elena.rostova@example.org"],
                "member": users["marcus.thorne@example.org"],
                "track": tracks.get("Developer tools"),
                "title": "GitSift",
                "summary": "Automated visual code review and semantic git commit summarizer.",
                "desc": "GitSift generates side-by-side architecture impact maps for large PRs, helping maintainers spot security flaws and unintended architectural drift.",
                "repo": "https://github.com/commitcraft/gitsift",
                "demo": "https://gitsift.app",
            },
            {
                "team": "MeshRescue",
                "lead": users["maya.patel@example.org"],
                "member": users["alex.morgan@example.org"],
                "track": tracks.get("Civic tech"),
                "title": "NeighbourNet",
                "summary": "Hyperlocal community emergency preparedness bulletin mesh.",
                "desc": "A zero-cloud, peer-to-peer web app using WebRTC and local Bluetooth mesh to coordinate supply distribution and medical checks during network blackouts.",
                "repo": "https://github.com/meshrescue/neighbournet",
                "demo": "https://neighbournet.local",
            },
            {
                "team": "TransitLogic",
                "lead": users["zara.al-mansoor@example.org"],
                "member": users["liam.obrien@example.org"],
                "track": tracks.get("Open data"),
                "title": "EcoTransit",
                "summary": "Live carbon-optimized public transit routing using open GTFS feeds.",
                "desc": "Calculates real-world lifecycle carbon footprints for multimodal commutes, benchmarking bus, subway, bike-share, and EV fleets.",
                "repo": "https://github.com/transitlogic/ecotransit",
                "demo": "https://ecotransit.org",
            },
        ]

        created_projects = []
        for pdata in sample_projects:
            # Check if project already exists
            proj = db.query(Project).filter_by(event_id=pj.id, title=pdata["title"]).first()
            if not proj:
                # Create team
                t = Team(
                    event_id=pj.id,
                    name=pdata["team"],
                    invite_code=new_token(12),
                )
                db.add(t)
                db.flush()

                # Add members
                db.add(TeamMember(team_id=t.id, user_id=pdata["lead"].id, event_id=pj.id, is_lead=True))
                grant_role(db, pdata["lead"], pj, Role.PARTICIPANT)

                proj = Project(
                    event_id=pj.id,
                    team_id=t.id,
                    track_id=pdata["track"].id if pdata["track"] else None,
                    title=pdata["title"],
                    summary=pdata["summary"],
                    description=pdata["desc"],
                    repo_url=pdata["repo"],
                    demo_url=pdata["demo"],
                    status=ProjectStatus.SUBMITTED,
                    submitted_at=now - timedelta(hours=5),
                )
                db.add(proj)
                db.flush()
            created_projects.append(proj)

        # 4. Add Comments
        sample_comments = [
            (created_projects[0], users["chen.wei@example.org"], "The distributed trace visualizations are incredibly fast! How are you handling clock drift across instances?"),
            (created_projects[0], users["alex.morgan@example.org"], "Thanks Chen! We use Hybrid Logical Clocks (HLC) synced with monotonic timestamps to avoid clock skew."),
            (created_projects[1], users["maya.patel@example.org"], "Tested CivicLens on a sample 4K clip from our dashcam and it accurately tagged 3 potholes! Really cool project."),
            (created_projects[2], users["alex.morgan@example.org"], "Turning government budget PDFs into structured tables is one of the most frustrating problems in civic data. Huge kudos!"),
            (created_projects[3], users["samira.khan@example.org"], "Great work on the semantic commit graph. Does it support mono-repos with multiple packages?"),
        ]

        for proj, user, body in sample_comments:
            exists = db.query(Comment).filter_by(project_id=proj.id, user_id=user.id, body=body).first()
            if not exists:
                comment = Comment(project_id=proj.id, user_id=user.id, body=body, created_at=now - timedelta(minutes=30))
                db.add(comment)

        # 5. Add Community Votes
        vote_pairs = [
            (created_projects[0], users["chen.wei@example.org"]),
            (created_projects[0], users["samira.khan@example.org"]),
            (created_projects[0], users["liam.obrien@example.org"]),
            (created_projects[1], users["alex.morgan@example.org"]),
            (created_projects[1], users["zara.al-mansoor@example.org"]),
            (created_projects[2], users["alex.morgan@example.org"]),
            (created_projects[2], users["chen.wei@example.org"]),
            (created_projects[3], users["maya.patel@example.org"]),
            (created_projects[4], users["liam.obrien@example.org"]),
            (created_projects[5], users["samira.khan@example.org"]),
        ]

        for proj, user in vote_pairs:
            has_vote = db.query(Vote).filter_by(event_id=pj.id, project_id=proj.id, user_id=user.id).first()
            if not has_vote:
                v = Vote(event_id=pj.id, project_id=proj.id, user_id=user.id, ip="127.0.0.1", created_at=now - timedelta(minutes=15))
                db.add(v)

        # 6. Add Webhook for Practice Jam
        existing_wh = db.query(Webhook).filter_by(event_id=pj.id).first()
        if not existing_wh:
            wh = Webhook(
                event_id=pj.id,
                url="https://webhook.site/demo-broadsheet-hackathon",
                secret="whsec_practice_jam_demo_secret_2026",
                subscribed_events=["project.submitted", "comment.posted", "vote.cast"],
                is_active=True,
                created_at=now - timedelta(days=1),
            )
            db.add(wh)
            db.flush()

            # Add sample deliveries
            d1 = WebhookDelivery(
                webhook_id=wh.id,
                event_type="project.submitted",
                payload={"event": "practice-jam", "project": "OmniPulse", "status": "submitted"},
                status_code=200,
                succeeded=True,
                attempted_at=now - timedelta(hours=4),
            )
            d2 = WebhookDelivery(
                webhook_id=wh.id,
                event_type="comment.posted",
                payload={"event": "practice-jam", "project": "CivicLens", "author": "maya.patel@example.org"},
                status_code=200,
                succeeded=True,
                attempted_at=now - timedelta(hours=1),
            )
            db.add_all([d1, d2])

        db.commit()
        print("Successfully seeded Practice Jam with rich dummy projects, comments, votes, and webhook data!")


if __name__ == "__main__":
    seed()
