"""Shared isolated settings for automated tests."""

import os

import pytest

os.environ["POS_DATABASE_URL"] = "sqlite://"
os.environ["POS_AUTH_SECRET"] = "test-secret-value-that-is-at-least-32-characters"
os.environ["POS_BOOTSTRAP_TOKEN"] = "test-bootstrap-token"

from app.database import Base, engine


@pytest.fixture(autouse=True)
def reset_database() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
