import json
from pathlib import Path

from app.schemas import GithubWebhookPayload
from app.db import SessionLocal
from app.github import get_or_create_repository, utc_now
from app.models import WorkflowFailure

payload = GithubWebhookPayload.model_validate(json.loads(Path('app/payload.json').read_text()))

with SessionLocal() as db:
    repo = get_or_create_repository(db, payload.repository.model_dump())
    print('repo', repo.id, repo.full_name)
    failure = WorkflowFailure(
        github_run_id=payload.workflow_run.id,
        repository_id=repo.id,
        workflow_name=payload.workflow_run.name,
        branch=payload.workflow_run.head_branch,
        conclusion=payload.workflow_run.conclusion,
        status=payload.workflow_run.status,
        error_summary='Failure captured; log analysis pending.',
        created_at=utc_now(),
    )
    db.add(failure)
    db.commit()
    print('rows', db.query(WorkflowFailure).count())
    print(db.query(WorkflowFailure).all())
