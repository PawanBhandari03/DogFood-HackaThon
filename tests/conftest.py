"""Tests run against a real Postgres database (`dogfood_test`), migrated with
the same Alembic migrations production uses. Every test starts from empty
tables.

Run them with:  docker compose run --rm app pytest
"""

import os

TEST_URL = os.environ.get("TEST_DATABASE_URL",
                          "postgresql+psycopg://dogfood:dogfood@localhost:5432/dogfood_test")
os.environ["DATABASE_URL"] = TEST_URL  # must happen before app.db is imported

from datetime import timedelta  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import grant_role  # noqa: E402
from app.config import SESSION_COOKIE  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Event,
    Role,
    RubricCriterion,
    Team,
    TeamMember,
    Track,
    User,
    utcnow,
)
from app.security import create_session, hash_password, new_token  # noqa: E402
from app.services.ratelimit import login_limiter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _create_database() -> None:
    plain = TEST_URL.replace("postgresql+psycopg://", "postgresql://")
    base, name = plain.rsplit("/", 1)
    with psycopg.connect(f"{base}/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session", autouse=True)
def migrated():
    _create_database()
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "src", "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean(migrated):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    login_limiter.reset()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


_PW = hash_password("password123")


class Factory:
    """Small helpers that build rows and log people in."""

    def __init__(self, db):
        self.db = db

    def user(self, email: str, name: str | None = None, **flags) -> User:
        u = User(email=email.lower(), name=name or email.split("@")[0], password_hash=_PW, **flags)
        self.db.add(u)
        self.db.commit()
        return u

    def event(self, slug: str = "hack", *, open_: bool = True, tracks=("Alpha", "Beta")) -> Event:
        now = utcnow()
        start = now - timedelta(days=1) if open_ else now - timedelta(days=5)
        close = now + timedelta(days=2) if open_ else now - timedelta(days=1)
        e = Event(slug=slug, name=slug.title(), starts_at=start, submissions_close_at=close)
        self.db.add(e)
        self.db.flush()
        for i, t in enumerate(tracks):
            self.db.add(Track(event_id=e.id, name=t, position=i))
        for i, key in enumerate(["functionality", "quality"]):
            self.db.add(RubricCriterion(event_id=e.id, key=key, label=key.title(), position=i))
        self.db.commit()
        return e

    def role(self, user: User, event: Event, role: str) -> None:
        grant_role(self.db, user, event, role)
        self.db.commit()

    def team(self, event: Event, *members: User, name: str = "Team") -> Team:
        t = Team(event_id=event.id, name=name, invite_code=new_token(12))
        self.db.add(t)
        self.db.flush()
        for i, m in enumerate(members):
            self.db.add(TeamMember(team_id=t.id, user_id=m.id, event_id=event.id, is_lead=i == 0))
            grant_role(self.db, m, event, Role.PARTICIPANT)
        self.db.commit()
        return t

    def token(self, user: User) -> str:
        token = create_session(self.db, user)
        self.db.commit()
        return token

    def cookies(self, user: User) -> dict:
        return {SESSION_COOKIE: self.token(user)}


@pytest.fixture
def make(db):
    return Factory(db)


def as_user(client: TestClient, make: Factory, user: User) -> TestClient:
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make.token(user))
    return client
