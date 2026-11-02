#Minimal GitHub REST client for reading Actions runs, jobs and job logs.
import base64
from dataclasses import dataclass

import httpx

PAGE_SIZE = 100


class GitHubError(Exception):
    pass


@dataclass
class FailedJob:
    github_job_id: int
    job_name: str
    failed_step: str | None


class GitHubClient:
    def __init__(self, token: str, api_url: str, transport: httpx.BaseTransport | None = None):
        if not token:
            raise GitHubError("GITHUB_TOKEN is not set")
        self._http = httpx.Client(
            base_url=api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            transport=transport,
        )

    def _get(self, path: str, **kwargs) -> httpx.Response:
        response = self._http.get(path, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(f"GitHub returned {response.status_code} for {path}")
        return response

    def list_failed_jobs(self, repo_full_name: str, run_id: int) -> list[FailedJob]:
        failed: list[FailedJob] = []
        page = 1
        while True:
            response = self._get(
                f"/repos/{repo_full_name}/actions/runs/{run_id}/jobs",
                params={"per_page": PAGE_SIZE, "page": page},
            )
            jobs = response.json().get("jobs", [])
            for job in jobs:
                if job.get("conclusion") == "failure":
                    failed.append(
                        FailedJob(
                            github_job_id=job["id"],
                            job_name=job["name"],
                            failed_step=_first_failed_step(job),
                        )
                    )
            if len(jobs) < PAGE_SIZE:
                return failed
            page += 1

    def get_job_log(self, repo_full_name: str, job_id: int) -> str:
        # GitHub answers with a redirect to a short-lived download URL.
        # httpx drops the Authorization header when the redirect leaves api.github.com.
        response = self._http.get(
            f"/repos/{repo_full_name}/actions/jobs/{job_id}/logs",
            follow_redirects=True,
        )
        if response.status_code >= 400:
            # Logs expire after 90 days, so a 404 here is normal for old runs.
            raise GitHubError(f"Could not download log for job {job_id}: {response.status_code}")
        return response.text


    def get_run_details(self, repo_full_name: str, run_id: int) -> dict:
        response = self._get(f"/repos/{repo_full_name}/actions/runs/{run_id}")
        data = response.json()
        return {"path": data.get("path"), "head_sha": data.get("head_sha")}

    def get_file(self, repo_full_name: str, path: str, ref: str) -> tuple[str, str]:
        # Returns (text content, blob sha). The blob sha is needed to update the file later.
        response = self._get(f"/repos/{repo_full_name}/contents/{path}", params={"ref": ref})
        data = response.json()
        if data.get("type") != "file" or data.get("encoding") != "base64":
            raise GitHubError(f"{path} is not a readable file")
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

    def create_branch(self, repo_full_name: str, branch: str, sha: str) -> None:
        response = self._http.post(
            f"/repos/{repo_full_name}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if response.status_code >= 400:
            raise GitHubError(f"Could not create branch {branch}: {response.status_code}")

    def put_file(
        self,
        repo_full_name: str,
        path: str,
        branch: str,
        content: str,
        blob_sha: str,
        message: str,
    ) -> None:
        response = self._http.put(
            f"/repos/{repo_full_name}/contents/{path}",
            json={
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "sha": blob_sha,
                "branch": branch,
            },
        )
        if response.status_code >= 400:
            raise GitHubError(f"Could not commit {path} to {branch}: {response.status_code}")

    def create_draft_pr(self, repo_full_name: str, head: str, base: str, title: str, body: str) -> str:
        response = self._http.post(
            f"/repos/{repo_full_name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": True},
        )
        if response.status_code >= 400:
            raise GitHubError(f"Could not open draft PR: {response.status_code}")
        return response.json()["html_url"]


def _first_failed_step(job: dict) -> str | None:
    for step in job.get("steps", []):
        if step.get("conclusion") == "failure":
            return step.get("name")
    return None
