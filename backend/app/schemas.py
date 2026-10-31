# validates the JSON going in and out of the API endpoints
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel

class RepositoryPayload(BaseModel):
    id: Optional[int] = None
    full_name: Optional[str] = None
    default_branch: Optional[str] = None

class WorkflowRunPayload(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    status: Optional[str] = None
    conclusion: Optional[str] = None
    head_branch: Optional[str] = None
    html_url: Optional[str] = None

class GithubWebhookPayload(BaseModel):
    action: Optional[str] = None
    zen: Optional[str] = None
    hook_id: Optional[int] = None
    repository: Optional[RepositoryPayload] = None
    workflow_run: Optional[WorkflowRunPayload] = None
    sender: Optional[Dict[str, Any]] = None
     
class FailureListItem(BaseModel):
    id: int
    repo_name: str
    workflow_name: str | None
    branch: str | None
    conclusion: str | None
    status: str
    error_summary: str | None
    created_at: datetime

    class Config:
        from_attributes = True

class WebhookAck(BaseModel):
    accepted: bool
    message: str
