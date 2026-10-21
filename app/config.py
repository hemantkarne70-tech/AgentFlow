
import logging
import os

from dotenv import load_dotenv

# Load local .env values without overriding variables set by the environment.
load_dotenv(override=False)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be a boolean value.")


APP_NAME = os.getenv("APP_NAME", "AgentFlow")
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agentflow.db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

DEBUG = _env_bool("DEBUG", default=False)

if APP_ENV not in {"development", "testing", "production"}:
    raise ValueError(
        "APP_ENV must be development, testing, or production."
    )

if LOG_LEVEL not in {
    "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
}:
    raise ValueError("LOG_LEVEL is not a valid logging level.")

if APP_ENV == "production" and DEBUG:
    raise ValueError("DEBUG must be false in production.")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)
