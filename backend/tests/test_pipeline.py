import base64

import httpx
import pytest

from app.analysis import RootCauseAnalysis
from app.db import SessionLocal
from app.github_client import GitHubClient
from app.models import JobFailure, Repository, WorkflowFailure
from app.pipeline import run_failure_pipeline

API = "https://api.github.com"
JOBS_PATH = "/repos/octo/demo/actions/runs/42/jobs"
LOG_PATH = "/repos/octo/demo/actions/jobs/7/logs"
RUN_PATH = "/repos/octo/demo/actions/runs/42"
FILE_PATH = "/repos/octo/demo/contents/.github/workflows/ci.yml"
WORKFLOW_TEXT = "name: CI\n"

JOBS_BODY = {
    "jobs": [
        {
            "id": 7,
            "name": "test",
            "conclusion": "failure",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Run tests", "conclusion": "failure"},
            ],
        },
        {"id": 8, "name": "lint", "conclusion": "success", "steps": []},
    ]
}


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def analyze(self, log_text, files=None):
        self.calls += 1
        self.seen_files = files
        return RootCauseAnalysis(
            failure_type="test_failure",
            root_cause="int + str raises TypeError.",
            suggested_fix="Convert before adding.",
            confidence="high",
        )


def github_transport(log_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == JOBS_PATH:
            return httpx.Response(200, json=JOBS_BODY)
        if request.url.path == RUN_PATH:
            return httpx.Response(200, json={"path": ".github/workflows/ci.yml", "head_sha": "abc123"})
        if request.url.path == FILE_PATH:
            encoded = base64.b64encode(WORKFLOW_TEXT.encode()).decode()
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "content": encoded, "sha": "blob1"})
        if request.url.path == LOG_PATH:
            if log_status != 200:
                return httpx.Response(log_status, text="gone")
            return httpx.Response(200, text="TypeError: unsupported operand")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def seed_failure():
    with SessionLocal() as db:
        repo = Repository(github_repo_id=1, full_name="octo/demo")
        failure = WorkflowFailure(
            github_run_id=42,
            repository=repo,
            workflow_name="CI",
            branch="main",
            conclusion="failure",
            status="completed",
        )
        db.add_all([repo, failure])
        db.commit()
        return failure.id


def client(log_status=200):
    return GitHubClient("test-token", API, transport=github_transport(log_status))


def test_client_lists_only_failed_jobs_with_failed_step():
    failed = client().list_failed_jobs("octo/demo", 42)

    assert len(failed) == 1
    assert failed[0].github_job_id == 7
    assert failed[0].job_name == "test"
    assert failed[0].failed_step == "Run tests"


def test_client_requires_token():
    from app.github_client import GitHubError

    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        GitHubClient("", API)


def test_pipeline_fetches_log_and_sends_to_review():
    failure_id = seed_failure()
    provider = FakeProvider()

    run_failure_pipeline(failure_id, client(), provider)

    with SessionLocal() as db:
        job = db.query(JobFailure).one()
        assert job.status == "PENDING_REVIEW"
        assert job.failed_step == "Run tests"
        assert job.raw_log == "TypeError: unsupported operand"
        assert job.failure_type == "test_failure"
    assert provider.calls == 1


def test_pipeline_is_idempotent_on_redelivery():
    failure_id = seed_failure()
    provider = FakeProvider()

    run_failure_pipeline(failure_id, client(), provider)
    run_failure_pipeline(failure_id, client(), provider)

    with SessionLocal() as db:
        assert db.query(JobFailure).count() == 1
    assert provider.calls == 1


def test_expired_log_marks_job_as_error_without_crashing():
    failure_id = seed_failure()
    provider = FakeProvider()

    run_failure_pipeline(failure_id, client(log_status=410), provider)

    with SessionLocal() as db:
        job = db.query(JobFailure).one()
        assert job.status == "ERROR"
        assert "410" in job.error_summary
    assert provider.calls == 0


def test_webhook_schedules_pipeline_only_when_token_is_set(client, monkeypatch):
    from app import github
    from app.config import settings

    scheduled = []
    monkeypatch.setattr(github, "run_failure_pipeline_from_settings", scheduled.append)
    body = {
        "action": "completed",
        "repository": {"id": 1, "full_name": "octo/demo", "default_branch": "main"},
        "workflow_run": {
            "id": 42,
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
        },
    }
    headers = {"X-GitHub-Event": "workflow_run"}

    monkeypatch.setattr(settings, "github_token", "")
    client.post("/api/github/webhook", json=body, headers=headers)
    assert scheduled == []

    monkeypatch.setattr(settings, "github_token", "test-token")
    client.post("/api/github/webhook", json=body, headers=headers)
    assert len(scheduled) == 1


def test_pipeline_sends_the_failed_workflow_file_to_the_model():
    failure_id = seed_failure()
    provider = FakeProvider()

    run_failure_pipeline(failure_id, client(), provider)

    assert provider.seen_files == {".github/workflows/ci.yml": WORKFLOW_TEXT}
    with SessionLocal() as db:
        job = db.query(JobFailure).one()
        assert job.workflow_path == ".github/workflows/ci.yml"
        assert job.head_sha == "abc123"
