#!/usr/bin/env python3
"""task_store_http.py — shared HTTP engine for the urllib-backed task-store
providers (GitHub, GitLab, …).

Holds the parts every REST provider shares verbatim: the transport seam, the
ONE-retry budget (GET-class only: one retry on 5xx / network / timeout / 429;
a write never retries — a retried write can double-post), the Retry-After cap,
the path-segment percent-encoder, and the no-token-leak discipline (errors
name the env var, never the token value).

A provider subclasses HttpTaskStore and supplies only what differs:
  PROVIDER_NAME     — used in every error message.
  ACCEPT            — value of the Accept header.
  DEFAULT_BASE_URL  — base URL when cfg omits one.
  _TOKEN_HINT       — trailing clause of the missing-token AuthError (what
                      scope the token needs).
  _extra_headers()  — provider-specific headers added per attempt (auth + any
                      api-version pin). Called INSIDE the retry loop so the
                      token is read fresh each attempt; MUST include auth.
  _normalize(obj)   — provider JSON shape → canonical task dict.
  the four surface methods (get_task / list_tasks / add_comment / whoami).

Keeping the retry/no-leak loop in ONE place means a fix to the budget or the
leak discipline lands for every provider at once — two copies could otherwise
drift, and drift on this path is a security regression.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from task_store import (
    AuthError, NotFound, RateLimited, TaskStoreAdapter, Unavailable,
)

_RETRY_AFTER_CAP_S = 30


def default_send(req, timeout):
    """Transport seam: urlopen → (status, headers dict, body bytes). HTTPError
    is a *response* here, not an exception — status mapping is the provider's
    job, not the transport's."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class HttpTaskStore(TaskStoreAdapter):
    """Template-method base: subclasses set the class attrs below and implement
    `_extra_headers`, `_normalize`, and the four surface methods."""

    PROVIDER_NAME = "HTTP"
    ACCEPT = "application/json"
    DEFAULT_BASE_URL = ""
    _TOKEN_HINT = "export an access token in that env var"

    def __init__(self, cfg, send=None):
        self.base_url = str(cfg.get("base_url") or self.DEFAULT_BASE_URL).rstrip("/")
        self.token_env = cfg["token_env"]
        timeouts = cfg.get("timeouts") or {}
        # urllib exposes a single deadline; use the larger of the two so a slow
        # read is not cut off by the connect budget.
        self.timeout_s = max(timeouts.get("connect_s", 5),
                             timeouts.get("read_s", 15))
        self._send = send or default_send
        self._sleep = time.sleep  # test seam

    # ---------------------------------------------------------- plumbing ---

    def _token(self) -> str:
        tok = os.environ.get(self.token_env, "")
        if not tok:
            raise AuthError(
                "no token in $%s — %s" % (self.token_env, self._TOKEN_HINT))
        return tok

    def _extra_headers(self) -> dict:
        """Provider-specific headers (auth + any api-version pin), built fresh
        each attempt so the token is read at request time. MUST include auth."""
        raise NotImplementedError

    def _request(self, method, path, *, query=None, payload=None,
                 retry_get_class=True):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        attempts = 0
        while True:
            attempts += 1
            data = None
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Accept", self.ACCEPT)
            for key, val in self._extra_headers().items():
                req.add_header(key, val)
            if data is not None:
                req.add_header("Content-Type", "application/json")

            try:
                status, headers, body = self._send(req, self.timeout_s)
            except Exception as e:
                # Network/timeout. GET-class may retry once.
                if retry_get_class and attempts == 1:
                    continue
                raise Unavailable(
                    "%s unreachable after %d attempt(s): %s: %s"
                    % (self.PROVIDER_NAME, attempts, type(e).__name__, e),
                    attempts=attempts)

            if status in (401, 403):
                raise AuthError(
                    "%s returned %d for %s %s — check the token in $%s "
                    "(scope/expiry)"
                    % (self.PROVIDER_NAME, status, method, path, self.token_env))
            if status == 404:
                raise NotFound(
                    "%s returned 404 for %s %s"
                    % (self.PROVIDER_NAME, method, path))
            if status == 429:
                retry_after = self._retry_after_s(headers)
                if attempts == 1 and retry_get_class:
                    self._sleep(min(retry_after or 1, _RETRY_AFTER_CAP_S))
                    continue
                raise RateLimited(
                    "%s rate limit hit on %s %s — retry after ~%ss"
                    % (self.PROVIDER_NAME, method, path, retry_after),
                    retry_after_s=retry_after)
            if status >= 500:
                if retry_get_class and attempts == 1:
                    continue
                raise Unavailable(
                    "%s returned %d for %s %s after %d attempt(s)"
                    % (self.PROVIDER_NAME, status, method, path, attempts),
                    attempts=attempts)

            try:
                return json.loads(body.decode("utf-8")) if body else {}
            except ValueError:
                raise Unavailable(
                    "%s returned unparsable JSON for %s %s (HTTP %d)"
                    % (self.PROVIDER_NAME, method, path, status),
                    attempts=attempts)

    @staticmethod
    def _retry_after_s(headers):
        for k, v in (headers or {}).items():
            if str(k).lower() == "retry-after":
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return None
        return None

    @staticmethod
    def _seg(ref) -> str:
        """A ref becomes exactly ONE url path segment — every reserved char
        (slash included) is percent-encoded, so a crafted ref cannot add
        segments or escape the issues/ subtree."""
        return urllib.parse.quote(str(ref), safe="")
