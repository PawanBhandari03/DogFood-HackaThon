"""Runtime settings, read once from the environment."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL", "postgresql+psycopg://dogfood:dogfood@localhost:5432/dogfood"))
    # "demo": seed fixtures, known passwords, fixed checker tokens.
    # "production": none of that; tokens are always random.
    mode: str = field(default_factory=lambda: _env("DOGFOOD_MODE", "demo"))
    fixtures_path: str = field(default_factory=lambda: _env("FIXTURES_PATH", "data/fixtures.json"))
    session_days: int = field(default_factory=lambda: int(_env("SESSION_DAYS", "14")))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "0") == "1")
    demo_password: str = field(default_factory=lambda: _env("DEMO_PASSWORD", "dogfood-demo"))

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"


settings = Settings()

SESSION_COOKIE = "session"
