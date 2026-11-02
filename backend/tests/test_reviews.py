import pytest
from google.genai import errors

from app import analysis, reviews
from app.analysis import GeminiProvider, RootCauseAnalysis
from app.config import settings
from app.db import SessionLocal
from app.models import JobFailure, Repository, WorkflowFailure

KEY = "review-key"
HEADERS = {"X-API-Key": KEY}


def seed_job(status="PENDING_REVIEW", raw_log="TypeError: boom"):
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
        job = JobFailure(
            workflow_failure=failure,
            github_job_id=7,
            job_name="test",
            failed_step="Run tests",
            raw_log=raw_log,
            status=status,
            failure_type="test_failure",
            error_summary="Root cause: x\nSuggested fix: y",
        )
        db.add_all([repo, failure, job])
        db.commit()
        return job.id


@pytest.fixture
def review_key(monkeypatch):
    monkeypatch.setattr(settings, "review_api_key", KEY)


class FakeProvider:
    def analyze(self, log_text, files=None):
        return RootCauseAnalysis(
            failure_type="test_failure",
            root_cause="int + str raises TypeError.",
            suggested_fix="Convert before adding.",
            confidence="high",
        )


def test_review_api_is_disabled_without_configured_key(client, monkeypatch):
    monkeypatch.setattr(settings, "review_api_key", "")

    response = client.get("/api/reviews", headers=HEADERS)

    assert response.status_code == 503


def test_missing_or_wrong_key_is_rejected(client, review_key):
    seed_job()

    assert client.get("/api/reviews").status_code == 401
    assert client.get("/api/reviews", headers={"X-API-Key": "wrong"}).status_code == 401


def test_pending_reviews_are_listed(client, review_key):
    job_id = seed_job()

    response = client.get("/api/reviews", headers=HEADERS)

    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == [job_id]
    assert items[0]["repo_name"] == "octo/demo"
    assert items[0]["failed_step"] == "Run tests"
    assert "raw_log" not in items[0]


def test_detail_includes_raw_log(client, review_key):
    job_id = seed_job()

    response = client.get(f"/api/reviews/{job_id}", headers=HEADERS)

    assert response.json()["raw_log"] == "TypeError: boom"


def test_approve_records_reviewer_and_blocks_second_decision(client, review_key):
    job_id = seed_job()

    response = client.post(
        f"/api/reviews/{job_id}/approve",
        headers=HEADERS,
        json={"reviewer": "mahima", "note": "matches the log"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["reviewed_by"] == "mahima"
    assert body["review_note"] == "matches the log"
    assert body["reviewed_at"] is not None

    again = client.post(f"/api/reviews/{job_id}/reject", headers=HEADERS, json={"reviewer": "x"})
    assert again.status_code == 409


def test_reject_sets_rejected(client, review_key):
    job_id = seed_job()

    response = client.post(f"/api/reviews/{job_id}/reject", headers=HEADERS, json={"reviewer": "mahima"})

    assert response.json()["status"] == "REJECTED"


def test_decision_requires_reviewer(client, review_key):
    job_id = seed_job()

    response = client.post(f"/api/reviews/{job_id}/approve", headers=HEADERS, json={"reviewer": ""})

    assert response.status_code == 422


def test_reanalyze_error_job_returns_it_to_review(client, review_key, monkeypatch):
    job_id = seed_job(status="ERROR")
    monkeypatch.setattr(reviews, "get_default_provider", lambda: FakeProvider())

    response = client.post(f"/api/reviews/{job_id}/analyze", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_REVIEW"


def test_reanalyze_rejects_jobs_already_in_review(client, review_key):
    job_id = seed_job(status="PENDING_REVIEW")

    response = client.post(f"/api/reviews/{job_id}/analyze", headers=HEADERS)

    assert response.status_code == 409


# Retry behaviour for the Gemini provider.

def provider_with(responses, monkeypatch):
    provider = GeminiProvider(api_key="test-key", model="gemini-3.8-flash")
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(provider._client.models, "generate_content", generate_content)
    monkeypatch.setattr(analysis.time, "sleep", lambda seconds: None)
    return provider, calls


class Parsed:
    parsed = RootCauseAnalysis(
        failure_type="unknown", root_cause="r", suggested_fix="f", confidence="low"
    )


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    unavailable = errors.ServerError(
        503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )
    provider, calls = provider_with([unavailable, unavailable, Parsed()], monkeypatch)

    result = provider.analyze("log")

    assert result.failure_type == "unknown"
    assert len(calls) == 3


def test_permanent_error_is_not_retried(monkeypatch):
    bad_request = errors.ClientError(
        400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}
    )
    provider, calls = provider_with([bad_request], monkeypatch)

    with pytest.raises(errors.ClientError):
        provider.analyze("log")
    assert len(calls) == 1
