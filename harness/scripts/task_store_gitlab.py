#!/usr/bin/env python3
"""task_store_gitlab.py — GitLab provider for the task-store surface.

Same four-method surface as the GitHub provider, on the shared HTTP engine in
`task_store_http` (transport seam, ONE-retry budget, Retry-After cap,
path-segment encoder, no-token-leak discipline). This module supplies only the
GitLab REST v4 differences:

  * auth header is `PRIVATE-TOKEN: <token>` (a personal/project access token),
    not `Authorization: Bearer`.
  * the project is a path (`group/name`) sent as ONE percent-encoded segment
    (`group%2Fname`); issues are addressed by their per-project `iid`.
  * the issue JSON differs: `description` (not body), `state` is
    `opened`/`closed` (normalized to open/closed), `labels` is a list of plain
    strings, `author.username`, `web_url`. No API-version header — v4 is the
    base path.

Writes (add_comment) are never retried — a retried note can double-post; the
mirror is advisory. Errors name the ENV VAR, never the token value.
"""

import urllib.parse

from task_store_http import HttpTaskStore


class GitLabTaskStore(HttpTaskStore):
    PROVIDER_NAME = "GitLab"
    ACCEPT = "application/json"
    DEFAULT_BASE_URL = "https://gitlab.com/api/v4"
    _TOKEN_HINT = ("export a GitLab access token in that env var "
                   "(api or read_api + write on issues is enough)")

    def __init__(self, cfg, send=None):
        super().__init__(cfg, send=send)
        # project path (group/name) → one percent-encoded path segment.
        self.project = urllib.parse.quote(str(cfg["project"]).strip("/"), safe="")

    def _extra_headers(self):
        # Token read fresh each attempt; never serialized into any error.
        return {"PRIVATE-TOKEN": self._token()}

    @staticmethod
    def _normalize(issue) -> dict:
        # GitLab `state` is opened/closed → canonical open/closed; `labels` are
        # plain strings; body is `description`; author is `author.username`.
        state = issue.get("state") or ""
        return {
            "ref": str(issue.get("iid")),
            "title": issue.get("title") or "",
            "state": "open" if state == "opened" else state,
            "body": issue.get("description") or "",
            "labels": [str(lab) for lab in issue.get("labels") or []],
            "author": (issue.get("author") or {}).get("username", ""),
            "url": issue.get("web_url"),
        }

    # ----------------------------------------------------------- surface ---

    def get_task(self, ref):
        issue = self._request(
            "GET", "/projects/%s/issues/%s" % (self.project, self._seg(ref)))
        return self._normalize(issue)

    def list_tasks(self, filter=None):
        query = {}
        if filter:
            # canonical open/closed → GitLab opened/closed (its scope term)
            st = filter.get("state")
            if st is not None:
                query["state"] = "opened" if st == "open" else st
            for key in ("labels", "per_page", "page"):
                if filter.get(key) is not None:
                    query[key] = filter[key]
        issues = self._request(
            "GET", "/projects/%s/issues" % self.project, query=query or None)
        return [self._normalize(i) for i in issues]

    def add_comment(self, ref, body):
        return self._request(
            "POST",
            "/projects/%s/issues/%s/notes" % (self.project, self._seg(ref)),
            payload={"body": body},
            retry_get_class=False)  # never retry a write

    def whoami(self):
        # Only the username leaves this method — the full /user payload carries
        # name/email that the harness has no business persisting.
        return (self._request("GET", "/user") or {}).get("username", "")
