"""Runtime settings, read once from the environment."""

import logging
import os
import secrets
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _instance_secret() -> str:
    """Read INSTANCE_SECRET from env.  If unset, generate a random value and
    warn: the random value changes on every restart, so any judge-record
    signatures issued before the restart will no longer verify.

    Operators MUST set ``INSTANCE_SECRET`` in production — see README.md.
    """
    val = os.environ.get("INSTANCE_SECRET", "")
    if not val:
        val = secrets.token_hex(32)
        logger.warning(
            "INSTANCE_SECRET is not set.  A random value will be used, which "
            "means judge-record signatures will change on every restart.  "
            "Set INSTANCE_SECRET in your environment for stable signatures."
        )
    return val


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL", "sqlite:///./dogfood.db"))
    # "demo": seed fixtures, known passwords, fixed checker tokens.
    # "production": none of that; tokens are always random.
    mode: str = field(default_factory=lambda: _env("DOGFOOD_MODE", "demo"))
    fixtures_path: str = field(default_factory=lambda: _env("FIXTURES_PATH", "data/fixtures.json"))
    session_days: int = field(default_factory=lambda: int(_env("SESSION_DAYS", "14")))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "0") == "1")
    demo_password: str = field(default_factory=lambda: _env("DEMO_PASSWORD", "dogfood-demo"))
    instance_secret: str = field(default_factory=_instance_secret)

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"


settings = Settings()

SESSION_COOKIE = "session"
