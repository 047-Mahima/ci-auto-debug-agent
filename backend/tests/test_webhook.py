import hashlib
import hmac
import json

from app.db import SessionLocal
from app.models import WorkflowFailure

WEBHOOK_URL = "/api/github/webhook"


def workflow_run_payload(run_id=1, conclusion="failure", branch="main"):
    return {
        "action": "completed",
        "repository": {
            "id": 67890,
            "full_name": "octo/demo",
            "default_branch": "main",
        },
        "workflow_run": {
            "id": run_id,
            "name": "CI",
            "status": "completed",
            "conclusion": conclusion,
            "head_branch": branch,
        },
    }


def post_event(client, event, body, secret=None):
    # Sign the exact bytes that are sent, because the HMAC covers the raw body.
    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-GitHub-Event": event}
    if secret is not None:
        digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    return client.post(WEBHOOK_URL, content=raw, headers=headers)


def stored_failure_count():
    with SessionLocal() as db:
        return db.query(WorkflowFailure).count()


def test_ping_is_acknowledged(client):
    response = post_event(client, "ping", {"zen": "hello"})

    assert response.status_code == 200
    assert response.json()["message"] == "GitHub webhook is reachable"


def test_missing_signature_is_rejected_when_secret_is_set(client, secret):
    response = post_event(client, "workflow_run", workflow_run_payload())

    assert response.status_code == 401
    assert stored_failure_count() == 0


def test_wrong_signature_is_rejected(client, secret):
    response = post_event(
        client, "workflow_run", workflow_run_payload(), secret="not-the-secret"
    )

    assert response.status_code == 401
    assert stored_failure_count() == 0


def test_valid_signature_stores_failure(client, secret):
    response = post_event(
        client, "workflow_run", workflow_run_payload(run_id=42), secret=secret
    )

    assert response.status_code == 200
    assert "Failure stored successfully" in response.json()["message"]
    assert stored_failure_count() == 1

    listed = client.get("/api/github/failures").json()
    assert listed[0]["repo_name"] == "octo/demo"
    assert listed[0]["workflow_name"] == "CI"


def test_successful_run_is_not_stored(client):
    response = post_event(
        client, "workflow_run", workflow_run_payload(conclusion="success")
    )

    assert response.status_code == 200
    assert "not stored as a failure" in response.json()["message"]
    assert stored_failure_count() == 0


def test_other_events_are_ignored(client):
    response = post_event(client, "push", {"ref": "refs/heads/main"})

    assert response.status_code == 200
    assert response.json()["message"] == "Ignored event: push"
    assert stored_failure_count() == 0


def test_redelivered_run_updates_instead_of_duplicating(client):
    post_event(client, "workflow_run", workflow_run_payload(run_id=7, branch="main"))
    post_event(client, "workflow_run", workflow_run_payload(run_id=7, branch="dev"))

    assert stored_failure_count() == 1
    with SessionLocal() as db:
        failure = db.query(WorkflowFailure).one()
        assert failure.branch == "dev"


def test_missing_workflow_run_returns_400(client):
    body = workflow_run_payload()
    del body["workflow_run"]

    response = post_event(client, "workflow_run", body)

    assert response.status_code == 400
    assert response.json()["detail"] == "workflow_run payload is missing"


def test_empty_body_is_rejected_before_handler(client):
    # FastAPI validates the required JSON body first, so an empty body gets a 422
    # and the handler's own "Request body is empty" check is never reached.
    response = client.post(
        WEBHOOK_URL,
        content=b"",
        headers={"X-GitHub-Event": "workflow_run"},
    )

    assert response.status_code == 422


def test_data_survives_app_restart(secret):
    # Each TestClient context runs the startup hook, which used to drop every table.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as first_run:
        post_event(first_run, "workflow_run", workflow_run_payload(run_id=5), secret=secret)

    with TestClient(app):
        pass

    assert stored_failure_count() == 1


def test_failed_run_on_auto_fix_branch_is_not_stored(client):
    # Opening a draft PR creates a branch, which GitHub runs as a push. Those runs must not
    # be analyzed again, or each fix would open another PR.
    response = post_event(
        client,
        "workflow_run",
        workflow_run_payload(run_id=77, branch="autodebug/run-1-job-2"),
    )

    assert response.status_code == 200
    assert "auto-fix branch" in response.json()["message"]
    assert stored_failure_count() == 0
