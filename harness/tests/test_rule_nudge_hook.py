#!/usr/bin/env python3
"""P5 — rule_nudge_hook: name the rules whose scope matches the file being written.

A PreToolUse (Write/Edit/MultiEdit) nudge. Folded onto the shared harness-hooks.yaml
nudge plane (shipped ON, dogfood default); the env var HARNESS_RULE_NUDGE stays as a
per-repo override that wins when set (truthy on / falsey off). When on it writes ONE
terse stderr line naming up to N (config nudge_max_rules, default 3) scope-matching
rules by id, once per file per session, and ALWAYS continues (fail-open).
"""

import sys
from pathlib import Path

import yaml as _yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = _REPO_ROOT / "harness" / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import rule_nudge_hook  # noqa: E402

_AREA = """id: STD-REVIEW-COMMON
type: std_area
zone: operational
title: "t"
rule_groups:
  - id: STD-REVIEW-COMMON-RG1
    title: "g"
    rules:
      - id: R-ALPHA
        title: "alpha rule"
        scope: ["**/*"]
        severity: info
        description: |
          SECRET-BODY-TOKEN: this long description must never reach the nudge.
      - id: R-BETA
        title: "beta python rule"
        scope: ["**/*.py"]
        severity: info
      - id: R-GAMMA
        title: "gamma rule"
        scope: ["**/*"]
        severity: info
      - id: R-DELTA
        title: "delta rule"
        scope: ["**/*"]
        severity: info
      - id: R-EPSILON
        title: "epsilon rule"
        scope: ["**/*"]
        severity: info
"""


def _repo(tmp_path):
    areas = tmp_path / "harness" / "standards" / "areas"
    areas.mkdir(parents=True, exist_ok=True)
    (areas / "STD-REVIEW-COMMON.std.yaml").write_text(_AREA, encoding="utf-8")
    return tmp_path


def _payload(tmp_path, rel, tool="Write", session="s1"):
    return {"tool_name": tool, "session_id": session,
            "tool_input": {"file_path": str(tmp_path / rel)}}


def _env_on(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_NUDGE", "1")
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)


# (a) env unset -> falls through to harness-hooks.yaml (shipped default now OFF:
# rule_nudge is one of the low-value nudges disabled by default).
def test_default_off_via_config(monkeypatch):
    monkeypatch.delenv("HARNESS_RULE_NUDGE", raising=False)
    assert rule_nudge_hook._enabled() is False


# (b) env override wins over the config plane, both directions.
def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_NUDGE", "1")
    assert rule_nudge_hook._enabled() is True
    monkeypatch.setenv("HARNESS_RULE_NUDGE", "0")
    assert rule_nudge_hook._enabled() is False  # env forces off even when config is ON


