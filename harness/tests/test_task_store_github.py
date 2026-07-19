"""test_task_store_github.py — GitHub provider: request building, response
parsing, error mapping, retry budget, and the no-token-leak invariant.

No network: the provider takes an injectable `send` callable; tests feed it
canned (status, headers, body) tuples shaped like live GitHub responses
pinned at API version 2022-11-28. The token-leak tests assert the secret
never appears in ANY error class string or in the trace — not just AuthError.
"""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import task_store  # noqa: E402
import task_store_github  # noqa: E402
from task_store import (  # noqa: E402
    AuthError, ConfigError, NotFound, RateLimited, Unavailable,
)

TOKEN = "ghp_SECRETSECRETSECRET123"

# Response shapes pinned from the API recon at version 2022-11-28.
ISSUE_FIXTURE = {
    "id": 1, "number": 42, "title": "Fix the flange",
    "body": "It rattles.", "state": "open", "state_reason": None,
    "user": {"login": "octocat"},
    "labels": [{"id": 7, "name": "bug"}, {"id": 8, "name": "p1"}],
    "assignees": [], "comments": 2, "locked": False,
    "html_url": "https://github.com/own/repo/issues/42",
    "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-06-02T00:00:00Z",
}
LIST_FIXTURE = [ISSUE_FIXTURE,
                dict(ISSUE_FIXTURE, number=43, title="Second", state="closed",
                     labels=[])]
COMMENT_FIXTURE = {"id": 9001, "body": "claimed by x",
                   "user": {"login": "octocat"}}
USER_FIXTURE = {"login": "octocat", "id": 1, "name": "The Octocat",
                "email": "octo@cat", "company": "GitHub"}


def _cfg(**over):
    cfg = {"base_url": "https://api.github.example", "repo": "own/repo",
           "token_env": "TEST_GH_TOKEN", "api_version": "2022-11-28"}
    cfg.update(over)
    return cfg


