"""The disabled-skill reference nudge (advisory, fail-open).

After the 2.0.0 collapse every skill lives in one `hs` plugin and an install can
omit skills at the dir level. A handoff that points at an OMITTED skill is NOT a
broken reference — the nudge spots it and suggests re-enabling (or reading it
inline), without ever deleting the reference or blocking. The 13-skill spine is
never omittable, so a spine ref never trips it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "harness" / "hooks"
sys.path.insert(0, str(HOOKS))
import disabled_ref_nudge as drn  # noqa: E402


def _omit(tmp: Path, skills) -> None:
    d = tmp / "harness" / "state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "install-omitted-skills.json").write_text(
        json.dumps({"omitted": list(skills)}))


def test_ref_to_omitted_skill_nudges(tmp_path, monkeypatch):
    _omit(tmp_path, ["remember"])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    msg = drn.core({"prompt": "please run hs:remember to save this"})
    assert msg, "expected a nudge for an omitted-skill ref"
    assert "hs:remember" in msg
    assert "enable" in msg.lower()
    # the /hs:use proxy path is one of the three offered routes
    assert "/hs:use remember" in msg


def test_ref_to_installed_skill_is_silent(tmp_path, monkeypatch):
    _omit(tmp_path, ["journal"])  # remember is NOT omitted
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert drn.core({"prompt": "run hs:remember"}) is None


def test_spine_ref_never_nudges(tmp_path, monkeypatch):
    # even if the record somehow lists a spine skill, the common path: no omit at all
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert drn.core({"prompt": "run hs:plan then hs:cook then hs:ship"}) is None


def test_no_refs_is_silent(tmp_path, monkeypatch):
    _omit(tmp_path, ["remember"])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert drn.core({"prompt": "just a normal message, no skill refs"}) is None


def test_no_omit_record_is_silent(tmp_path, monkeypatch):
    # a dogfood/dev tree with no install-omitted record never nudges (fail-open)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert drn.core({"prompt": "run hs:remember and hs:discover"}) is None


def test_main_is_fail_open_on_garbage_stdin(tmp_path):
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(yaml.safe_dump({"hooks": {"disabled_ref_nudge": {"enabled": True}}}))
    env = {
        "PATH": "/usr/bin:/bin",
        "HARNESS_HOOK_CONFIG": str(cfg),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
    }
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "disabled_ref_nudge.py")],
        input="this is not json", capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0


def test_registered_and_enabled():
    # migrated into the in-process dispatcher (UserPromptSubmit): it fires as a core of
    # hook_dispatch.py, registered in hook-dispatch.yaml rather than its own command.
    disp = yaml.safe_load((ROOT / "harness/data/hook-dispatch.yaml").read_text())
    assert any(c.get("module") == "disabled_ref_nudge"
               for grp in disp["groups"].values() for c in grp)
    cfg = yaml.safe_load((ROOT / "harness/data/harness-hooks.yaml").read_text())
    entry = (cfg.get("hooks") or {}).get("disabled_ref_nudge") or {}
    assert entry.get("enabled") is True


@pytest.mark.dev_repo  # reads the dev CLAUDE.md routing — absent on installs
def test_rule_exists_and_routed_in_claude_md():
    assert (ROOT / "harness/rules/disabled-group-handling.md").is_file()
    assert "disabled-group-handling" in (ROOT / "CLAUDE.md").read_text()


def test_ref_inside_url_is_not_a_false_positive(tmp_path, monkeypatch):
    # a skill-looking token inside a URL (preceded by '/') is not a code reference
    _omit(tmp_path, ["remember", "plan"])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert drn.core(
        {"prompt": "see https://example.com/hs:remember and www.hs:plan.io"}
    ) is None
