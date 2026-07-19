"""Phase E: hs-cli — a thin operator front-end over existing harness scripts.

Each verb wraps a script that already carries the logic (verify_install, preflight,
migrate_decomposition, component_config, install). The CLI adds no new behaviour, so
the tests assert it dispatches correctly and propagates exit codes — not that it
re-implements anything.
"""
import json
import pytest
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hs_cli  # noqa: E402


def test_version_prints_release_fields(capsys):
    rel = json.loads((ROOT / "harness/release.json").read_text())
    assert hs_cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert rel["harness_version"] in out
    assert rel["kit_digest"][:8] in out


def test_migrate_check_proxies_engine_exit():
    # the real tree is COLLAPSED (themed siblings folded back into the spine), so the
    # applicable direction is the reverse check: it reports 0 dangling themed-form refs
    # → exit 0. (Forward --check would now flag every collapsed hs:<skill> ref, since
    # post-collapse hs:<skill> is the correct form everywhere.) The point of the test is
    # unchanged: the CLI proxies the engine's exit code on a fully-migrated tree.
    assert hs_cli.main(["migrate", "--check", "--reverse"]) == 0


def test_list_shows_plugins_and_skills(capsys):
    # Post-collapse there is ONE plugin -- the spine `hs` -- carrying every skill. The
    # list still has to surface the plugin and its skills, spanning what used to be
    # separate groups: a former-flow skill (loop) and a spine skill (plan) both appear
    # under the single `hs` row, marked [spine].
    assert hs_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "hs" in out and "spine" in out  # the single plugin, marked spine
    assert "loop" in out          # a former-flow skill, now under hs
    assert "plan" in out          # a spine skill, also under hs


def _components_target(tmp):
    """A target tree carrying the real components.yaml + skill-deps + the viz
    group's skill dirs AND their dep closure (so a round-trip enable, which auto-
    ticks deps, finds those deps already installed — as a real install would)."""
    import shutil
    import skill_deps
    base = tmp / "target"
    (base / "harness" / "data").mkdir(parents=True)
    shutil.copy(ROOT / "harness/data/components.yaml",
                base / "harness/data/components.yaml")
    shutil.copy(ROOT / "harness/data/skill-deps.yaml",
                base / "harness/data/skill-deps.yaml")
    viz = ["excalidraw", "mermaidjs", "graphify", "preview", "tech-graph", "drawio"]
    closure = skill_deps.resolve(viz, ROOT / "harness/data/skill-deps.yaml")
    skills = base / "harness/plugins/hs/skills"
    for s in sorted(closure):
        (skills / s).mkdir(parents=True)
        (skills / s / "SKILL.md").write_text("---\nname: hs:%s\n---\n# %s\n" % (s, s))
    return base


def test_components_label_group_disable_omits_its_skills(tmp_path):
    # Post-collapse a former plugin group (viz) is a SKILL LABEL: disabling it must
    # OMIT its skill dirs (dir-omit), not flip a dead enabledPlugins key.
    base = _components_target(tmp_path)
    assert hs_cli.main(["components", "--disable", "viz", "--root", str(base)]) == 0
    assert (base / "harness/plugins/hs/disabled-skills/excalidraw").is_dir()
    assert not (base / "harness/plugins/hs/skills/excalidraw").exists()
    omit = json.loads(
        (base / "harness/state/install-omitted-skills.json").read_text())
    assert "excalidraw" in omit["omitted"] and "mermaidjs" in omit["omitted"]


def test_components_label_group_round_trips(tmp_path):
    base = _components_target(tmp_path)
    hs_cli.main(["components", "--disable", "viz", "--root", str(base)])
    assert hs_cli.main(["components", "--enable", "viz", "--root", str(base)]) == 0
    assert (base / "harness/plugins/hs/skills/excalidraw/SKILL.md").is_file()
    omit = json.loads(
        (base / "harness/state/install-omitted-skills.json").read_text())
    assert "excalidraw" not in omit["omitted"]


