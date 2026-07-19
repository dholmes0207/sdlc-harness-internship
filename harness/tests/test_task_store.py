"""test_task_store.py — provider-agnostic task-store surface + claims mirror.

The adapter is the ONLY thing in the harness that talks to a remote task
provider, and it is never on the gate path (gate hooks must stay
network-free — see the gate-path purity invariant). The contract suite runs
against an in-memory FakeProvider AND the GitHub provider wired to a canned
transport, so the surface semantics are pinned independent of any network.

Mirroring is advisory by design: the local claim is the source of truth, the
remote comment is a best-effort breadcrumb. A mirror failure must never roll
back or block an acquired claim.
"""
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import claims  # noqa: E402
import task_store  # noqa: E402
from task_store import (  # noqa: E402
    AuthError, ConfigError, NotFound, RateLimited, TaskStoreAdapter,
    TaskStoreError, Unavailable,
)

_DATA = Path(__file__).resolve().parent.parent / "data"


class FakeProvider(TaskStoreAdapter):
    """In-memory provider implementing the full ABC for contract tests."""

    def __init__(self):
        self.tasks = {
            "1": {"ref": "1", "title": "first", "state": "open",
                  "body": "b1", "labels": ["bug"], "url": None, "author": "a"},
            "2": {"ref": "2", "title": "second", "state": "closed",
                  "body": "b2", "labels": [], "url": None, "author": "b"},
        }
        self.comments = {}
        self.raise_on_comment = None

    def get_task(self, ref):
        ref = str(ref)
        if ref not in self.tasks:
            raise NotFound("task %s not found" % ref)
        return dict(self.tasks[ref])

    def list_tasks(self, filter=None):
        out = list(self.tasks.values())
        state = (filter or {}).get("state")
        if state and state != "all":
            out = [t for t in out if t["state"] == state]
        return [dict(t) for t in out]

    def add_comment(self, ref, body):
        if self.raise_on_comment is not None:
            raise self.raise_on_comment
        ref = str(ref)
        if ref not in self.tasks:
            raise NotFound("task %s not found" % ref)
        self.comments.setdefault(ref, []).append(body)
        return {"ref": ref, "body": body}

    def whoami(self):
        return "fake-user"


def _github_with_canned_store():
    """GitHubTaskStore against a canned transport emulating the same two
    issues, so the contract suite also pins the real provider's semantics."""
    import task_store_github

    issues = {
        "1": {"number": 1, "title": "first", "state": "open", "body": "b1",
              "labels": [{"name": "bug"}], "user": {"login": "a"},
              "html_url": "https://x/1"},
        "2": {"number": 2, "title": "second", "state": "closed", "body": "b2",
              "labels": [], "user": {"login": "b"}, "html_url": "https://x/2"},
    }
    comments = {}

    def send(req, timeout):
        url, method = req.full_url, req.get_method()
        m = re.search(r"/issues/(\d+)/comments$", url)
        if m and method == "POST":
            num = m.group(1)
            if num not in issues:
                return 404, {}, b'{"message": "Not Found"}'
            body = json.loads(req.data.decode("utf-8"))["body"]
            comments.setdefault(num, []).append(body)
            return 201, {}, json.dumps({"id": 9, "body": body}).encode()
        m = re.search(r"/issues/(\d+)$", url)
        if m:
            num = m.group(1)
            if num not in issues:
                return 404, {}, b'{"message": "Not Found"}'
            return 200, {}, json.dumps(issues[num]).encode()
        if re.search(r"/issues(\?|$)", url):
            state = "open"
            sm = re.search(r"[?&]state=(\w+)", url)
            if sm:
                state = sm.group(1)
            out = [i for i in issues.values()
                   if state == "all" or i["state"] == state]
            return 200, {}, json.dumps(out).encode()
        if url.endswith("/user"):
            return 200, {}, b'{"login": "fake-user"}'
        return 404, {}, b"{}"

    cfg = {"base_url": "https://api.github.example", "repo": "own/repo",
           "token_env": "TEST_GH_TOKEN", "api_version": "2022-11-28"}
    provider = task_store_github.GitHubTaskStore(cfg, send=send)
    provider._comments_store = comments
    return provider


