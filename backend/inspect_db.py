from app.db import SessionLocal
from app.models import WorkflowFailure, Repository

with SessionLocal() as db:
    rows = db.query(WorkflowFailure).all()
    print('failures', len(rows))
    for row in rows:
        print(row.id, row.github_run_id, row.workflow_name, row.conclusion, row.status, row.repository_id)

    repos = db.query(Repository).all()
    print('repos', len(repos))
    for repo in repos:
        print(repo.id, repo.github_repo_id, repo.full_name, repo.default_branch)
