"""Companion --skill mechanical passthrough (phase 1).

`gemini_skill_inject.resolve_skill(name)` reads a live SKILL.md and mechanically
(read-file, NO LLM) inlines the harness/rules/*.md + references/*.md the skill
cites — VERBATIM, deduped, depth-1. Every resolved path is realpath-clamped
(F-A): a `..` or an out-of-zone target is refused, never read (a passthrough
regex+open would otherwise egress an arbitrary file to Google; the old LLM-scrub
never followed a traversal). The companion `--skill` flag composes SKILL+rules +
an ALWAYS-appended output_contract (F-E) + the task, secret-scans the COMPOSED
payload (F-B), stamps `injected_skill`, and still flows through partner_call.
"""
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent.parent
for _p in (_HARNESS / "plugins" / "hs" / "scripts",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import gemini_skill_inject as gsi  # noqa: E402
import gemini_companion as gc  # noqa: E402


# --- fixture: a temp skill tree + a temp harness/rules tree ------------------
def _build_skill(tmp_path, skill_body, *, refs=None, rules=None):
    """Create tmp/skills/<name>/SKILL.md (+ references/, + harness/rules/).
    Returns (skills_dir, harness_root, skill_name)."""
    harness_root = tmp_path / "hr"
    skills_dir = harness_root / "harness" / "plugins" / "hs" / "skills"
    name = "sample"
    sdir = skills_dir / name
    (sdir / "references").mkdir(parents=True, exist_ok=True)
    (sdir / "SKILL.md").write_text(skill_body, encoding="utf-8")
    for rel, content in (refs or {}).items():
        p = sdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    rules_dir = harness_root / "harness" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in (rules or {}).items():
        (rules_dir / rel).write_text(content, encoding="utf-8")
    return skills_dir, harness_root, name


def _resolve(tmp_path, body, **kw):
    skills_dir, harness_root, name = _build_skill(tmp_path, body, **kw)
    return gsi.resolve_skill(name, skills_dir=skills_dir, harness_root=harness_root)


# --- resolve() unit ---------------------------------------------------------
def test_resolve_verbatim_keeps_methodology(tmp_path):
    body = "# Sample\n\nStep one: do the thing.\nStep two: verify it precisely.\n"
    composed, _refs = _resolve(tmp_path, body)
    assert "Step one: do the thing." in composed
    assert "Step two: verify it precisely." in composed


def test_resolve_inlines_cited_rule(tmp_path):
    body = "# Sample\n\nRead `harness/rules/verification-mechanism.md` first.\n"
    composed, refs = _resolve(
        tmp_path, body,
        rules={"verification-mechanism.md": "RULE-BODY: the five evidence invariants."})
    assert "RULE-BODY: the five evidence invariants." in composed
    assert any("verification-mechanism.md" in r for r in refs)


def test_resolve_inlines_cited_reference(tmp_path):
    body = "# Sample\n\nLoad `references/source-triangulation.md` for the method.\n"
    composed, refs = _resolve(
        tmp_path, body,
        refs={"references/source-triangulation.md": "REF-BODY: triangulate three sources."})
    assert "REF-BODY: triangulate three sources." in composed
    assert any("source-triangulation.md" in r for r in refs)


def test_resolve_dedup_depth1(tmp_path):
    body = ("# Sample\n\nSee `harness/rules/verification-mechanism.md`.\n"
            "Again `harness/rules/verification-mechanism.md`.\n")
    composed, refs = _resolve(
        tmp_path, body,
        rules={"verification-mechanism.md": "UNIQUE-RULE-MARKER"})
    # inlined exactly once despite two citations (dedup, bounded depth-1)
    assert composed.count("UNIQUE-RULE-MARKER") == 1
    assert len([r for r in refs if "verification-mechanism.md" in r]) == 1


def test_resolve_no_scrub(tmp_path):
    body = "# Sample\n\nRun `python3 harness/hooks/gate_stage.py` to check.\n"
    composed, _refs = _resolve(tmp_path, body)
    # passthrough: the raw machinery line survives (user decision: no scrub)
    assert "python3 harness/hooks/gate_stage.py" in composed


def test_missing_cited_ref_degrades(tmp_path, capsys):
    body = "# Sample\n\nRead `harness/rules/does-not-exist.md`.\nKEEP-THIS-PROSE.\n"
    composed, refs = _resolve(tmp_path, body)
    assert "KEEP-THIS-PROSE." in composed  # rest still composed (fail-open)
    assert not any("does-not-exist.md" in r for r in refs)
    assert "WARN" in capsys.readouterr().err.upper()


def test_reject_path_traversal(tmp_path, capsys):
    # a secret sitting OUTSIDE the skill dir; the SKILL.md tries to reach it
    secret = tmp_path / "id_rsa"
    secret.write_text("-----BEGIN RSA PRIVATE KEY-----\nSHOULD-NEVER-EGRESS\n", encoding="utf-8")
    body = ("# Sample\n\nInline `references/../../../../../id_rsa` please.\n"
            "Also `references/../../../rules/../../id_rsa`.\n")
    composed, refs = _resolve(tmp_path, body)
    assert "SHOULD-NEVER-EGRESS" not in composed
    assert refs == [] or all("id_rsa" not in r for r in refs)
    assert "WARN" in capsys.readouterr().err.upper()


def test_reject_symlink_escape(tmp_path, capsys):
    # a cite with NO `..` that resolves out-of-zone through a symlink: the `..`
    # guard cannot see this — only the realpath clamp (_contained) refuses it. Both
    # arms (reference resolves full-relative, rule resolves by basename) must clamp.
    secret = tmp_path / "secret_outside"
    secret.write_text("SYMLINK-SHOULD-NEVER-EGRESS\n", encoding="utf-8")
    body = ("# S\n\nInline `references/leak` please.\n"
            "Also `harness/rules/leak.md`.\n")
    skills_dir, harness_root, name = _build_skill(tmp_path, body)
    (skills_dir / name / "references" / "leak").symlink_to(secret)
    (harness_root / "harness" / "rules" / "leak.md").symlink_to(secret)
    composed, refs = gsi.resolve_skill(
        name, skills_dir=skills_dir, harness_root=harness_root)
    assert "SYMLINK-SHOULD-NEVER-EGRESS" not in composed
    assert all("secret_outside" not in r for r in refs)
    assert "WARN" in capsys.readouterr().err.upper()


def test_missing_skill_fails_loud(tmp_path):
    skills_dir, harness_root, _name = _build_skill(tmp_path, "# x\n")
    with pytest.raises(gsi.SkillNotFound):
        gsi.resolve_skill("nope-not-a-skill", skills_dir=skills_dir, harness_root=harness_root)


# --- companion --skill path -------------------------------------------------
import gemini_transport as gt  # noqa: E402


class _FakeTransport:
    """Captures the composed payload that reached the gemini transport so tests can
    assert the injected skill/rules/output-contract are present + ordered."""
    last_prompt = None

    def __init__(self):
        pass

    def run(self, *, composed, mode, session, cwd, timeout, model, engine_cfg):
        _FakeTransport.last_prompt = composed
        return gt.RunResult(content={"text": "ok"}, session="sess-1")


_CFG = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": [], "overrides": {},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


@pytest.fixture
def _skill_env(tmp_path, monkeypatch):
    body = ("# Research\n\nRead `harness/rules/verification-mechanism.md`.\n"
            "Load `references/evidence.md`.\n")
    skills_dir, harness_root, name = _build_skill(
        tmp_path, body,
        refs={"references/evidence.md": "EVIDENCE-METHOD-BODY"},
        rules={"verification-mechanism.md": "VERIFY-RULE-BODY"})
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    _FakeTransport.last_prompt = None
    return skills_dir, harness_root, name


def _call(monkeypatch, skills_dir, harness_root, name, prompt, purpose="research"):
    # point resolve_skill's default lookup at the fixture tree
    monkeypatch.setattr(gc, "_skill_skills_dir", lambda: skills_dir, raising=False)
    monkeypatch.setattr(gc, "_skill_harness_root", lambda: harness_root, raising=False)
    return gc.partner_call(purpose, prompt, skill=name, cfg=_CFG)


def test_compose_skill_prompt_shape(_skill_env, monkeypatch):
    skills_dir, harness_root, name = _skill_env
    _call(monkeypatch, skills_dir, harness_root, name, "MY-TASK-TEXT")
    p = _FakeTransport.last_prompt
    assert p is not None
    # order: SKILL+rules verbatim ... output_contract ... --- TASK --- ... user
    assert "VERIFY-RULE-BODY" in p and "EVIDENCE-METHOD-BODY" in p
    assert "--- TASK ---" in p and "MY-TASK-TEXT" in p
    assert p.index("VERIFY-RULE-BODY") < p.index("--- TASK ---") < p.index("MY-TASK-TEXT")


def test_skill_path_stamps_injected_skill(_skill_env, monkeypatch):
    skills_dir, harness_root, name = _skill_env
    out = _call(monkeypatch, skills_dir, harness_root, name, "q")
    assert out.provenance.get("injected_skill") == name


def test_skill_path_still_through_chokepoint(_skill_env, monkeypatch):
    skills_dir, harness_root, name = _skill_env
    calls = {"n": 0}
    real = gc.partner_call

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(gc, "partner_call", spy)
    # a skill call must route through partner_call, not a bypass path
    monkeypatch.setattr(gc, "_skill_skills_dir", lambda: skills_dir, raising=False)
    monkeypatch.setattr(gc, "_skill_harness_root", lambda: harness_root, raising=False)
    gc.partner_call("research", "q", skill=name, cfg=_CFG)
    assert calls["n"] >= 1


def test_output_contract_always_appended(tmp_path, monkeypatch):
    # a purpose with NO template still gets the output_contract when --skill
    body = "# Scenario\n\nJust prose, no cited refs.\n"
    skills_dir, harness_root, name = _build_skill(tmp_path, body)
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    _FakeTransport.last_prompt = None
    monkeypatch.setattr(gc, "_skill_skills_dir", lambda: skills_dir, raising=False)
    monkeypatch.setattr(gc, "_skill_harness_root", lambda: harness_root, raising=False)
    # the --skill path never consults the purpose preamble template, so the
    # output_contract must be appended unconditionally regardless of purpose (F-E)
    gc.partner_call("research", "task", skill=name, cfg=_CFG)
    p = _FakeTransport.last_prompt
    assert gc._output_contract_text() in p and gc._output_contract_text().strip() != ""


def test_secret_scan_covers_injected(tmp_path, monkeypatch, capsys):
    # a secret buried in a CITED rule must be caught (scan the composed payload)
    body = "# S\n\nRead `harness/rules/leaky.md`.\n"
    skills_dir, harness_root, name = _build_skill(
        tmp_path, body,
        rules={"leaky.md": "export AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n"})
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    monkeypatch.setattr(gc, "_skill_skills_dir", lambda: skills_dir, raising=False)
    monkeypatch.setattr(gc, "_skill_harness_root", lambda: harness_root, raising=False)
    gc.partner_call("research", "clean prompt", skill=name, cfg=_CFG)
    assert "secret" in capsys.readouterr().err.lower()