@pytest.fixture(params=["fake", "github"])
def provider(request, monkeypatch):
    monkeypatch.setenv("TEST_GH_TOKEN", "tok-for-contract")
    if request.param == "fake":
        return FakeProvider()
    return _github_with_canned_store()


# ---------- contract suite (both providers) ----------

class TestAdapterContract:
    def test_get_task_returns_normalized_shape(self, provider):
        t = provider.get_task("1")
        assert t["ref"] == "1" and t["title"] == "first"
        assert t["state"] == "open" and t["labels"] == ["bug"]
        for field in ("ref", "title", "state", "body", "labels"):
            assert field in t, field

    def test_get_task_unknown_raises_notfound(self, provider):
        with pytest.raises(NotFound):
            provider.get_task("999")

    def test_list_tasks_filters_by_state(self, provider):
        assert {t["ref"] for t in provider.list_tasks({"state": "open"})} == {"1"}
        assert {t["ref"] for t in provider.list_tasks({"state": "all"})} == {"1", "2"}

    def test_add_comment_lands_on_the_task(self, provider):
        provider.add_comment("1", "hello from contract")
        if isinstance(provider, FakeProvider):
            stored = provider.comments["1"]
        else:
            stored = provider._comments_store["1"]
        assert stored == ["hello from contract"]

    def test_add_comment_unknown_task_raises_notfound(self, provider):
        with pytest.raises(NotFound):
            provider.add_comment("999", "x")

    def test_whoami_returns_login(self, provider):
        assert provider.whoami() == "fake-user"


# ---------- error taxonomy ----------

class TestErrorTaxonomy:

    def test_rate_limited_carries_retry_after(self):
        e = RateLimited("slow down", retry_after_s=7)
        assert e.retry_after_s == 7

    def test_unavailable_carries_attempts(self):
        e = Unavailable("boom", attempts=2)
        assert e.attempts == 2


# ---------- config + factory ----------

