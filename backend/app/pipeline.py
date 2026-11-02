#Runs in the background after a failed workflow is stored: fetches each failed job's
#log, then asks the analysis provider for a root cause. Results wait for human review.
import logging

from app.analysis import AnalysisProvider, analyze_job_failure, get_default_provider
from app.config import settings
from app.db import SessionLocal
from app.github_client import GitHubClient, GitHubError
from app.models import JobFailure, WorkflowFailure

logger = logging.getLogger(__name__)


def run_failure_pipeline(
    workflow_failure_id: int,
    gh: GitHubClient,
    provider: AnalysisProvider,
) -> None:
    with SessionLocal() as db:
        failure = db.get(WorkflowFailure, workflow_failure_id)
        if failure is None:
            logger.warning("Pipeline skipped: workflow failure %s not found", workflow_failure_id)
            return

        repo_name = failure.repository.full_name
        try:
            failed_jobs = gh.list_failed_jobs(repo_name, failure.github_run_id)
        except GitHubError:
            logger.exception("Could not list jobs for run %s", failure.github_run_id)
            return

        # The workflow file that ran is the only file the model may edit in v1.
        try:
            details = gh.get_run_details(repo_name, failure.github_run_id)
        except GitHubError:
            logger.exception("Could not read run details for run %s", failure.github_run_id)
            details = {"path": None, "head_sha": None}

        for job in failed_jobs:
            # A re-delivered webhook must not create duplicate rows.
            exists = db.query(JobFailure).filter_by(github_job_id=job.github_job_id).first()
            if exists is not None:
                continue

            row = JobFailure(
                workflow_failure_id=failure.id,
                github_job_id=job.github_job_id,
                job_name=job.job_name,
                failed_step=job.failed_step,
                head_sha=details["head_sha"],
                workflow_path=details["path"],
                status="NEW",
            )
            db.add(row)
            db.commit()

            try:
                row.raw_log = gh.get_job_log(repo_name, job.github_job_id)
                row.status = "LOG_FETCHED"
                db.commit()
                analyze_job_failure(db, row, row.raw_log, provider, _workflow_files(gh, repo_name, row))
            except Exception as exc:
                # Record the error on this job and carry on with the others.
                logger.exception("Pipeline failed for job %s", job.github_job_id)
                db.rollback()
                row = db.get(JobFailure, row.id)
                row.status = "ERROR"
                row.error_summary = f"{type(exc).__name__}: {exc}"
                db.commit()


def run_failure_pipeline_from_settings(workflow_failure_id: int) -> None:
    # Entry point for the webhook's background task.
    gh = GitHubClient(settings.github_token, settings.github_api_url)
    run_failure_pipeline(workflow_failure_id, gh, get_default_provider())


def _workflow_files(gh: GitHubClient, repo_name: str, row: JobFailure) -> dict[str, str] | None:
    # Send the workflow file at the failing commit. If it can't be read, analyze the log alone.
    if not row.workflow_path or not row.head_sha:
        return None
    try:
        content, _ = gh.get_file(repo_name, row.workflow_path, row.head_sha)
    except GitHubError:
        logger.warning("Could not read %s at %s", row.workflow_path, row.head_sha)
        return None
    return {row.workflow_path: content}
