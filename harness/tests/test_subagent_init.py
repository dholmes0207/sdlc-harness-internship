"""test_subagent_init.py — SubagentStart context injector (telemetry, fail-open).

At subagent spawn the hook injects the FULL resolved register (the same block the
main session gets at SessionStart, built by the shared register_block) PLUS the
existing pointer at the harness rule layer + standards + ownership, PLUS a one-line
reinforcement that extends the voice scope-fence over ALL subagent output. Telemetry
posture: never blocks; any error degrades to the generic pointer, never no-spawn.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
for _p in (_HOOKS, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import subagent_init as si  # noqa: E402
import register_block       # noqa: E402


def _output_yaml(tmp_path, **doc):
    import yaml
    p = tmp_path / "output.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


# ----------------------------------------------------- existing pointer kept ---

def test_context_points_at_rules_and_standards():
    txt = si.context_text({"agent_type": "hs:developer", "agent_id": "abc"})
    assert "harness/rules" in txt
    assert "harness/standards" in txt


def test_context_mentions_ownership_discipline():
    txt = si.context_text({"agent_type": "claude"})
    assert "ownership" in txt.lower() or "scope" in txt.lower()


def test_context_fail_open_on_empty_payload():
    assert isinstance(si.context_text({}), str)
    assert isinstance(si.context_text(None), str)


# ------------------------------------------------------- full register inject ---

def test_full_register_injected(tmp_path, monkeypatch):
    """Diet C: a subagent gets the register essence + a MANDATORY read-pointer to the
    profile (not the dumped body): with code_style=3 the essence line + the read
    directive ride along, plus the voice axis line and the kept ownership pointer."""
    out = _output_yaml(tmp_path, language="vi", code_style=3)
    monkeypatch.setenv("HARNESS_OUTPUT", str(out))
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "researcher"})
    assert "code_style=3/5" in txt, "code_style essence missing\n%s" % txt
    assert "output-styles/code-style-level-3.md" in txt, "read-pointer missing\n%s" % txt
    assert "MANDATORY CODE DIRECTIVES" not in txt, "profile body leaked into register"
    assert "voice_level=" in txt
    assert "ownership" in txt.lower()


def test_scopefence_clause_present(tmp_path, monkeypatch):
    """The verbatim scope-fence clause rides into the subagent. [A3/A4]"""
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "red-teamer"})
    assert "change NOTHING in code, generated docs/reports" in txt, (
        "scope-fence clause missing\n%s" % txt)


def test_voice9_eq_voice5_scopefence_parity():
    """Scope-fence invariant on the subagent path: the scope-fence + artifact-voice clause
    is byte-identical at voice_level 9 and 5 (the knob does not touch the fence). [A4]"""
    def _prefs(vl):
        return {"voice_level": vl, "terminal_voice_level": 3, "persona": "none",
                "no_markdown": False, "interview_rigor": "standard",
                "action_prompting": "standard", "audience": None,
                "code_style": None, "humanize": None}

    def _fence(txt):
        return [ln for ln in txt.splitlines() if ln.startswith("Scope-fence:")]

    r9 = register_block.build_register(_prefs(9))
    r5 = register_block.build_register(_prefs(5))
    assert _fence(r9) == _fence(r5) and _fence(r9), "scope-fence drifted across voice_level"


def test_register_import_fail_fallbacks(monkeypatch):
    """register_block import/build failure → the generic pointer, no raise, no
    no-context (fail-open). [fail-open]"""
    import builtins
    real_import = builtins.__import__

    def _block_register(name, *a, **k):
        if name == "register_block":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block_register)
    txt = si.context_text({"agent_type": "claude"})
    assert isinstance(txt, str) and "harness/rules" in txt
    assert "Lead with trade-offs" not in txt  # body absent on fallback


def test_subagent_scope_reinforcement_present(tmp_path, monkeypatch):
    """The mandatory F3 reinforcement line extends the fence over ALL subagent
    output — reasoning, reports, and messages back to the lead. [F3 user-decision]"""
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "researcher"})
    assert "apply to ALL your output" in txt, "reinforcement line missing\n%s" % txt
    assert "messages back to the lead" in txt


def test_rbac_scope_present_full_path(tmp_path, monkeypatch):
    """The RBAC write-scope reminder rides the full-register path so every subagent —
    including default agents that carry no authored .md — learns to resolve its lanes
    via check_permission before writing."""
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "general-purpose"})
    assert "check_permission.py --name" in txt, "RBAC scope missing on full path\n%s" % txt
    assert "BLOCKED by agent_rbac_guard" in txt
    assert "widen the floor yourself" in txt
    assert "deny-list model" in txt


def test_rbac_scope_present_on_fallback(monkeypatch):
    """The RBAC reminder also rides the degraded fail-open path (register build fails)
    so a fallback spawn is not left without a lane reminder."""
    import builtins
    real_import = builtins.__import__

    def _block_register(name, *a, **k):
        if name == "register_block":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block_register)
    txt = si.context_text({"agent_type": "claude"})
    assert "check_permission.py --name" in txt, "RBAC scope missing on fallback\n%s" % txt
    assert "BLOCKED by agent_rbac_guard" in txt


def test_standards_directive_present_full_path(tmp_path, monkeypatch):
    """A write-class delegated task is told to READ the standard before writing — the
    directive is a read-order, not the file body — and it rides the full-register path."""
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "developer"})
    assert "docs/code-standards.md" in txt, "standards directive missing on full path\n%s" % txt
    assert "you MUST read" in txt
    assert "docs/system-architecture.md" in txt


def test_standards_directive_present_on_fallback(monkeypatch):
    """The read directive also rides the degraded fail-open path (register build fails)
    so a fallback spawn still carries the read-the-standard order."""
    import builtins
    real_import = builtins.__import__

    def _block_register(name, *a, **k):
        if name == "register_block":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block_register)
    txt = si.context_text({"agent_type": "claude"})
    assert "docs/code-standards.md" in txt, "standards directive missing on fallback\n%s" % txt
    assert "you MUST read" in txt


def test_subagent_context_within_cap(tmp_path, monkeypatch):
    """Tripwire: the injected additionalContext stays under 9000c — well below the
    SubagentStart 10000c silent-truncation cap — so register + directive cannot bloat
    past the limit unnoticed."""
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    txt = si.context_text({"agent_type": "researcher"})
    assert len(txt) <= 9000, "context_text grew to %d chars (cap 9000)" % len(txt)


def test_subprocess_stdout_is_valid_json(tmp_path):
    """The real stdin→stdout contract: a JSON payload in, a single valid JSON
    object out (hookSpecificOutput.additionalContext), exit 0."""
    import json
    import os
    import subprocess
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "subagent_init.py")],
        input=json.dumps({"agent_type": "researcher"}),
        capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    hs = payload.get("hookSpecificOutput") or {}
    assert hs.get("hookEventName") == "SubagentStart"
    assert "harness/rules" in hs.get("additionalContext", "")


def test_subagent_surface_excludes_persona_bundle_pii(tmp_path, monkeypatch):
    """Security: a bundle active + a PII file present must STILL leave the subagent
    surface free of NAME/CHARACTERISTIC/SOUL/RELATIONSHIP — build_register emits only
    the FORM, and subagent_init injects nothing else. The FORM is allowed."""
    import json as _json
    import yaml
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "b", "name": "NameLeakZZ", "characteristic": "CharLeakZZ",
        "soul": "SoulLeakZZ", "form": "military", "default_voice_level": 7}]},
        allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: b\npersona: none\nvoice_level: 7\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    me = tmp_path / "persona-me.json"
    me.write_text(_json.dumps({"name": "RelLeakZZ"}), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_ME", str(me))
    monkeypatch.setenv("HARNESS_OUTPUT", str(_output_yaml(tmp_path, language="vi")))
    txt = si.context_text({"agent_type": "tester"})
    for leak in ("NameLeakZZ", "CharLeakZZ", "SoulLeakZZ", "RelLeakZZ"):
        assert leak not in txt, "%s leaked into the subagent surface" % leak
    assert "persona=military" in txt  # only the FORM crosses to the subagent surface
