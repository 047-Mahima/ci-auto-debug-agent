import os
import tempfile
from pathlib import Path

# The engine is created when app.db is imported, so the test database has to be
# configured before any app module is imported here.
_test_dir = tempfile.mkdtemp(prefix="ci_debug_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_test_dir, 'test.db').as_posix()}"
os.environ["GITHUB_WEBHOOK_SECRET"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    # Using TestClient as a context manager runs the startup hook.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "test-secret")
    return "test-secret"
