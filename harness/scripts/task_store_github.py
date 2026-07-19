#!/usr/bin/env python3
"""task_store_github.py — GitHub provider for the task-store surface.

stdlib urllib only (no new deps). API pinned to version 2022-11-28 via the
X-GitHub-Api-Version header (supported until 2028-03; the pin keeps response
shapes stable under the parsing tests' fixtures).

The shared HTTP engine (transport seam, ONE-retry budget, Retry-After cap,
path-segment encoder, no-token-leak discipline) lives in `task_store_http`.
This module supplies only the GitHub-specific bits: auth via
`Authorization: Bearer`, the api-version header, the issue JSON shape, and the
four surface endpoints. Token handling stays no-leak — errors name the env var,
never the token value.
"""

from task_store_http import HttpTaskStore

_API_VERSION_DEFAULT = "2022-11-28"


class GitHubTaskStore(HttpTaskStore):
    PROVIDER_NAME = "GitHub"
    ACCEPT = "application/vnd.github+json"
    DEFAULT_BASE_URL = "https://api.github.com"
    _TOKEN_HINT = ("export a GitHub personal access token in that env var "
                   "(fine-grained, issues:write is enough)")

    def __init__(self, cfg, send=None):
        super().__init__(cfg, send=send)
        self.repo = str(cfg["repo"]).strip("/")
        self.api_version = cfg.get("api_version") or _API_VERSION_DEFAULT

    def _extra_headers(self):
        # Token read fresh each attempt; never serialized into any error.
        return {"X-GitHub-Api-Version": self.api_version,
                "Authorization": "Bearer " + self._token()}

    @staticmethod
    def _normalize(issue) -> dict:
        return {
            "ref": str(issue.get("number")),
            "title": issue.get("title") or "",
            "state": issue.get("state") or "",
            "body": issue.get("body") or "",
            "labels": [lab.get("name", "") for lab in issue.get("labels") or []],
            "author": (issue.get("user") or {}).get("login", ""),
            "url": issue.get("html_url"),
        }

    # ----------------------------------------------------------- surface ---

    def get_task(self, ref):
        issue = self._request(
            "GET", "/repos/%s/issues/%s" % (self.repo, self._seg(ref)))
        return self._normalize(issue)

    def list_tasks(self, filter=None):
        query = {}
        for key in ("state", "labels", "assignee", "per_page", "page"):
            if filter and filter.get(key) is not None:
                query[key] = filter[key]
        issues = self._request(
            "GET", "/repos/%s/issues" % self.repo, query=query or None)
        return [self._normalize(i) for i in issues]

    def add_comment(self, ref, body):
        return self._request(
            "POST",
            "/repos/%s/issues/%s/comments" % (self.repo, self._seg(ref)),
            payload={"body": body},
            retry_get_class=False)  # never retry a write

    def whoami(self):
        # Only the login leaves this method — the full /user payload carries
        # name/email/company that the harness has no business persisting.
        return (self._request("GET", "/user") or {}).get("login", "")
