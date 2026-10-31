from app.github import extract_workflow_context


def test_extract_workflow_context_reads_nested_payload():
    payload = {
        "repository": {
            "id": 1,
            "full_name": "octo/demo",
            "default_branch": "main",
        },
        "workflow_run": {
            "id": 99,
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
        },
    }

    context = extract_workflow_context(payload)

    assert context["conclusion"] == "failure"
    assert context["repo_full_name"] == "octo/demo"
    assert context["run_id"] == 99
