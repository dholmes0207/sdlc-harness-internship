"""test_secret_scan_before_ship.py — pre-ship secret leak gate (compliance, fail-closed).

The gate fires only at the leak boundary (push/pr/ship/deploy), scans the diff of the
commits about to leave the machine, and BLOCKS when a real secret pattern appears in an
added line of a non-excluded file. Test/fixture/docs paths are excluded so the gate never
self-blocks on its own fake-secret fixtures and avoids the common false positive.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

from conftest import _git  # noqa: E402

import secret_scan_before_ship as ss  # noqa: E402

# fake secrets assembled at runtime so this very test file does not ship a literal match
_AWS = "AKIA" + "1234567890ABCDEF"          # AKIA + 16
_PEM = "-----BEGIN RSA PRIVATE KEY-----"


def test_scan_detects_aws_key():
    assert ss.scan_text("aws = %s" % _AWS)


def test_scan_detects_private_key_pem():
    assert ss.scan_text("key:\n%s\n..." % _PEM)


def test_scan_detects_generic_api_key_assignment():
    assert ss.scan_text('api_key = "abcd1234efgh5678ij"')


def test_scan_clean_text_is_empty():
    assert ss.scan_text("just some normal code, no secrets here = 42") == []


def test_added_lines_skip_excluded_paths():
    # realistic unified-diff pairs (--- then +++); a bare +++ with no preceding
    # --- is scanned as added content by design (the ++ evasion fix), so each
    # file header carries its --- partner as real git output always does.
    diff = (
        "--- a/src/config.py\n"
        "+++ b/src/config.py\n"
        "+api_key = \"abcd1234efgh5678ij\"\n"
        "--- a/tests/test_thing.py\n"
        "+++ b/tests/test_thing.py\n"
        "+api_key = \"zzzz1111yyyy2222ww\"\n"
    )
    scannable = ss.scannable_added_lines(diff)
    assert "src/config.py" not in scannable  # header lines are not content
    assert "abcd1234efgh5678ij" in scannable        # real source file kept
    assert "zzzz1111yyyy2222ww" not in scannable     # test file excluded


def test_test_name_exclusion_is_scoped_to_py_not_published_libs():
    # a real published module merely NAMED test_/_test in another language must be
    # SCANNED (lib/test_helpers.js, Go's server_test.go) — only the .py pytest
    # convention is exempt (its fake fixtures would otherwise DoS the gate).
    assert not ss._excluded("lib/test_helpers.js")
    assert not ss._excluded("src/server_test.go")
    assert ss._excluded("test_foo.py")             # pytest at root: exempt
    assert ss._excluded("pkg/utils_test.py")       # pytest _test alt: exempt
    assert ss._excluded("tests/test_x.go")         # under a test dir: exempt by dir anchor


# ---- end-to-end on a real temp repo ----------------------------------------

def _repo(tmp_path, content):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    return repo


def test_gate_blocks_push_with_secret(tmp_path):
    repo = _repo(tmp_path, "AWS = '%s'\n" % _AWS)
    reason = ss.gate_reason("git push origin main", str(repo))
    assert reason and "secret" in reason.lower()


def test_gate_allows_clean_push(tmp_path):
    repo = _repo(tmp_path, "def f():\n    return 42\n")
    assert ss.gate_reason("git push origin main", str(repo)) is None


def test_gate_ignores_non_ship_command(tmp_path):
    repo = _repo(tmp_path, "AWS = '%s'\n" % _AWS)
    # a non-ship Bash command is not gated even with a secret present
    assert ss.gate_reason("ls -la", str(repo)) is None


def test_gate_blocks_ship_with_untracked_secret(tmp_path):
    # npm/cargo publish + docker push pack the WORKING TREE, including UNTRACKED
    # files that are not in any git commit — for ship/deploy (no transport backstop)
    # the pack surface must be scanned, not just unpushed commits.
    repo = _repo(tmp_path, "def f():\n    return 42\n")          # clean committed
    (repo / "secret.js").write_text("KEY = '%s'\n" % _AWS, encoding="utf-8")  # untracked
    reason = ss.gate_reason("npm publish", str(repo))
    assert reason and "secret" in reason.lower()


def test_gate_push_ignores_untracked_secret(tmp_path):
    # git push transmits only commits, so an untracked working-tree secret is not a
    # push leak — the pack-surface scan is ship/deploy-only.
    repo = _repo(tmp_path, "def f():\n    return 42\n")
    (repo / "secret.js").write_text("KEY = '%s'\n" % _AWS, encoding="utf-8")
    assert ss.gate_reason("git push origin main", str(repo)) is None


def test_gate_ship_allows_clean_working_tree(tmp_path):
    repo = _repo(tmp_path, "def f():\n    return 42\n")
    assert ss.gate_reason("npm publish", str(repo)) is None


def test_gate_ship_excludes_untracked_test_file(tmp_path):
    repo = _repo(tmp_path, "def f():\n    return 42\n")
    (repo / "test_keys.py").write_text("KEY = '%s'\n" % _AWS, encoding="utf-8")
    assert ss.gate_reason("npm publish", str(repo)) is None


def test_added_content_starting_with_plusplus_is_scanned():
    # An added line whose CONTENT starts with '++' renders in a unified diff as
    # '+++<content>' (one '+' for "added" + the '++' of the content). The header
    # check must not mistake it for a '+++ b/path' file header and skip it —
    # otherwise a secret on such a line evades the gate.
    token = "ghp_" + "A" * 30
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -0,0 +1 @@\n"
        '+++token = "%s"\n' % token
    )
    body = ss.scannable_added_lines(diff)
    assert "ghp_" in body, "++-prefixed added content was dropped: %r" % body
    assert ss.scan_text(body), "secret on a ++-prefixed added line evaded the scan"


def test_new_high_confidence_formats_caught():
    import secret_scan_before_ship as s
    assert "stripe-key" in s.scan_text("sk_live_" + "a" * 24)
    assert "github-fine-pat" in s.scan_text("github_pat_" + "a" * 24)
    assert "slack-token" in s.scan_text("xoxb-1234567890-abcdefghij")
    assert "google-api-key" in s.scan_text("AIza" + "a" * 35)
    assert "generic-secret" in s.scan_text('password = "' + "x" * 20 + '"')
    assert s.scan_text("ordinary prose, no token or key here at all") == []


def test_fail_closed_when_unpushed_diff_unreadable(monkeypatch):
    import secret_scan_before_ship as s
    import stage_detector
    monkeypatch.setattr(s, "gather_unpushed_diff", lambda root: None)
    monkeypatch.setattr(stage_detector, "detect_stage", lambda c: "push")
    reason = s.gate_reason("git push origin main", "/tmp")
    assert reason and "failing closed" in reason


def test_clean_empty_diff_still_passes(monkeypatch):
    # "" (a clean repo with nothing unpushed) must NOT fail closed
    import secret_scan_before_ship as s
    import stage_detector
    monkeypatch.setattr(s, "gather_unpushed_diff", lambda root: "")
    monkeypatch.setattr(stage_detector, "detect_stage", lambda c: "push")
    assert s.gate_reason("git push origin main", "/tmp") is None


def test_jwt_and_npm_formats_caught():
    import secret_scan_before_ship as s
    assert "npm-token" in s.scan_text("npm_" + "a" * 36)
    assert "jwt" in s.scan_text(
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghij")
    # prose containing 'eyebrows' or 'npm install' must not false-match
    assert s.scan_text("run npm install then check your eyebrows") == []


def test_secret_split_across_added_lines_is_caught(monkeypatch):
    import secret_scan_before_ship as s
    import stage_detector
    diff = "+++ b/cfg.py\n+AKIA\n+IOSFODNN7EXAMPLE = 1\n"
    monkeypatch.setattr(s, "gather_unpushed_diff", lambda root: diff)
    monkeypatch.setattr(stage_detector, "detect_stage", lambda c: "push")
    reason = s.gate_reason("git push origin main", "/tmp")
    assert reason and "aws-access-key" in reason


def test_red_team_false_negatives_now_caught():
    # F1-F11: each previously-missed real-world format now matches its pattern.
    samples = {
        "aws-secret-key": "aws_secret_access_key=" + "abcd1234" * 5,        # 40 chars, digits
        "openai-key": "sk-proj-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",      # 32 alnum
        "gitlab-pat": "glpat-" + "a1b2c3d4e5f6g7h8i9j0",                    # 20
        "db-uri-cred": "postgres://admin:s3cr3t@db.internal:5432/app",
        "sendgrid-key": "SG." + "a" * 22 + "." + "b" * 43,
        "twilio-key": "SK" + "0a1b2c3d4e5f60718293a4b5c6d7e8f9",            # 32 hex
        "stripe-key": "rk_live_" + "a1b2c3d4e5f6g7h8i9j0",                  # restricted key variant
        "hf-token": "hf_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5",               # 30
        "do-token": "dop_v1_" + "0123456789abcdef" * 4,                    # 64 hex
        "azure-account-key": "AccountKey=" + "abcd1234" * 6,               # 48 base64
        "generic-secret-unquoted": "DB_PASSWORD=h7Kp9mXq2wLs5tRv8nZb",     # unquoted, digit
    }
    for name, sample in samples.items():
        assert name in ss.scan_text(sample), "%s missed: %r" % (name, sample)


def test_db_uri_local_dev_host_not_flagged():
    # FP fix: a db connection string whose HOST is local-dev (localhost / loopback /
    # a dotless compose service name / *.local) is not a remotely-usable leak, so the
    # db-uri-cred pattern must NOT fire on it (the common docker-compose false positive).
    for uri in (
        "postgres://postgres:postgres@localhost:5432/dev",
        "mongodb://admin:admin@mongo:27017",
        "redis://:devpass@redis:6379/0",
        "postgresql://user:pass123@db.local/app",
        "mysql://root:root@127.0.0.1:3306/app",
    ):
        assert "db-uri-cred" not in ss.scan_text(uri), "local-dev URI false-flagged: %r" % uri


def test_db_uri_remote_host_still_flagged():
    # Guard the FP fix: a URI pointing OFF-machine (dotted FQDN or non-loopback IP)
    # is still a real, remotely-usable leak and must keep firing.
    for uri in (
        "postgres://admin:s3cr3t@db.internal:5432/app",
        "mongodb+srv://user:pass123@cluster0.abcd.mongodb.net/app",
        "postgres://user:pass123@10.0.0.5:5432/app",
    ):
        assert "db-uri-cred" in ss.scan_text(uri), "remote URI missed: %r" % uri


def test_pgp_block_caught_without_self_matching():
    assert "pgp-private-key" in ss.scan_text("-----BEGIN PGP PRIVATE KEY BLOCK-----")


def test_documentation_placeholders_not_flagged():
    # each WOULD match a generic pattern but is an obvious placeholder -> exempt,
    # so the gate does not false-block help text and get itself disabled.
    for ph in ("API_KEY='your-api-key-here'",
               "password='changeme0123456789'",
               "API_KEY=your-example-key-12345"):
        assert ss.scan_text(ph) == [], "placeholder flagged: %r" % ph


def test_template_files_excluded_from_scan():
    assert ss._excluded("config/.env.example")
    assert ss._excluded("settings.sample")
    assert ss._excluded("docker-compose.template")
