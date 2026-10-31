import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import WorkflowFailure

client = TestClient(app)
data = json.loads(Path('app/payload.json').read_text())
resp = client.post(
    '/api/github/webhook',
    json=data,
    headers={'X-GitHub-Event': 'workflow_run'},
)
print('status', resp.status_code)
print(resp.text)

with SessionLocal() as db:
    rows = db.query(WorkflowFailure).all()
    print('rows', len(rows))
    for row in rows:
        print(row.id, row.github_run_id, row.workflow_name, row.conclusion, row.status)
