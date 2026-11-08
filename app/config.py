"""Application settings read from the environment."""

import os


DATABASE_URL_ENV = "POS_DATABASE_URL"
AUTH_SECRET_ENV = "POS_AUTH_SECRET"
BOOTSTRAP_TOKEN_ENV = "POS_BOOTSTRAP_TOKEN"


def database_url() -> str:
    """Return the configured database URL without creating a database."""
    return os.environ.get(DATABASE_URL_ENV, "sqlite:///C:/JayaSurabhiPOS/data/pos.db")


def auth_secret() -> str | None:
    return os.environ.get(AUTH_SECRET_ENV)


def bootstrap_token() -> str | None:
    return os.environ.get(BOOTSTRAP_TOKEN_ENV)
