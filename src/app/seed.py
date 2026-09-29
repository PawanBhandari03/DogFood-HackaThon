"""Runs on every boot (scripts/start.sh). Safe to run repeatedly.

In demo mode it imports fixtures.json, creates the demo accounts, a second
event that is open for submissions, and fixed session tokens for the
acceptance checker, then prints the four headers for .dogfood.toml.
In production mode it does nothing except create the first admin if asked.
"""

import json
import os
import sys
from datetime import timedelta

from sqlalchemy import delete, select

from app.auth import grant_role
from app.config import settings
from app.db import SessionLocal
from app.models import AuthSession, Event, Prize, Role, RubricCriterion, Track, User, utcnow
from app.security import create_session, hash_password, hash_token
from app.services import audit
from app.seed_extras import seed_practice_extras
from app.services.importer import import_event

# Fixed only in demo mode, so the checker and .dogfood.toml can rely on them.
CHECKER_TOKENS = {
    "organizer": "org_binarybuilders_demo",
    "judge_a": "jdg_a_binarybuilders_demo",
    "judge_b": "jdg_b_binarybuilders_demo",
    "participant": "prt_binarybuilders_demo",
}
# judge_a and judge_b are the two fixture judges with the most finished reviews.
CHECKER_USERS = {
    "organizer": "organizer@dogfood.local",
    "judge_a": "diego.herrera@example.org",   # jdg_24
    "judge_b": "jonas.vogel@example.org",     # jdg_26
    "participant": "priya1@example.org",      # tm_01
}


def _ensure_user(db, email, name, pw_hash, **flags) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name, password_hash=pw_hash, **flags)
        db.add(user)
        db.flush()
    else:
        for k, v in flags.items():
            setattr(user, k, v)
    return user


def _practice_event(db, organizer: User) -> Event:
    event = db.scalar(select(Event).where(Event.slug == "practice-jam"))
    if event:
        if event.voting_mode != "authenticated":
            event.voting_mode = "authenticated"
            event.voting_open_at = event.starts_at or utcnow()
            event.voting_close_at = event.submissions_close_at or (utcnow() + timedelta(days=30))
            event.max_votes = 3
        return event
    now = utcnow()
    event = Event(
        slug="practice-jam", name="Practice Jam",
        tagline="An open event to try the whole flow: team, submit, judge, publish.",
        description="Seeded in demo mode. Submissions are open for 30 days from first boot.",
        starts_at=now - timedelta(days=1), submissions_close_at=now + timedelta(days=30),
        voting_mode="authenticated",
        voting_open_at=now,
        voting_close_at=now + timedelta(days=30),
        max_votes=3,
        created_by_id=organizer.id,
    )
    db.add(event)
    db.flush()
    for i, name in enumerate(["Developer tools", "Civic tech", "Open data"]):
        db.add(Track(event_id=event.id, name=name, position=i))
    for i, (key, weight) in enumerate([("functionality", 0.4), ("quality", 0.3), ("innovation", 0.3)]):
        db.add(RubricCriterion(event_id=event.id, key=key, label=key.capitalize(), weight=weight, position=i))
    for i, (name, reward) in enumerate([("Grand prize", "Fork and adoption"), ("Runner up", "Honour")]):
        db.add(Prize(event_id=event.id, name=name, reward=reward, position=i))
    grant_role(db, organizer, event, Role.ORGANIZER)
    return event


def seed_demo() -> None:
    with SessionLocal() as db:
        pw_hash = hash_password(settings.demo_password)
        admin = _ensure_user(db, "admin@dogfood.local", "Ada Admin", pw_hash, is_admin=True)
        organizer = _ensure_user(db, "organizer@dogfood.local", "Olu Organizer", pw_hash,
                                 can_create_events=True)

        with open(settings.fixtures_path, encoding="utf-8") as f:
            data = json.load(f)
        report = import_event(db, data, password_hash=pw_hash)
        grant_role(db, organizer, report.event, Role.ORGANIZER)
        if report.created:
            audit.record(db, "fixtures.imported", actor=admin, event_id=report.event.id,
                         entity_type="event", entity_id=report.event.slug,
                         detail={"created": report.created, "duplicates": report.duplicates})
        _practice_event(db, organizer)
        db.flush()
        seed_practice_extras(db)

        for role, token in CHECKER_TOKENS.items():
            user = db.scalar(select(User).where(User.email == CHECKER_USERS[role]))
            db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
            create_session(db, user, label=f"checker:{role}", token=token, days=365)
        db.commit()

        print("\nseeded. fixtures:", ", ".join(f"{v} {k}" for k, v in report.created.items()) or "already imported")
        for line in report.duplicates:
            print("  flagged duplicate:", line)
        print(f"\nweb logins (password for every seeded account: {settings.demo_password})")
        print("  admin        admin@dogfood.local")
        print("  organizer    organizer@dogfood.local")
        print(f"  judge_a      {CHECKER_USERS['judge_a']}")
        print(f"  judge_b      {CHECKER_USERS['judge_b']}")
        print(f"  participant  {CHECKER_USERS['participant']}")
        print("\ntest logins for .dogfood.toml:")
        for role, token in CHECKER_TOKENS.items():
            print(f"  {role:<12} Cookie: session={token}")
        print(flush=True)


def seed_production() -> None:
    email, password = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if not (email and password):
        return
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.is_admin.is_(True))):
            return
        _ensure_user(db, email.lower(), "Admin", hash_password(password),
                     is_admin=True, can_create_events=True)
        db.commit()
        print(f"created admin {email}")


if __name__ == "__main__":
    seed_demo() if settings.is_demo else seed_production()
    sys.exit(0)