# (c) ON + a scope-matching write -> names the matching rule ids, not others.
# Uses a .ts code file: matches the broad **/* scope but not **/*.py, and (post
# noncode-filter) a code target still nudges where a .md/.json would not.
def test_nudges_matching_rules(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    msg = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.ts"), root=str(tmp_path))
    assert msg and "R-ALPHA" in msg                 # scope **/* matches a .ts
    assert "R-BETA" not in msg                       # scope **/*.py does NOT


# (d) touched-flag: a second write/edit of the same file in a session is silent.
def test_once_per_file_per_session(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    first = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.py"), root=str(tmp_path))
    second = rule_nudge_hook.core(
        _payload(tmp_path, "pkg/x.py", tool="Edit"), root=str(tmp_path))
    assert first is not None and second is None


# (e) cap: at most N rules named, never the rule body.
def test_caps_and_no_body(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    msg = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.py"), root=str(tmp_path))
    # 5 rules match a .py file; the cap (default 3) shows 3 + a "+N more" tail.
    named = [rid for rid in ("R-ALPHA", "R-BETA", "R-GAMMA", "R-DELTA", "R-EPSILON")
             if rid in msg]
    assert len(named) <= 3
    assert "more" in msg                              # the elided tail is summarized
    assert "SECRET-BODY-TOKEN" not in msg             # never dumps the description


# (f0) a write to an absolute path OUTSIDE the repo root does not nudge (the
# basename must not be matched against repo rule scopes).
def test_outside_root_is_skipped(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    data = {"tool_name": "Write", "session_id": "s1",
            "tool_input": {"file_path": "/etc/cron.d/payload.py"}}
    assert rule_nudge_hook.core(data, root=str(tmp_path)) is None


# (f1) a titleless rule (layer-b USR-* overrides carry no title) renders as the
# id alone — no dangling "— " separator, never the string "None".
def test_titleless_rule_shows_id_only(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    areas = tmp_path / "harness" / "standards" / "areas"
    areas.mkdir(parents=True, exist_ok=True)
    (areas / "STD-REVIEW-COMMON.std.yaml").write_text(
        "id: STD-REVIEW-COMMON\ntype: std_area\nzone: operational\ntitle: t\n"
        "rule_groups: []\n", encoding="utf-8")
    folder = tmp_path / "docs" / "standards"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "self.std.yaml").write_text(
        _yaml.safe_dump({"overrides": [
            {"rule_id": "USR-X-NOTITLE", "reason": "r", "scope": ["**/*.py"]}]}),
        encoding="utf-8")
    msg = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.py"), root=str(tmp_path))
    assert "USR-X-NOTITLE" in msg
    assert "None" not in msg and "— " not in msg


# (f) fail-open: a core that raises still continues (exit 0, no block).
def test_fail_open(tmp_path, monkeypatch, capsys):
    _env_on(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(rule_nudge_hook, "core", _boom)
    monkeypatch.setattr(rule_nudge_hook.sys, "stdin",
                        __import__("io").StringIO('{"tool_name":"Write"}'))
    rc = rule_nudge_hook.main([])
    assert rc == 0                                    # never exit 2 / block


# (g) registered as a nudge in the kit AND folded onto the harness-hooks.yaml
# nudge plane (shipped ON); the env var stays as a per-repo override.
def test_registered_as_nudge():
    # rule_nudge_hook is migrated into the in-process dispatcher: it fires as a core
    # of hook_dispatch.py (PreToolUse:Write|Edit|MultiEdit), not its own command. It is
    # registered as a nudge-class core in hook-dispatch.yaml.
    disp = _yaml.safe_load(
        (_REPO_ROOT / "harness" / "data" / "hook-dispatch.yaml").read_text())
    cores = [c for grp in disp["groups"].values() for c in grp
             if c.get("module") == "rule_nudge_hook"]
    assert cores and cores[0].get("class") == "nudge"
    # on the shared on/off plane; shipped default is OFF (low-value nudge diet).
    hh = _yaml.safe_load(
        (_REPO_ROOT / "harness" / "data" / "harness-hooks.yaml").read_text())
    assert (hh.get("hooks") or {}).get("rule_nudge_hook", {}).get("enabled") is False


# ---------------------------------------------------------------------------
# noncode-target filter: RG1 rules are CODE-review rules; a per-edit nudge has
# no business firing on prose/data/config. The filter lives in the HOOK (the
# noise is a nudge problem) — the standards' scope: ["**/*"] is user-owned and
# stays untouched, so a full review still applies the rule to those files.
# ---------------------------------------------------------------------------

# (h) a markdown write is silenced even though **/* matches it.
def test_no_advisory_for_markdown(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    assert rule_nudge_hook.core(
        _payload(tmp_path, "CLAUDE.md"), root=str(tmp_path)) is None


# (i) a JSON artifact under plans/ — caught by both ext and dir prefix.
def test_no_advisory_for_json_artifact(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    assert rule_nudge_hook.core(
        _payload(tmp_path, "plans/x/artifacts/verification.json"),
        root=str(tmp_path)) is None


# (j) a YAML policy under harness/data/ — ext .yaml AND data dir prefix.
def test_no_advisory_for_yaml_policy(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    assert rule_nudge_hook.core(
        _payload(tmp_path, "harness/data/guard-policy.yaml"),
        root=str(tmp_path)) is None


# (k) any file under docs/ is data/doc, never code.
def test_no_advisory_for_docs_dir(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    assert rule_nudge_hook.core(
        _payload(tmp_path, "docs/GLOSSARY.md"), root=str(tmp_path)) is None


# (l) regression of the primary function: a .py code edit still nudges.
def test_advisory_still_fires_for_py(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    msg = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.py"), root=str(tmp_path))
    assert msg and "R-ALPHA" in msg


# (m) a .ts code edit still nudges.
def test_advisory_fires_for_ts(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    msg = rule_nudge_hook.core(_payload(tmp_path, "pkg/x.ts"), root=str(tmp_path))
    assert msg and "R-ALPHA" in msg


# (n) a silenced noncode write leaves NO dedup marker (so it neither nudges nor
# leaks a flag for P1's GC to reclaim later).
def test_noncode_does_not_create_marker(tmp_path, monkeypatch):
    _env_on(tmp_path, monkeypatch)
    _repo(tmp_path)
    rel = "CLAUDE.md"
    assert rule_nudge_hook.core(
        _payload(tmp_path, rel), root=str(tmp_path)) is None
    assert not rule_nudge_hook._flag_path("s1", rel).exists()


# (o) the predicate itself: code extensions pass, noncode ext + data dirs fail.
def test_is_code_target_predicate():
    f = rule_nudge_hook._is_code_target
    assert f("pkg/x.py") and f("a/b/c.ts") and f("main.go") and f("run.sh")
    assert not f("CLAUDE.md") and not f("conf.yaml") and not f("d.json")
    assert not f("docs/x.py")          # data/doc dir wins over a code ext
    assert not f("harness/data/x.py")
    assert not f("plans/p/note.py")