def test_components_hook_component_uses_policy_not_skills(tmp_path):
    # a hook-bearing component (rbac) still rides the policy/hook-flag path
    base = _components_target(tmp_path)
    pol = tmp_path / "policy.yaml"
    pol.write_text("components: {}\n")
    rc = hs_cli.main([
        "components", "--disable", "rbac", "--root", str(base),
        "--policy-file", str(pol), "--settings-file", str(tmp_path / "s.json"),
        "--hooks-file", str(tmp_path / "h.yaml"),
        "--state-file", str(tmp_path / "st.json")])
    assert rc == 0
    assert (yaml.safe_load(pol.read_text()) or {}).get("components", {}).get("rbac") is False
    # no skills were touched by a hook-component toggle
    assert not (base / "harness/plugins/hs/disabled-skills").exists()


def test_components_show_honors_override_paths(tmp_path, capsys):
    # 'show' (no enable/disable) must honor --hooks-file/--state-file: point it at
    # a hooks file that disables a flow hook for an OFF component so the resolved
    # drift/state reflects the override rather than the live tree.
    pol = tmp_path / "policy.yaml"
    pol.write_text("components:\n  flow: false\n")
    hooks = tmp_path / "hooks.yaml"
    hooks.write_text("hooks: {}\n")
    state = tmp_path / "state.json"
    rc = hs_cli.main([
        "components",
        "--policy-file", str(pol), "--hooks-file", str(hooks),
        "--state-file", str(state),
    ])
    out = capsys.readouterr().out
    data = json.loads(out)
    # flow is disabled in the override policy -> reflected in resolved components
    assert data["components"].get("flow") is False
    # the override hooks file (empty) drives drift detection, not the live tree
    assert isinstance(data.get("drift"), list)
    # nonzero rc only signals drift; with an empty override hooks map the OFF
    # flow component's hooks are "not disabled" -> drift may be reported there.
    assert rc in (0, 1)


def test_components_accepts_plugin_prefixed_group_name(tmp_path):
    # the legacy `hs-<group>` form de-prefixes to the bare label, same as `viz`
    base = _components_target(tmp_path)
    assert hs_cli.main(["components", "--disable", "hs-viz", "--root", str(base)]) == 0
    assert not (base / "harness/plugins/hs/skills/excalidraw").exists()


def test_unknown_verb_is_nonzero():
    proc = subprocess.run([sys.executable, str(SCRIPTS / "hs_cli.py"), "bogus"],
                          capture_output=True, text=True)
    assert proc.returncode != 0


@pytest.mark.dev_repo
def test_doctor_runs_clean_on_this_repo():
    # verify_install --strict + preflight on the live (consistent) tree → 0
    assert hs_cli.main(["doctor"]) == 0


def test_gates_reports_posture(capsys):
    # read-only posture report: exit 0, names the hard stages + artifacts + the
    # opt-in security-scan line (robust to whether a posture override is active)
    assert hs_cli.main(["gates"]) == 0
    out = capsys.readouterr().out
    assert "push" in out and "HARD" in out
    assert "verification" in out
    assert "security-scan gate" in out


def test_guards_reports_posture(capsys):
    assert hs_cli.main(["guards"]) == 0
    out = capsys.readouterr().out
    assert "preset" in out
    assert "protected branches" in out


def test_gates_handles_unloadable_policy(monkeypatch, capsys):
    # a malformed/unloadable stage-policy must report, not crash
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    import artifact_check
    monkeypatch.setattr(artifact_check, "load_policy",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad yaml")))
    rc = hs_cli.main(["gates"])
    assert rc == 1
    _c = capsys.readouterr()
    assert "could not load" in (_c.out + _c.err)


def test_guards_handles_unloadable_policy(monkeypatch, capsys):
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    import guard_policy
    monkeypatch.setattr(guard_policy, "load_guard_policy",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    rc = hs_cli.main(["guards"])
    assert rc == 0  # guards degrades gracefully (prints what it can)
    _c = capsys.readouterr()
    assert "could not load" in (_c.out + _c.err)
