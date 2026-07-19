"""test_task_store_gitlab.py — GitLab provider: request building, GitLab→canonical
normalization, error mapping, retry budget, no-token-leak, and factory dispatch.

No network: the provider takes an injectable `send`; tests feed canned
(status, headers, body) tuples shaped like live GitLab v4 responses.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import task_store  # noqa: E402
import task_store_gitlab  # noqa: E402
from task_store import AuthError, ConfigError, NotFound, RateLimited, Unavailable  # noqa: E402

TOKEN = "glpat-SECRETSECRETSECRET"

ISSUE_FIXTURE = {
    "id": 1, "iid": 42, "title": "Fix the flange", "description": "It rattles.",
    "state": "opened", "labels": ["bug", "p1"],
    "author": {"username": "tanuki", "name": "The Tanuki", "email": "t@gl"},
    "web_url": "https://gitlab.com/grp/proj/-/issues/42",
}
LIST_FIXTURE = [ISSUE_FIXTURE,
                dict(ISSUE_FIXTURE, iid=43, title="Second", state="closed", labels=[])]
USER_FIXTURE = {"id": 1, "username": "tanuki", "name": "The Tanuki", "email": "t@gl"}


def _cfg(**over):
    cfg = {"base_url": "https://gitlab.example/api/v4", "project": "grp/proj",
           "token_env": "TEST_GL_TOKEN"}
    cfg.update(over)
    return cfg


class CannedSend:
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
    monkeypatch.setenv("TEST_GL_TOKEN", TOKEN)


def _provider(send, **over):
    return task_store_gitlab.GitLabTaskStore(_cfg(**over), send=send)


def test_get_task_normalizes_gitlab_shape(token_env):
    send = CannedSend((200, {}, ISSUE_FIXTURE))
    t = _provider(send).get_task(42)
    assert t == {"ref": "42", "title": "Fix the flange", "state": "open",
                 "body": "It rattles.", "labels": ["bug", "p1"],
                 "author": "tanuki", "url": "https://gitlab.com/grp/proj/-/issues/42"}
    # project path is one percent-encoded segment; addressed by iid
    assert "/projects/grp%2Fproj/issues/42" in send.requests[0].full_url


def test_request_uses_private_token_header(token_env):
    send = CannedSend((200, {}, ISSUE_FIXTURE))
    _provider(send).get_task(42)
    assert send.requests[0].get_header("Private-token") == TOKEN  # PRIVATE-TOKEN, not Bearer


def test_list_maps_open_to_opened_and_normalizes(token_env):
    send = CannedSend((200, {}, LIST_FIXTURE))
    out = _provider(send).list_tasks(filter={"state": "open", "labels": "bug"})
    assert [i["ref"] for i in out] == ["42", "43"]
    assert out[1]["state"] == "closed"
    assert "state=opened" in send.requests[0].full_url  # canonical open → GitLab opened


def test_add_comment_posts_to_notes_and_never_retries(token_env):
    send = CannedSend((500, {}, b""))  # a 500 on a write must NOT retry
    with pytest.raises(Unavailable) as ei:
        _provider(send).add_comment(42, "claimed by x")
    assert ei.value.attempts == 1
    assert len(send.requests) == 1
    assert send.requests[0].method == "POST"
    assert "/issues/42/notes" in send.requests[0].full_url


def test_get_retries_once_on_5xx(token_env):
    send = CannedSend((503, {}, b""), (200, {}, ISSUE_FIXTURE))
    t = _provider(send).get_task(42)
    assert t["ref"] == "42"
    assert len(send.requests) == 2  # one retry


def test_error_mapping(token_env):
    assert isinstance(_raise(_provider(CannedSend((401, {}, b""))).get_task, 1), AuthError)
    assert isinstance(_raise(_provider(CannedSend((404, {}, b""))).get_task, 1), NotFound)
    rl = _raise(_provider(CannedSend((429, {"Retry-After": "7"}, b""),
                                     (429, {"Retry-After": "7"}, b""))).get_task, 1)
    assert isinstance(rl, RateLimited) and rl.retry_after_s == 7


def test_missing_token_raises_auth_without_leaking(monkeypatch):
    monkeypatch.delenv("TEST_GL_TOKEN", raising=False)
    with pytest.raises(AuthError) as ei:
        _provider(CannedSend((200, {}, ISSUE_FIXTURE))).get_task(42)
    assert "TEST_GL_TOKEN" in str(ei.value)
    assert TOKEN not in str(ei.value)


def test_no_token_leak_in_any_error(token_env):
    # token must never appear in an error string even when the request "succeeds"
    # transport-wise but maps to an error status
    err = _raise(_provider(CannedSend((403, {}, b""))).get_task, 1)
    assert TOKEN not in str(err)


def test_whoami_returns_only_username(token_env):
    assert _provider(CannedSend((200, {}, USER_FIXTURE))).whoami() == "tanuki"


def test_factory_dispatches_gitlab(tmp_path, monkeypatch):
    cfg = tmp_path / "task-store.yaml"
    cfg.write_text("provider: gitlab\ngitlab: {project: grp/proj, token_env: GL_TOK}\n",
                   encoding="utf-8")
    monkeypatch.setenv("HARNESS_TASK_STORE_CONFIG", str(cfg))
    adapter = task_store.load_adapter()
    assert isinstance(adapter, task_store_gitlab.GitLabTaskStore)


def test_factory_gitlab_missing_project_is_config_error(tmp_path, monkeypatch):
    cfg = tmp_path / "task-store.yaml"
    cfg.write_text("provider: gitlab\ngitlab: {token_env: GL_TOK}\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TASK_STORE_CONFIG", str(cfg))
    with pytest.raises(ConfigError):
        task_store.load_adapter()


def _raise(fn, *args):
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 — return the exception for assertion
        return e
    raise AssertionError("expected an exception")
