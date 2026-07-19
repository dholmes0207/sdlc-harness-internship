"""Tests for privacy_read_guard.py — block reads of secret files (compliance).

Restores the upstream block→approve control that an earlier port had downgraded
to an advisory nudge. On PreToolUse:Read of a likely-secret file (.env, *.pem,
*.key, credentials, secrets.yaml, id_rsa/id_ed25519) the read is BLOCKED (exit 2)
and an @@PRIVACY_PROMPT@@ marker carrying AskUserQuestion JSON is emitted.

Compliance class properties under test:
  - default ON: a bare config (no entry) still blocks a sensitive read — the gate
    ships awake. This is the property the nudge lacked.
  - blocks with exit 2 + a parseable approval marker the controller can surface.
  - break-glass: an explicit `enabled: false` makes the gate inert (exit 0).
  - signal-gated: only blocks a sensitive path; example/sample/template exempt,
    ordinary files and non-Read tools pass.
  - fail-open on absent/unparseable input (a transport hiccup must not brick Read).

Tested via subprocess + real stdin JSON (code-standards §7), HARNESS_ROOT seam.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK_PATH = _HOOKS / "privacy_read_guard.py"

# Compliance default is ENABLED, so a bare config already blocks. The break-glass
# is an explicit per-hook `enabled: false`.
_DEFAULT = "hooks: {}\n"
_DISABLED = "hooks:\n  privacy_read_guard: {enabled: false}\n"

_START = "@@PRIVACY_PROMPT_START@@"
_END = "@@PRIVACY_PROMPT_END@@"


def _run(root: Path, config: Path, payload, raw: bool = False):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env["HARNESS_HOOK_CONFIG"] = str(config)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin, text=True, capture_output=True, env=env,
    )


def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _read(path: str):
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def _marker_json(stderr: str) -> dict:
    """Pull the JSON the controller would parse from between the markers."""
    assert _START in stderr and _END in stderr, stderr
    body = stderr.split(_START, 1)[1].split(_END, 1)[0].strip()
    return json.loads(body)


def test_blocks_dotenv_by_default(tmp_path):
    # Bare config (no per-hook entry) — proves the gate is ON by default.
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read("/repo/.env"))
    assert r.returncode == 2
    assert _START in r.stderr
    assert ".env" in r.stderr


def test_blocks_symlink_alias_to_secret(tmp_path):
    # F2: `ln -s .env innocent.txt; Read(innocent.txt)` must not slip the secret
    # past on a non-matching name — the guard resolves the alias before matching.
    (tmp_path / ".env").write_text("SECRET=x\n", encoding="utf-8")
    alias = tmp_path / "innocent.txt"
    alias.symlink_to(tmp_path / ".env")
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(str(alias)))
    assert r.returncode == 2, r.stderr[:200]


def test_symlink_to_benign_file_passes(tmp_path):
    # the resolve must not over-block: an alias to a non-secret reads fine.
    (tmp_path / "notes.md").write_text("hi\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(tmp_path / "notes.md")
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(str(alias)))
    assert r.returncode == 0, r.stderr[:200]


def test_blocks_dotenv_variant(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read("config/.env.production"))
    assert r.returncode == 2
    assert _START in r.stderr


def test_blocks_pem_key_idrsa_ed25519(tmp_path):
    for p in ("deploy/server.pem", "tls/private.key",
              "/home/u/.ssh/id_rsa", "/home/u/.ssh/id_ed25519"):
        r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(p))
        assert r.returncode == 2, p
        assert _START in r.stderr, p


def test_blocks_all_ssh_private_key_types(tmp_path):
    # All four common SSH private-key types are equally sensitive — gating
    # id_rsa/id_ed25519 but not id_ecdsa/id_dsa was an inconsistent coverage gap
    # (ECDSA is the default key type on many modern systems).
    for p in ("/home/u/.ssh/id_ecdsa", "/home/u/.ssh/id_dsa"):
        r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(p))
        assert r.returncode == 2, p
        assert _START in r.stderr, p


def test_blocks_uppercase_secret_variants(tmp_path):
    # on a case-insensitive FS (macOS/Windows) .ENV IS .env — the gate must not
    # leak it through a casing difference.
    for p in ("/repo/.ENV", "config/.Env.PRODUCTION", "deploy/server.PEM",
              "tls/private.KEY", "/home/u/.ssh/id_RSA"):
        r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(p))
        assert r.returncode == 2, p
        assert _START in r.stderr, p


def test_blocks_notebookread_of_secret(tmp_path):
    # NotebookRead is a pure read path — a secret behind it must still be gated
    payload = {"tool_name": "NotebookRead", "tool_input": {"file_path": "/repo/.env"}}
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), payload)
    assert r.returncode == 2
    assert _START in r.stderr


def test_blocks_credentials_and_secrets_yaml(tmp_path):
    for p in ("config/credentials.json", "k8s/secrets.yaml"):
        r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(p))
        assert r.returncode == 2, p
        assert _START in r.stderr, p


def test_marker_carries_askuser_json(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read("/repo/.env"))
    assert r.returncode == 2
    data = _marker_json(r.stderr)
    assert data["type"] == "PRIVACY_PROMPT"
    assert data["file"] == "/repo/.env"
    assert data["basename"] == ".env"
    assert len(data["question"]["options"]) == 2
    # The block reason must tell the agent how to proceed once approved.
    assert "cat" in r.stderr


def test_allows_env_example_template(tmp_path):
    # example/sample/template are documentation, not secrets → never block
    for p in (".env.example", ".env.sample", "config.env.template"):
        r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read(p))
        assert r.returncode == 0, p
        assert _START not in r.stderr, p


def test_allows_ordinary_file(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), _read("src/main.py"))
    assert r.returncode == 0
    assert _START not in r.stderr


def test_ignores_non_read_tool(tmp_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env"}}
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), payload)
    assert r.returncode == 0
    assert _START not in r.stderr


def test_break_glass_disabled_allows(tmp_path):
    # explicit enabled:false is the only way past the gate on a sensitive target
    r = _run(tmp_path, _cfg(tmp_path, _DISABLED), _read("/repo/.env"))
    assert r.returncode == 0
    assert _START not in r.stderr


def test_failopen_on_malformed_stdin(tmp_path):
    # absent/unparseable payload names no file to gate → must not brick Read
    r = _run(tmp_path, _cfg(tmp_path, _DEFAULT), "}{ not json", raw=True)
    assert r.returncode == 0


def test_public_keys_are_not_blocked():
    """A .pub is a PUBLIC key — safe to read. Blocking id_*.pub is a false
    positive that trains users to rubber-stamp the approval prompt."""
    import sys
    sys.path.insert(0, str(_HOOKS))
    import privacy_read_guard as prg
    # private SSH keys stay sensitive
    assert prg._is_sensitive("/home/u/.ssh/id_rsa")
    assert prg._is_sensitive("/home/u/.ssh/id_ed25519")
    assert prg._is_sensitive("config/id_ecdsa")
    # public counterparts are safe
    assert not prg._is_sensitive("/home/u/.ssh/id_rsa.pub")
    assert not prg._is_sensitive("/home/u/.ssh/id_ed25519.pub")
    assert not prg._is_sensitive("config/id_ecdsa.pub")
    # other secret patterns stay blocked (no broadening of the exemption)
    assert prg._is_sensitive("server.pem")
    assert prg._is_sensitive("api.key")
    assert prg._is_sensitive("project/credentials.json")


def test_extended_secret_file_formats_flagged():
    # red-team coverage: cloud creds, kubeconfig, keystores, registry tokens, and
    # infra secret files were all slipping silently. Each must now flag — a missed
    # read is permanent context contamination.
    sys.path.insert(0, str(_HOOKS))
    import privacy_read_guard as prg
    for path in ("home/.aws/config", "proj/.kube/config", "x.kubeconfig",
                 "k8s/serviceAccount.json", "client_secret_abc.json", "release.p12",
                 "app.jks", "vault.keystore", "auth.p8", ".npmrc", "config/.pypirc",
                 ".netrc", "_netrc", "deploy/.docker/config.json", "prod.tfvars",
                 "vars.auto.tfvars", "wp-config.php", "config/database.yml",
                 ".azure/accessTokens.json", ".config/gcloud/credentials.db"):
        assert prg._is_sensitive(path), "%s should be sensitive" % path
    # benign config/code files must NOT flag (else the gate trains rubber-stamping)
    for path in ("config.json", "package.json", "tsconfig.json", "docker-compose.yml",
                 "src/main.py", "README.md", "data/settings.yaml"):
        assert not prg._is_sensitive(path), "%s should be clean" % path
