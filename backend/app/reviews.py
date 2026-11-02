#Human review of AI root-cause analyses. Nothing leaves PENDING_REVIEW without a
#reviewer decision, and approving does not create a pull request.
import hmac
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analysis import analyze_job_failure, get_default_provider
from app.config import settings
from app.db import get_db
from app.github_client import GitHubClient, GitHubError
from app.models import JobFailure, WorkflowFailure, utc_now
from app.pull_request import PullRequestError, open_draft_pr

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def require_review_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    # Fail closed: with no key configured, the review API stays off.
    if not settings.review_api_key:
        raise HTTPException(status_code=503, detail="Review API is disabled (REVIEW_API_KEY not set)")
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.review_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


class ReviewItem(BaseModel):
    id: int
    repo_name: str
    run_id: int
    workflow_name: str
    job_name: str
    failed_step: str | None
    status: str
    failure_type: str | None
    confidence: str | None
    error_summary: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    pr_url: str | None
    has_changes: bool
    created_at: datetime


class ReviewDetail(ReviewItem):
    raw_log: str | None
    proposed_changes: list[dict]


class ReviewDecision(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=2000)


def _to_item(job: JobFailure) -> ReviewItem:
    failure = job.workflow_failure
    return ReviewItem(
        id=job.id,
        repo_name=failure.repository.full_name,
        run_id=failure.github_run_id,
        workflow_name=failure.workflow_name,
        job_name=job.job_name,
        failed_step=job.failed_step,
        status=job.status,
        failure_type=job.failure_type,
        confidence=job.confidence,
        error_summary=job.error_summary,
        reviewed_by=job.reviewed_by,
        reviewed_at=job.reviewed_at,
        review_note=job.review_note,
        pr_url=job.pr_url,
        has_changes=bool(job.proposed_changes),
        created_at=job.created_at,
    )


def _get_job(db: Session, job_id: int) -> JobFailure:
    job = db.get(JobFailure, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job failure not found")
    return job


def _require_status(job: JobFailure, allowed: set[str]) -> None:
    if job.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status}; expected one of {sorted(allowed)}",
        )


@router.get("", response_model=list[ReviewItem], dependencies=[Depends(require_review_key)])
def list_reviews(
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED", "PR_OPENED", "MERGED", "PR_CLOSED", "ERROR", "LOG_FETCHED"] = "PENDING_REVIEW",
    db: Session = Depends(get_db),
):
    jobs = (
        db.query(JobFailure)
        .filter(JobFailure.status == status)
        .order_by(JobFailure.created_at.desc())
        .all()
    )
    return [_to_item(job) for job in jobs]


@router.get("/{job_id}", response_model=ReviewDetail, dependencies=[Depends(require_review_key)])
def get_review(job_id: int, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    return ReviewDetail(
        **_to_item(job).model_dump(),
        raw_log=job.raw_log,
        proposed_changes=json.loads(job.proposed_changes or "[]"),
    )


@router.post("/{job_id}/approve", response_model=ReviewItem, dependencies=[Depends(require_review_key)])
def approve(job_id: int, decision: ReviewDecision, db: Session = Depends(get_db)):
    return _decide(db, job_id, decision, "APPROVED")


@router.post("/{job_id}/reject", response_model=ReviewItem, dependencies=[Depends(require_review_key)])
def reject(job_id: int, decision: ReviewDecision, db: Session = Depends(get_db)):
    return _decide(db, job_id, decision, "REJECTED")


def _decide(db: Session, job_id: int, decision: ReviewDecision, new_status: str) -> ReviewItem:
    job = _get_job(db, job_id)
    _require_status(job, {"PENDING_REVIEW"})
    job.status = new_status
    job.reviewed_by = decision.reviewer.strip()
    job.reviewed_at = utc_now()
    job.review_note = decision.note
    db.commit()
    return _to_item(job)


@router.post("/{job_id}/analyze", response_model=ReviewItem, dependencies=[Depends(require_review_key)])
def reanalyze(job_id: int, db: Session = Depends(get_db)):
    # Re-run analysis for a job whose earlier attempt failed (for example, Gemini was down).
    job = _get_job(db, job_id)
    _require_status(job, {"ERROR", "LOG_FETCHED"})
    if not job.raw_log:
        raise HTTPException(status_code=409, detail="No stored log to analyze")
    try:
        analyze_job_failure(db, job, job.raw_log, get_default_provider())
    except Exception as exc:
        db.rollback()
        job = _get_job(db, job_id)
        job.status = "ERROR"
        job.error_summary = f"{type(exc).__name__}: {exc}"
        db.commit()
        raise HTTPException(status_code=502, detail=job.error_summary)
    return _to_item(job)


def get_github_client() -> GitHubClient:
    return GitHubClient(settings.github_token, settings.github_api_url)


@router.post("/{job_id}/open-pr", response_model=ReviewItem, dependencies=[Depends(require_review_key)])
def open_pr(job_id: int, db: Session = Depends(get_db)):
    # Only after a human approved the suggestion. Always opens a draft PR.
    job = _get_job(db, job_id)
    try:
        open_draft_pr(db, job, get_github_client())
    except PullRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except GitHubError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))
    return _to_item(job)