class TestConfigAndFactory:
    def test_tracked_config_parses_and_never_holds_a_token(self):
        raw = (_DATA / "task-store.yaml").read_text(encoding="utf-8")
        cfg = task_store.load_config()
        assert cfg["provider"] == "github"
        assert cfg["github"]["token_env"] == "GITHUB_TOKEN"
        assert cfg["github"]["api_version"] == "2022-11-28"
        # The file names WHICH env var holds the token; it never holds one.
        for line in raw.splitlines():
            assert not re.search(r"(?<!_)token\s*:", line.split("#")[0]), line

    def test_missing_config_file_is_actionable(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        with pytest.raises(ConfigError) as exc:
            task_store.load_config(path=missing)
        assert str(missing) in str(exc.value)

    def test_unknown_provider_names_file_and_key(self, tmp_path):
        p = tmp_path / "ts.yaml"
        p.write_text("provider: jira\n", encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            task_store.load_adapter(path=p)
        msg = str(exc.value)
        assert str(p) in msg and "provider" in msg

    def test_missing_token_env_key_names_file_and_key(self, tmp_path):
        p = tmp_path / "ts.yaml"
        p.write_text("provider: github\ngithub: {repo: a/b}\n", encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            task_store.load_adapter(path=p)
        msg = str(exc.value)
        assert str(p) in msg and "token_env" in msg

    def test_factory_builds_github_provider(self, tmp_path):
        p = tmp_path / "ts.yaml"
        p.write_text(
            "provider: github\n"
            "github: {repo: a/b, token_env: T}\n", encoding="utf-8")
        adapter = task_store.load_adapter(path=p)
        import task_store_github
        assert isinstance(adapter, task_store_github.GitHubTaskStore)

    def test_optional_loader_none_when_file_absent(self, tmp_path):
        assert task_store.load_adapter_optional(path=tmp_path / "absent.yaml") is None

    def test_optional_loader_still_raises_on_broken_present_config(self, tmp_path):
        p = tmp_path / "ts.yaml"
        p.write_text("provider: jira\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            task_store.load_adapter_optional(path=p)


# ---------- comment-text sanitization ----------

class TestSanitizeCommentText:
    def test_neutralizes_pings_and_markdown(self):
        out = task_store.sanitize_comment_text(
            "user:[email protected]/agent:`rev` **bold** [link](http://e) @org/team")
        assert "@" not in out
        for ch in "`*[]<>#!|":
            assert ch not in out
        # Input parens are stripped BEFORE @ -> (at), so the only parens
        # left are the token's own — no `[x](url)` link can survive.
        assert out.count("(") == out.count("(at)") == out.count(")")
        assert "(at)" in out

    def test_strips_control_chars_and_caps_length(self):
        out = task_store.sanitize_comment_text("a\x00b\nc" + "x" * 500, max_len=50)
        assert "\x00" not in out and "\n" not in out
        assert len(out) <= 50


# ---------- claims mirror (advisory, fail-open) ----------

@pytest.fixture()
def mirror_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_USER", "tester@local")
    monkeypatch.setenv("HARNESS_TASK_STORE_CONFIG", str(tmp_path / "absent.yaml"))
    return tmp_path / "state"


def _trace_text(state_dir):
    trace = state_dir / "trace"
    if not trace.is_dir():
        return ""
    return "".join(p.read_text(encoding="utf-8")
                   for p in sorted(trace.glob("trace-*.jsonl")))


class TestClaimsMirror:
    def test_acquire_mirrors_one_comment_with_actor_and_expiry(
            self, mirror_env, monkeypatch):
        fake = FakeProvider()
        fake.tasks["T-1"] = {"ref": "T-1", "title": "t", "state": "open",
                             "body": "", "labels": [], "url": None, "author": "x"}
        monkeypatch.setattr(task_store, "load_adapter_optional", lambda: fake)
        r = claims.acquire("T-1", lease_s=60)
        assert r["ok"]
        assert len(fake.comments["T-1"]) == 1
        body = fake.comments["T-1"][0]
        assert "claimed by" in body and "until" in body
        assert "tester(at)local" in body  # actor sanitized, no raw @
        assert r["claim"]["expires_ts"] in body
        assert "claim_mirrored" in _trace_text(mirror_env)

    def test_mirror_failure_keeps_claim_and_traces(
            self, mirror_env, monkeypatch, capfd):
        fake = FakeProvider()
        fake.raise_on_comment = Unavailable("github down", attempts=1)
        fake.tasks["T-2"] = dict(fake.tasks["1"], ref="T-2")
        monkeypatch.setattr(task_store, "load_adapter_optional", lambda: fake)
        r = claims.acquire("T-2", lease_s=60)
        assert r["ok"], "mirror failure must never roll back the claim"
        assert (mirror_env / "claims" / "T-2.claim").exists()
        assert "mirror_failed" in _trace_text(mirror_env)
        assert "mirror" in capfd.readouterr().err.lower()

    def test_no_mirror_attempt_when_store_not_configured(
            self, mirror_env, capfd):
        # HARNESS_TASK_STORE_CONFIG points at an absent file: unconfigured
        # is a silent skip, not a warned failure.
        r = claims.acquire("T-3", lease_s=60)
        assert r["ok"]
        assert "mirror" not in capfd.readouterr().err.lower()
        assert "mirror" not in _trace_text(mirror_env)

    def test_no_mirror_when_acquire_loses(self, mirror_env, monkeypatch):
        fake = FakeProvider()
        fake.tasks["T-4"] = dict(fake.tasks["1"], ref="T-4")
        monkeypatch.setattr(task_store, "load_adapter_optional", lambda: fake)
        claims.acquire("T-4", lease_s=60)
        claims.acquire("T-4", lease_s=60)  # loser
        assert len(fake.comments["T-4"]) == 1

    def test_claims_imports_task_store_lazily(self):
        # Gate-path purity is transitive: anything importing claims at module
        # level must not drag the network adapter into its import graph.
        src = (_SCRIPTS / "claims.py").read_text(encoding="utf-8")
        assert not re.search(r"(?m)^(?:from|import)\s+task_store\b", src)
