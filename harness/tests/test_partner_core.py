"""partner_core primitives (Result/Inert/Degraded, JobRegistry, time/transient/id
helpers, git helper) — provider-agnostic, standalone from any lane. Both the
gemini lane and a future partner lane import these instead of duplicating them.
"""
import sys
from pathlib import Path

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_core as pc  # noqa: E402


def test_result_types_shape():
    r = pc.Result(content={"text": "ok"}, provenance={"engine": "gemini"}, session="s1")
    assert r.status == "ok"
    assert r.content == {"text": "ok"}
    assert r.provenance == {"engine": "gemini"}
    assert r.session == "s1"

    i = pc.Inert(reason="master=off", provenance={"purpose": "review"})
    assert i.status == "inert"
    assert i.reason == "master=off"

    d = pc.Degraded(provenance={"engine": "gemini"}, reason="all engines failed")
    assert d.status == "degraded"
    assert d.reason == "all engines failed"


def test_job_registry_subdir(tmp_path):
    reg = pc.JobRegistry(state_dir=tmp_path, subdir="partner")
    assert reg._dir == tmp_path / "partner"

    rec = reg.append({"job_id": "abc123", "status": "running"})
    assert "actor" in rec
    assert "ts" in rec

    rows = reg.read_all()
    assert len(rows) == 1
    assert rows[0]["job_id"] == "abc123"
    assert "actor" in rows[0]
    assert "ts" in rows[0]


def test_job_registry_default_subdir_is_gemini(tmp_path):
    reg = pc.JobRegistry(state_dir=tmp_path)
    assert reg._dir == tmp_path / "gemini"


def test_read_all_skips_torn_line(tmp_path):
    reg = pc.JobRegistry(state_dir=tmp_path, subdir="partner")
    reg.append({"job_id": "aaa", "status": "running"})
    # Simulate a torn/corrupt JSONL line landing between two valid records
    # (e.g. a partial write from a crash) — append() always writes whole
    # lines, so this is written directly to exercise the read path.
    with open(reg.path, "a", encoding="utf-8") as fh:
        fh.write('{"job_id": "bbb", "status": "runni\n')
    reg.append({"job_id": "ccc", "status": "done"})

    rows = reg.read_all()
    job_ids = [r["job_id"] for r in rows]
    assert job_ids == ["aaa", "ccc"]
    assert reg.latest("aaa") is not None
    assert reg.latest("ccc") is not None


def test_is_transient():
    class _Err(Exception):
        pass

    assert pc._is_transient(_Err("Rate Limit hit"), ["rate_limit"]) is False
    assert pc._is_transient(_Err("rate_limit exceeded"), ["rate_limit"]) is True
    assert pc._is_transient(_Err("RATE_LIMIT exceeded"), ["rate_limit"]) is True


def test_new_job_id_and_git_out(tmp_path):
    jid1 = pc._new_job_id()
    jid2 = pc._new_job_id()
    assert jid1 != jid2
    assert len(jid1) == 12

    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    out = pc._git_out(tmp_path, "status", "--porcelain")
    assert out == ""


def test_now_iso_format():
    ts = pc._now_iso()
    assert "T" in ts
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_partner_core_does_not_import_gemini_lane():
    import ast
    tree = ast.parse((_PLUGIN_SCRIPTS / "partner_core.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "gemini" not in alias.name, alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "gemini" not in node.module, node.module