class CannedSend:
    """Queue of (status, headers, body) responses; records every request."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        status, headers, body = self.queue.pop(0)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        return status, headers, body


@pytest.fixture()
def token_env(monkeypatch):
    monkeypatch.setenv("TEST_GH_TOKEN", TOKEN)


def _provider(send, **cfg_over):
    return task_store_github.GitHubTaskStore(_cfg(**cfg_over), send=send)


# ---------- request building ----------

class TestRequestBuilding:
    def test_get_task_url_headers_and_timeout(self, token_env):
        send = CannedSend((200, {}, ISSUE_FIXTURE))
        _provider(send).get_task("42")
        req = send.requests[0]
        assert req.full_url == "https://api.github.example/repos/own/repo/issues/42"
        assert req.get_header("X-github-api-version") == "2022-11-28"
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert req.get_header("Accept") == "application/vnd.github+json"

    def test_ref_with_path_chars_is_url_encoded_per_segment(self, token_env):
        # A hostile ref must stay ONE path segment: no traversal, no new
        # segments, no fragment cut-off.
        send = CannedSend((404, {}, {"message": "Not Found"}))
        with pytest.raises(NotFound):
            _provider(send).get_task("42/../../secrets#frag")
        url = send.requests[0].full_url
        assert "../" not in url and "/secrets" not in url and "#" not in url
        path_after_issues = url.split("/issues/", 1)[1]
        assert "/" not in path_after_issues  # ref occupies a single segment

    def test_list_tasks_builds_query_from_filter(self, token_env):
        send = CannedSend((200, {}, LIST_FIXTURE))
        _provider(send).list_tasks({"state": "all", "labels": "bug,p1"})
        url = send.requests[0].full_url
        assert "/repos/own/repo/issues?" in url
        assert "state=all" in url and "labels=bug%2Cp1" in url

    def test_add_comment_posts_body_json(self, token_env):
        send = CannedSend((201, {}, COMMENT_FIXTURE))
        _provider(send).add_comment("42", "claimed by x until y")
        req = send.requests[0]
        assert req.full_url.endswith("/repos/own/repo/issues/42/comments")
        assert req.get_method() == "POST"
        assert json.loads(req.data.decode("utf-8")) == {"body": "claimed by x until y"}

    def test_missing_token_env_var_is_auth_error_naming_the_var(self, monkeypatch):
        monkeypatch.delenv("TEST_GH_TOKEN", raising=False)
        with pytest.raises(AuthError) as exc:
            _provider(CannedSend()).whoami()
        msg = str(exc.value)
        assert "TEST_GH_TOKEN" in msg


# ---------- response parsing (fixtures pinned at 2022-11-28) ----------

class TestResponseParsing:
    def test_issue_parses_to_normalized_task(self, token_env):
        t = _provider(CannedSend((200, {}, ISSUE_FIXTURE))).get_task("42")
        assert t == {
            "ref": "42", "title": "Fix the flange", "state": "open",
            "body": "It rattles.", "labels": ["bug", "p1"],
            "author": "octocat",
            "url": "https://github.com/own/repo/issues/42",
        }

    def test_list_parses_each_issue(self, token_env):
        out = _provider(CannedSend((200, {}, LIST_FIXTURE))).list_tasks()
        assert [t["ref"] for t in out] == ["42", "43"]
        assert out[1]["state"] == "closed" and out[1]["labels"] == []

    def test_whoami_parses_login_only(self, token_env):
        send = CannedSend((200, {}, USER_FIXTURE))
        login = _provider(send).whoami()
        assert login == "octocat"
        assert send.requests[0].full_url == "https://api.github.example/user"

    def test_null_body_normalizes_to_empty_string(self, token_env):
        fx = dict(ISSUE_FIXTURE, body=None)
        t = _provider(CannedSend((200, {}, fx))).get_task("42")
        assert t["body"] == ""


# ---------- error mapping ----------

class TestErrorMapping:
    def test_401_maps_to_auth_error_naming_env_var_not_value(self, token_env):
        send = CannedSend((401, {}, {"message": "Bad credentials"}))
        with pytest.raises(AuthError) as exc:
            _provider(send).get_task("42")
        msg = str(exc.value)
        assert "TEST_GH_TOKEN" in msg and TOKEN not in msg

    def test_403_maps_to_auth_error(self, token_env):
        send = CannedSend((403, {}, {"message": "Forbidden"}))
        with pytest.raises(AuthError):
            _provider(send).get_task("42")

    def test_404_maps_to_not_found(self, token_env):
        send = CannedSend((404, {}, {"message": "Not Found"}))
        with pytest.raises(NotFound):
            _provider(send).get_task("42")

    def test_429_exhausted_maps_to_rate_limited_with_retry_after(self, token_env):
        send = CannedSend((429, {"Retry-After": "31"}, {}),
                          (429, {"Retry-After": "31"}, {}))
        provider = _provider(send)
        provider._sleep = lambda s: None
        with pytest.raises(RateLimited) as exc:
            provider.get_task("42")
        assert exc.value.retry_after_s == 31

    def test_5xx_exhausted_maps_to_unavailable_with_attempts(self, token_env):
        send = CannedSend((502, {}, b"bad gateway"), (502, {}, b"bad gateway"))
        with pytest.raises(Unavailable) as exc:
            _provider(send).get_task("42")
        assert exc.value.attempts == 2

    def test_timeout_maps_to_unavailable(self, token_env):
        def send(req, timeout):
            raise urllib.error.URLError(TimeoutError("timed out"))
        with pytest.raises(Unavailable) as exc:
            _provider(send).get_task("42")
        assert exc.value.attempts == 2  # GET-class: retried once


# ---------- retry budget: exactly one retry, GET-class + 429 only ----------

class TestRetryBudget:
    def test_get_5xx_then_200_succeeds_in_two_calls(self, token_env):
        send = CannedSend((500, {}, b"oops"), (200, {}, ISSUE_FIXTURE))
        t = _provider(send).get_task("42")
        assert t["ref"] == "42" and len(send.requests) == 2

    def test_get_retries_exactly_once_not_more(self, token_env):
        send = CannedSend((500, {}, b"1"), (500, {}, b"2"), (200, {}, ISSUE_FIXTURE))
        with pytest.raises(Unavailable):
            _provider(send).get_task("42")
        assert len(send.requests) == 2

    def test_429_respects_retry_after_capped_at_30s(self, token_env):
        sleeps = []
        send = CannedSend((429, {"Retry-After": "120"}, {}),
                          (200, {}, ISSUE_FIXTURE))
        provider = _provider(send)
        provider._sleep = sleeps.append
        t = provider.get_task("42")
        assert t["ref"] == "42"
        assert sleeps == [30]  # honored but capped

    def test_add_comment_never_retries(self, token_env):
        # A retried write can double-post; one attempt only, whatever fails.
        for status in (500, 429):
            send = CannedSend((status, {"Retry-After": "1"}, b"x"),
                              (201, {}, COMMENT_FIXTURE))
            provider = _provider(send)
            provider._sleep = lambda s: None
            with pytest.raises((Unavailable, RateLimited)):
                provider.add_comment("42", "body")
            assert len(send.requests) == 1, "write retried on %d" % status

    def test_401_404_are_not_retried(self, token_env):
        for status, exc_type in ((401, AuthError), (404, NotFound)):
            send = CannedSend((status, {}, {"message": "m"}),
                              (200, {}, ISSUE_FIXTURE))
            with pytest.raises(exc_type):
                _provider(send).get_task("42")
            assert len(send.requests) == 1, status


# ---------- no token leak in ANY error class ----------

class TestNoTokenLeak:
    @pytest.mark.parametrize("scenario", ["401", "404", "429", "500", "timeout"])
    def test_token_absent_from_exception_str_and_repr(self, token_env, scenario):
        if scenario == "timeout":
            def send(req, timeout):
                raise urllib.error.URLError(TimeoutError("timed out"))
        else:
            status = int(scenario)
            send = CannedSend((status, {"Retry-After": "1"}, {"message": "m"}),
                              (status, {"Retry-After": "1"}, {"message": "m"}))
        provider = _provider(send)
        provider._sleep = lambda s: None
        with pytest.raises(task_store.TaskStoreError) as exc:
            provider.get_task("42")
        blob = str(exc.value) + repr(exc.value) + repr(exc.value.args)
        assert TOKEN not in blob

    def test_token_absent_from_trace_after_mirror_failure(
            self, token_env, tmp_path, monkeypatch):
        # End-to-end: a failing mirror writes a trace event; the token must
        # not survive into the trace file either.
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("HARNESS_USER", "tester@local")
        send = CannedSend((500, {}, b"boom"))
        provider = _provider(send)
        monkeypatch.setattr(task_store, "load_adapter_optional",
                            lambda: provider)
        import claims
        r = claims.acquire("T-leak", lease_s=60)
        assert r["ok"]
        trace = tmp_path / "state" / "trace"
        blob = "".join(p.read_text(encoding="utf-8")
                       for p in trace.glob("trace-*.jsonl"))
        assert "mirror_failed" in blob
        assert TOKEN not in blob
