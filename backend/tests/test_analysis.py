import pytest

from app.analysis import (
    MAX_LOG_CHARS,
    GeminiProvider,
    RootCauseAnalysis,
    analyze_job_failure,
    build_prompt,
    trim_log,
)
from app.db import SessionLocal
from app.models import JobFailure, Repository, WorkflowFailure


class FakeProvider:
    def __init__(self, result):
        self.result = result
        self.seen_logs = []

    def analyze(self, log_text, files=None):
        self.seen_logs.append(log_text)
        return self.result


def make_job_failure(db):
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
        status="LOG_FETCHED",
    )
    db.add_all([repo, failure, job])
    db.commit()
    return job


def test_trim_log_keeps_short_logs_unchanged():
    assert trim_log("short log") == "short log"


def test_trim_log_keeps_the_tail_where_errors_usually_are():
    log = "x" * (MAX_LOG_CHARS * 2) + "ERROR: boom"

    trimmed = trim_log(log)

    assert trimmed.endswith("ERROR: boom")
    assert trimmed.startswith("...[log truncated]...")
    assert len(trimmed) <= MAX_LOG_CHARS + len("...[log truncated]...\n")


def test_prompt_wraps_log_in_tags_and_marks_it_as_data():
    prompt = build_prompt("TypeError: bad operand")

    assert "<log>\nTypeError: bad operand\n</log>" in prompt
    assert "untrusted data" in prompt


def test_analysis_result_is_stored_and_sent_to_review():
    result = RootCauseAnalysis(
        failure_type="test_failure",
        root_cause="int + str raises TypeError.",
        suggested_fix="Convert the value with int() before adding.",
        confidence="high",
    )
    provider = FakeProvider(result)

    with SessionLocal() as db:
        job = make_job_failure(db)
        analyze_job_failure(db, job, "TypeError: unsupported operand", provider)

    with SessionLocal() as db:
        saved = db.query(JobFailure).one()
        assert saved.status == "PENDING_REVIEW"
        assert saved.failure_type == "test_failure"
        assert saved.confidence == "high"
        assert "Root cause: int + str raises TypeError." in saved.error_summary
        assert "Suggested fix: Convert the value" in saved.error_summary

    assert provider.seen_logs == ["TypeError: unsupported operand"]


def test_gemini_provider_requires_a_key():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key="", model="gemini-3.8-flash")


def test_gemini_provider_strips_models_prefix():
    provider = GeminiProvider(api_key="test-key", model="models/gemini-3.8-flash")

    assert provider._model == "gemini-3.8-flash"
