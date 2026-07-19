"""dev_override_wiring detector — a .harness-dev/<name>.yaml override that no
HARNESS_* env points to is silently ignored; the detector surfaces exactly
those, and stays silent on a non-dev tree or a fully-wired one.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import dev_override_wiring as dow  # noqa: E402


def _mk(project: Path, dev_files, env=None):
    dev = project / ".harness-dev"
    dev.mkdir(parents=True, exist_ok=True)
    for name in dev_files:
        (dev / name).write_text("{}\n", encoding="utf-8")
    claude = project / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    (claude / "settings.local.json").write_text(
        json.dumps({"env": env or {}}), encoding="utf-8")


def test_non_dev_tree_is_silent(tmp_path):
    # no .harness-dev/ → never a dev-repo concern
    assert dow.collect(str(tmp_path)) is None


def test_unwired_override_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    _mk(tmp_path, ["partner.yaml"], env={})
    sig = dow.collect(str(tmp_path))
    assert sig == {"unwired": ["partner.yaml"]}


def test_wired_override_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    # a relative HARNESS_ value (resolved against the project) counts as wired
    _mk(tmp_path, ["partner.yaml"],
        env={"HARNESS_PARTNER": ".harness-dev/partner.yaml"})
    assert dow.collect(str(tmp_path)) is None


def test_absolute_env_value_counts_as_wired(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    abs_path = str(tmp_path / ".harness-dev" / "guard-policy.yaml")
    _mk(tmp_path, ["guard-policy.yaml"], env={"HARNESS_GUARD_POLICY": abs_path})
    assert dow.collect(str(tmp_path)) is None


def test_no_env_needed_files_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    # farm off-list + auto-discovered voice file need no env — never flagged
    _mk(tmp_path, ["dev-off-skills.yaml", "terminal-voice.yaml"], env={})
    assert dow.collect(str(tmp_path)) is None


def test_only_the_unwired_ones_surface(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    _mk(tmp_path,
        ["partner.yaml", "guard-policy.yaml", "dev-off-skills.yaml"],
        env={"HARNESS_GUARD_POLICY": ".harness-dev/guard-policy.yaml"})
    # partner unwired; guard-policy wired; dev-off-skills skipped
    assert dow.collect(str(tmp_path)) == {"unwired": ["partner.yaml"]}


def test_live_env_wiring_also_counts(tmp_path, monkeypatch):
    # a HARNESS_ exported into the process env (not settings.local) still wires
    abs_path = str(tmp_path / ".harness-dev" / "output.yaml")
    monkeypatch.setattr("os.environ", {"HARNESS_OUTPUT": abs_path}, raising=False)
    _mk(tmp_path, ["output.yaml"], env={})
    assert dow.collect(str(tmp_path)) is None


def test_missing_settings_local_fails_soft(tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {}, raising=False)
    dev = tmp_path / ".harness-dev"
    dev.mkdir()
    (dev / "partner.yaml").write_text("{}\n", encoding="utf-8")
    # no .claude/settings.local.json at all → still detects the unwired file
    assert dow.collect(str(tmp_path)) == {"unwired": ["partner.yaml"]}
