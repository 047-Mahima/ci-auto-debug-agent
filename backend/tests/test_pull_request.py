import base64
import json

import httpx
import pytest

from app import reviews
from app.config import settings
from app.db import SessionLocal
from app.github_client import GitHubClient
from app.models import JobFailure, Repository, WorkflowFailure
from app.pull_request import PullRequestError, open_draft_pr

API = "https://api.github.com"
WORKFLOW = ".github/workflows/ci.yml"
ORIGINAL = "name: CI\nsteps:\n  - run: exit 1\n"
FIXED = "name: CI\nsteps:\n  - run: exit 0\n"
KEY = "review-key"
HEADERS = {"X-API-Key": KEY}


class RecordingGitHub:
    """Fake GitHub that records write calls so tests can check what was sent."""

    def __init__(self, original=ORIGINAL):
        self.original = original
        self.writes = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/repos/octo/demo/contents/{WORKFLOW}":
            encoded = base64.b64encode(self.original.encode()).decode()
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "content": encoded, "sha": "blob1"})
        if request.method == "POST" and path == "/repos/octo/demo/git/refs":
            self.writes.append(("branch", json.loads(request.content)))
            return httpx.Response(201, json={})
        if request.method == "PUT" and path == f"/repos/octo/demo/contents/{WORKFLOW}":
            self.writes.append(("commit", json.loads(request.content)))
            return httpx.Response(201, json={})
        if request.method == "POST" and path == "/repos/octo/demo/pulls":
            self.writes.append(("pr", json.loads(request.content)))
            return httpx.Response(201, json={"html_url": "https://github.com/octo/demo/pull/9"})
        return httpx.Response(404)

    def client(self):
        return GitHubClient("test-token", API, transport=httpx.MockTransport(self.handler))


def seed_job(status="APPROVED", changes=None, workflow_path=WORKFLOW, pr_url=None):
    if changes is None:
        changes = [{"path": WORKFLOW, "content": FIXED}]
    with SessionLocal() as db:
        repo = Repository(github_repo_id=1, full_name="octo/demo", default_branch="main")
        failure = WorkflowFailure(
            github_run_id=42, repository=repo, workflow_name="CI",
            branch="main", conclusion="failure", status="completed",
        )
        job = JobFailure(
            workflow_failure=failure, github_job_id=7, job_name="test",
            failed_step="Run tests", head_sha="abc123", workflow_path=workflow_path,
            proposed_changes=json.dumps(changes) if changes is not None else None,
            status=status, failure_type="config_error", confidence="high",
            error_summary="Root cause: exit 1\nSuggested fix: exit 0",
            reviewed_by="mahima", review_note="looks right", pr_url=pr_url,
        )
        db.add_all([repo, failure, job])
        db.commit()
        return job.id


def test_opens_draft_pr_with_fix_on_a_new_branch():
    job_id = seed_job()
    gh = RecordingGitHub()

    with SessionLocal() as db:
        job = db.get(JobFailure, job_id)
        pr_url = open_draft_pr(db, job, gh.client())

    assert pr_url == "https://github.com/octo/demo/pull/9"
    kinds = [kind for kind, _ in gh.writes]
    assert kinds == ["branch", "commit", "pr"]
    branch = gh.writes[0][1]
    assert branch["ref"] == "refs/heads/autodebug/run-42-job-7"
    assert branch["sha"] == "abc123"
    commit = gh.writes[1][1]
    assert base64.b64decode(commit["content"]).decode() == FIXED
    assert commit["sha"] == "blob1"
    pr = gh.writes[2][1]
    assert pr["draft"] is True
    assert pr["base"] == "main"
    assert "approved by mahima" in pr["body"]

    with SessionLocal() as db:
        saved = db.get(JobFailure, job_id)
        assert saved.status == "PR_OPENED"
        assert saved.pr_url == pr_url


def test_refuses_jobs_that_are_not_approved():
    job_id = seed_job(status="PENDING_REVIEW")
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError, match="only APPROVED"):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())
    assert gh.writes == []


def test_refuses_changes_to_files_other_than_the_failed_workflow():
    job_id = seed_job(changes=[{"path": "app/secret.py", "content": "x = 1\n"}])
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError, match="not allowed"):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())
    assert gh.writes == []


def test_refuses_a_no_op_edit_before_writing_anything():
    job_id = seed_job(changes=[{"path": WORKFLOW, "content": ORIGINAL}])
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError, match="changes nothing"):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())
    assert gh.writes == []


def test_refuses_too_many_files():
    changes = [{"path": WORKFLOW, "content": FIXED}] * 3
    job_id = seed_job(changes=changes)
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError, match="Too many"):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())


def test_refuses_when_no_changes_were_proposed():
    job_id = seed_job(changes=[])
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError, match="no file changes"):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())


def test_refuses_a_second_pr_for_the_same_job():
    job_id = seed_job(status="PR_OPENED", pr_url="https://github.com/octo/demo/pull/1")
    gh = RecordingGitHub()

    with SessionLocal() as db:
        with pytest.raises(PullRequestError):
            open_draft_pr(db, db.get(JobFailure, job_id), gh.client())
    assert gh.writes == []


def test_endpoint_returns_409_for_a_pending_job(client, monkeypatch):
    monkeypatch.setattr(settings, "review_api_key", KEY)
    job_id = seed_job(status="PENDING_REVIEW")

    response = client.post(f"/api/reviews/{job_id}/open-pr", headers=HEADERS)

    assert response.status_code == 409


def test_endpoint_opens_pr_for_an_approved_job(client, monkeypatch):
    monkeypatch.setattr(settings, "review_api_key", KEY)
    gh = RecordingGitHub()
    monkeypatch.setattr(reviews, "get_github_client", gh.client)
    job_id = seed_job()

    response = client.post(f"/api/reviews/{job_id}/open-pr", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PR_OPENED"
    assert body["pr_url"] == "https://github.com/octo/demo/pull/9"
    assert body["has_changes"] is True


PR_URL = "https://github.com/octo/demo/pull/9"


def seed_opened_pr(pr_url=PR_URL):
    return seed_job(status="PR_OPENED", pr_url=pr_url)


def test_merged_auto_fix_pr_marks_job_merged():
    from app.pull_request import record_pull_request_event

    job_id = seed_opened_pr()
    with SessionLocal() as db:
        recorded = record_pull_request_event(db, "closed", True, PR_URL, "autodebug/run-42-job-7")

    assert recorded is True
    with SessionLocal() as db:
        assert db.get(JobFailure, job_id).status == "MERGED"


def test_closed_without_merge_marks_job_pr_closed():
    from app.pull_request import record_pull_request_event

    job_id = seed_opened_pr()
    with SessionLocal() as db:
        record_pull_request_event(db, "closed", False, PR_URL, "autodebug/run-42-job-7")

    with SessionLocal() as db:
        assert db.get(JobFailure, job_id).status == "PR_CLOSED"


def test_unrelated_pull_requests_are_ignored():
    from app.pull_request import record_pull_request_event

    job_id = seed_opened_pr()
    with SessionLocal() as db:
        assert record_pull_request_event(db, "closed", True, PR_URL, "feature/login") is False
        assert record_pull_request_event(db, "opened", False, PR_URL, "autodebug/run-42-job-7") is False
        assert record_pull_request_event(db, "closed", True, None, "autodebug/run-42-job-7") is False

    with SessionLocal() as db:
        assert db.get(JobFailure, job_id).status == "PR_OPENED"


def test_webhook_records_merge_for_auto_fix_pr(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "github_webhook_secret", "")
    job_id = seed_opened_pr()
    body = {
        "action": "closed",
        "pull_request": {
            "html_url": PR_URL,
            "merged": True,
            "head": {"ref": "autodebug/run-42-job-7"},
        },
    }

    response = client.post(
        "/api/github/webhook",
        json=body,
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Pull request outcome recorded"
    with SessionLocal() as db:
        assert db.get(JobFailure, job_id).status == "MERGED"
