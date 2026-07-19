"""test_check_skill_structure.py — structural lint for hs:* skills.

check_skill_structure is an advisory analyzer over a skill directory. It enforces
the thin-core discipline documented for the harness: a SKILL.md stays small and its
references/ stay bounded, so detail lives one level deep instead of bloating the
always-loaded core.

Contract mirrored from check_report_language: advisory by default (exit 0, never
mutates); with --strict a HARD finding exits non-zero. Description-shape problems are
advisory only — they flag but never block.
"""
import json
import pytest
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_skill_structure as css  # noqa: E402


def _make_skill(root, name="hs:demo", desc="Do a demo thing. Use when you need a demo.",
                body_lines=10, refs=None, body=None):
    d = root / name.replace("hs:", "")
    d.mkdir(parents=True, exist_ok=True)
    fm = (
        "---\nname: %s\ndescription: %s\nuser-invocable: true\n"
        "metadata:\n  owner: harness\n  compliance-tier: workflow\n---\n" % (name, desc)
    )
    body_text = body if body is not None else "\n".join("body line %d" % i for i in range(body_lines))
    (d / "SKILL.md").write_text(fm + "# Title\n" + body_text + "\n", encoding="utf-8")
    if refs:
        rd = d / "references"
        rd.mkdir(exist_ok=True)
        for rname, rlines in refs.items():
            (rd / rname).write_text(
                "\n".join("ref line %d" % i for i in range(rlines)) + "\n", encoding="utf-8")
    return d


def _rules(result):
    return {f["rule"] for f in result["findings"]}


def _severity(result, rule):
    return next(f["severity"] for f in result["findings"] if f["rule"] == rule)


# Build text of an EXACT char length with every line <= 80 chars, so a size test never
# accidentally also trips the per-line cap. Each "a"*80 + "\n" contributes 81 chars.
def _text_of_chars(n):
    full = n // 81
    rem = n - full * 81
    return (("a" * 80 + "\n") * full + "a" * rem)[:n]


def _skill_with_body_chars(d, n):
    """Write a SKILL.md whose BODY is exactly n chars (short lines). Frontmatter is
    fixed and excluded from the body budget so this pins the char boundary alone."""
    d.mkdir(parents=True, exist_ok=True)
    fm = "---\nname: hs:demo\ndescription: Do a demo thing. Use when you need a demo.\n---\n"
    (d / "SKILL.md").write_text(fm + _text_of_chars(n), encoding="utf-8")
    return d


# --- well-formed skill --------------------------------------------------------

def test_wellformed_skill_passes(tmp_path):
    d = _make_skill(tmp_path)
    res = css.check_skill(str(d))
    assert res["verdict"] == "PASS"
    assert res["findings"] == []


# --- hard: char-count size gates ----------------------------------------------
# Size is measured in CHARS, not lines: line-count rewards narrow wrapping and lets a
# wide-wrapped file smuggle 2-3x the content past a line cap. Char-count == the real
# token/context cost, independent of how the prose is wrapped.

def test_oversized_skill_md_is_hard(tmp_path):
    # A many-lined body whose TOTAL chars exceed the budget (each line short, so this
    # trips size alone, not the per-line cap).
    d = _make_skill(tmp_path, body="\n".join("filler line %d" % i for i in range(1400)))
    res = css.check_skill(str(d))
    assert "skill-md-too-large" in _rules(res)
    assert _severity(res, "skill-md-too-large") == "hard"
    assert res["verdict"] == "FAIL"


def test_body_at_cap_allowed(tmp_path):
    # A body of exactly MAX_SKILL_CHARS sits at the cap and must PASS.
    d = _skill_with_body_chars(tmp_path / "demo", css.MAX_SKILL_CHARS)
    res = css.check_skill(str(d))
    assert "skill-md-too-large" not in _rules(res)


def test_body_just_over_cap_fails(tmp_path):
    # One char over the cap must FAIL. This pins the exact boundary tight against
    # test_body_at_cap_allowed so a future drift of the comparison operator cannot slip
    # an over-budget body through.
    d = _skill_with_body_chars(tmp_path / "demo", css.MAX_SKILL_CHARS + 1)
    res = css.check_skill(str(d))
    assert "skill-md-too-large" in _rules(res)
    assert _severity(res, "skill-md-too-large") == "hard"


def test_frontmatter_excluded_from_size_limit(tmp_path):
    # The size gate governs GUIDANCE (body), not metadata. A large frontmatter plus a
    # body under the budget must NOT trip skill-md-too-large — otherwise the schema
    # frontmatter rollout would spuriously break near-budget skills.
    d = tmp_path / "demo"
    d.mkdir()
    fm = (
        "---\nname: hs:demo\ndescription: Do a thing. Use when needed.\n"
        "category: core\nlicense: AGPL-3.0\nkeywords: [a, b, c]\n"
        + "filler_field_%d: some padding value here\n" % 0
        + "".join("pad_%d: %s\n" % (i, "x" * 60) for i in range(200))  # fat frontmatter
        + "user-invocable: true\nmetadata:\n  owner: harness\n  compliance-tier: workflow\n---\n"
    )
    body = _text_of_chars(2000)  # small body, well under the char budget
    (d / "SKILL.md").write_text(fm + body, encoding="utf-8")
    res = css.check_skill(str(d))
    assert "skill-md-too-large" not in _rules(res)


def test_oversized_reference_is_hard(tmp_path):
    d = _make_skill(tmp_path)
    rd = d / "references"
    rd.mkdir(exist_ok=True)
    (rd / "big.md").write_text(_text_of_chars(css.MAX_REF_CHARS + 500), encoding="utf-8")
    res = css.check_skill(str(d))
    assert "reference-too-large" in _rules(res)
    assert _severity(res, "reference-too-large") == "hard"


def test_oversized_nested_reference_is_hard(tmp_path):
    # A reference in a subdirectory (references/<topic>/big.md) must be size-checked too —
    # a depth-1 glob let nested drawers escape the cap (a 29k file hid under references/x/).
    d = _make_skill(tmp_path)
    nested = d / "references" / "topic"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "big.md").write_text(_text_of_chars(css.MAX_REF_CHARS + 500), encoding="utf-8")
    res = css.check_skill(str(d))
    assert "reference-too-large" in _rules(res)
    assert _severity(res, "reference-too-large") == "hard"
    assert any("topic/big.md" in f["detail"] for f in res["findings"]
               if f["rule"] == "reference-too-large")


# --- advisory: description shape ----------------------------------------------

def test_description_without_trigger_is_advisory(tmp_path):
    d = _make_skill(tmp_path, desc="Does a thing with no trigger clause.")
    res = css.check_skill(str(d))
    assert "description-missing-trigger" in _rules(res)
    assert _severity(res, "description-missing-trigger") == "advisory"
    # advisory-only findings never fail the skill
    assert res["verdict"] == "PASS_WITH_RISK"


def test_short_description_is_advisory(tmp_path):
    d = _make_skill(tmp_path, desc="Too short")
    res = css.check_skill(str(d))
    assert "description-length" in _rules(res)
    assert _severity(res, "description-length") == "advisory"


# --- hard: dangling local reference -------------------------------------------

def test_dangling_ref_is_hard(tmp_path):
    d = _make_skill(tmp_path, body="See references/nope.md for the detail.")
    res = css.check_skill(str(d))
    assert "broken-reference-link" in _rules(res)
    assert _severity(res, "broken-reference-link") == "hard"
    assert res["verdict"] == "FAIL"


def test_existing_local_ref_not_flagged(tmp_path):
    # The body links a reference that DOES exist on disk => no dangling, no orphan.
    d = _make_skill(tmp_path, body="Detail lives in references/detail.md here.",
                    refs={"detail.md": 20})
    res = css.check_skill(str(d))
    assert "broken-reference-link" not in _rules(res)
    assert "orphan-reference" not in _rules(res)


def test_absolute_path_prose_not_flagged(tmp_path):
    # An absolute-path mention is preceded by `/`, which the negative-lookbehind skips.
    d = _make_skill(
        tmp_path,
        body="Detail lives in harness/plugins/hs/skills/demo/references/x.md (absolute).")
    res = css.check_skill(str(d))
    assert "broken-reference-link" not in _rules(res)


# --- hard: birth-marker leak (tightened) --------------------------------------

def test_birth_marker_in_body_is_hard(tmp_path):
    d = _make_skill(tmp_path, body="generated_on: 2026-06-17 by the drafting pipeline.")
    res = css.check_skill(str(d))
    assert "birth-marker-leak" in _rules(res)
    assert _severity(res, "birth-marker-leak") == "hard"
    assert res["verdict"] == "FAIL"


def test_birth_marker_in_frontmatter_not_flagged(tmp_path):
    # The same marker inside frontmatter metadata is NOT a prose leak.
    d = tmp_path / "fmdemo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: hs:fmdemo\ndescription: A demo skill. Use when demoing things.\n"
        "generated_on: 2026-06-17\n---\n# Title\nclean prose body\n",
        encoding="utf-8")
    res = css.check_skill(str(d))
    assert "birth-marker-leak" not in _rules(res)


def test_evidence_heading_not_birth_marker(tmp_path):
    d = _make_skill(tmp_path, body="## Evidence\n\nfile.py:42 proves the claim.")
    res = css.check_skill(str(d))
    assert "birth-marker-leak" not in _rules(res)


def test_doc_prose_not_birth_marker(tmp_path):
    # Belief-store documentation prose must NOT read as a machine provenance leak —
    # else the phases that document the belief store self-trip the gate this builds.
    d = _make_skill(
        tmp_path,
        body="The belief was reinforced from 3 episodes and the success rate held at 0.9.")
    res = css.check_skill(str(d))
    assert "birth-marker-leak" not in _rules(res)
    assert all(f["severity"] != "hard" for f in res["findings"])


# --- advisory: orphan reference -----------------------------------------------

def test_orphan_ref_advisory_not_hard(tmp_path):
    d = _make_skill(tmp_path, body="No links in this body.", refs={"unused.md": 5})
    res = css.check_skill(str(d))
    assert "orphan-reference" in _rules(res)
    assert _severity(res, "orphan-reference") == "advisory"
    assert res["verdict"] != "FAIL"  # advisory-only never fails


# --- write-gate decision (the skill_quality_gate hook brain) -------------------

def test_write_gate_blocks_on_dangling_ref(tmp_path):
    d = _make_skill(tmp_path, body="See references/nope.md.")
    reason = css.write_gate_reason(str(d / "SKILL.md"))
    assert reason is not None
    assert str(d / "SKILL.md") in reason  # actionable: names the path
    assert "broken-reference-link" in reason


def test_write_gate_failopen_on_shape_only(tmp_path):
    # A description-shape-only problem is advisory => the gate allows it (None).
    d = _make_skill(tmp_path, desc="Too short", body="clean body")
    assert css.write_gate_reason(str(d / "SKILL.md")) is None


def test_write_gate_inert_on_non_skill_md(tmp_path):
    other = tmp_path / "notes.md"
    other.write_text("references/nope.md\n", encoding="utf-8")
    assert css.write_gate_reason(str(other)) is None


# --- CLI contract -------------------------------------------------------------

def _run(args):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_skill_structure.py"), *args],
        capture_output=True, text=True,
    )


def test_cli_advisory_exits_zero_even_with_hard(tmp_path):
    # Without --strict the lint is advisory: a hard finding is reported, never blocks.
    d = _make_skill(tmp_path, body="See references/nope.md for the missing detail.")
    r = _run([str(d)])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["tool"] == "check_skill_structure"


def test_cli_strict_blocks_hard(tmp_path):
    d = _make_skill(tmp_path, body="See references/nope.md for the missing detail.")
    r = _run([str(d), "--strict"])
    assert r.returncode == 1


def test_cli_strict_clean_passes(tmp_path):
    d = _make_skill(tmp_path)
    r = _run([str(d), "--strict"])
    assert r.returncode == 0


def test_cli_missing_skill_is_inert(tmp_path):
    # A path with no SKILL.md never hard-fails the caller.
    r = _run([str(tmp_path / "nope"), "--strict"])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("skipped")


# --- hard: dangling ref is case-blind on the file part ------------------------

def test_dangling_ref_case_insensitive_on_file_part(tmp_path):
    # A reader clicking references/Detail.md gets the same 404 as references/detail.md,
    # so the broken-reference-link rule must catch a capitalized name or an uppercase
    # extension too — a case-sensitive lint silently passes a dead link.
    cap = _make_skill(tmp_path / "a", body="See references/Detail.md for the detail.")
    assert "broken-reference-link" in _rules(css.check_skill(str(cap)))
    ext = _make_skill(tmp_path / "b", body="See references/detail.MD for the detail.")
    assert "broken-reference-link" in _rules(css.check_skill(str(ext)))
    # and the write-time gate must block it, not just the lint
    assert css.write_gate_reason(cap / "SKILL.md") is not None


def test_existing_uppercase_ref_not_flagged(tmp_path):
    # The mirror: a capitalized ref that DOES resolve on disk is neither a dangling
    # link NOR an orphan. The orphan assertion makes this discriminate pre/post-fix:
    # pre-fix the old regex never matched `Detail.md`, so the on-disk file was an
    # unlinked orphan; post-fix the link is recognized, so the ref is fully clean.
    d = _make_skill(tmp_path, body="Detail lives in references/Detail.md here.",
                    refs={"Detail.md": 5})
    rules = _rules(css.check_skill(str(d)))
    assert "broken-reference-link" not in rules
    assert "orphan-reference" not in rules


# --- char-aware line cap + disabled-skills coverage + desc quality -------------

_REPO = Path(__file__).resolve().parents[2]
_SKILLS = _REPO / "harness/plugins/hs/skills"


def test_overlong_line_is_hard(tmp_path):
    # The per-line cap is a HARD readability/diff guard: one run-on line past the cap
    # fails, independent of the file's total size. It is not an anti-evasion tool (the
    # char size cap already bounds total content) — it keeps every line reviewable.
    d = _make_skill(tmp_path, body="x" * (css.MAX_LINE_CHARS + 100))
    res = css.check_skill(str(d))
    assert "overlong-line" in _rules(res)
    assert _severity(res, "overlong-line") == "hard"
    assert res["verdict"] == "FAIL"
    # a normal short-lined body does not trip it
    ok = _make_skill(tmp_path / "ok", body_lines=10)
    assert "overlong-line" not in _rules(css.check_skill(str(ok)))


def test_overlong_line_boundary(tmp_path):
    # A line of exactly MAX_LINE_CHARS passes; one char over fails. Pins the operator.
    at = _make_skill(tmp_path / "at", body="x" * css.MAX_LINE_CHARS)
    assert "overlong-line" not in _rules(css.check_skill(str(at)))
    over = _make_skill(tmp_path / "over", body="x" * (css.MAX_LINE_CHARS + 1))
    assert "overlong-line" in _rules(css.check_skill(str(over)))


def test_grandfathered_hard_downgraded_but_strict_bypasses(tmp_path, monkeypatch):
    # A grandfathered (component, rule) HARD finding is downgraded to advisory in the
    # default sweep view (marked grandfathered) so the standard lands without a mass
    # rewrite; --strict (honor_grandfather=False) keeps it HARD so CI re-teeths on edit.
    d = _make_skill(tmp_path, name="hs:gf", body="x" * (css.MAX_LINE_CHARS + 50))
    monkeypatch.setattr(css, "_GRANDFATHER", {("gf", "overlong-line")})
    soft = css.check_skill(str(d))  # default honor_grandfather=True
    assert _severity(soft, "overlong-line") == "advisory"
    assert next(f for f in soft["findings"] if f["rule"] == "overlong-line").get("grandfathered")
    assert soft["verdict"] != "FAIL"
    strict = css.check_skill(str(d), honor_grandfather=False)
    assert _severity(strict, "overlong-line") == "hard"
    assert strict["verdict"] == "FAIL"


def test_grandfather_does_not_touch_unlisted_skill(tmp_path, monkeypatch):
    # A skill NOT in the ledger keeps its HARD finding even in the default view.
    d = _make_skill(tmp_path, name="hs:other", body="x" * (css.MAX_LINE_CHARS + 50))
    monkeypatch.setattr(css, "_GRANDFATHER", {("gf", "overlong-line")})
    res = css.check_skill(str(d))
    assert _severity(res, "overlong-line") == "hard"
    assert res["verdict"] == "FAIL"


# --- agents: size + per-line coverage -----------------------------------------

def test_agent_oversized_is_hard(tmp_path):
    ad = tmp_path / "agents"
    ad.mkdir()
    (ad / "big.md").write_text(
        "---\nname: big\ndescription: An agent. Use when needed.\n---\n"
        + _text_of_chars(css.MAX_AGENT_CHARS + 500), encoding="utf-8")
    res = css.check_agent(str(ad / "big.md"))
    assert "agent-too-large" in _rules(res)
    assert _severity(res, "agent-too-large") == "hard"
    assert res["verdict"] == "FAIL"


def test_agent_overlong_line_is_hard(tmp_path):
    ad = tmp_path / "agents"
    ad.mkdir()
    (ad / "wide.md").write_text(
        "---\nname: wide\ndescription: An agent. Use when needed.\n---\n"
        "# Body\n" + "x" * (css.MAX_LINE_CHARS + 50) + "\n", encoding="utf-8")
    res = css.check_agent(str(ad / "wide.md"))
    assert "overlong-line" in _rules(res)
    assert _severity(res, "overlong-line") == "hard"


def test_agent_frontmatter_description_exempt_from_overlong_line(tmp_path):
    # An agent's `description:` is one YAML scalar (its routing text) that cannot be
    # prose-wrapped without a block scalar / semantic change, and the description is
    # model-visible for routing. The per-line readability cap is a BODY/prose concern,
    # measured on the body only — consistent with how SKILL.md overlong-line is measured.
    ad = tmp_path / "agents"
    ad.mkdir()
    long_desc = "Use this agent when " + "x" * (css.MAX_LINE_CHARS + 50)
    (ad / "wide_fm.md").write_text(
        "---\nname: wide_fm\ndescription: %s\n---\n# Body\nshort body line.\n" % long_desc,
        encoding="utf-8")
    res = css.check_agent(str(ad / "wide_fm.md"))
    assert "overlong-line" not in _rules(res)


def test_agents_swept_alongside_skills(tmp_path):
    # check_path over a plugin skills/ root also sweeps the sibling agents/ dir, so an
    # oversized agent reddens the same aggregate the skill sweep uses.
    live = tmp_path / "skills"
    _make_skill(live, name="hs:good")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "fat.md").write_text(
        "---\nname: fat\ndescription: An agent. Use when needed.\n---\n"
        + _text_of_chars(css.MAX_AGENT_CHARS + 500), encoding="utf-8")
    result = css.check_path(str(live))
    agent_names = {a["agent"] for a in result.get("agents", [])}
    assert "fat" in agent_names
    assert result["verdict"] == "FAIL"
    assert result["hard"] >= 1


def test_scans_disabled_skills_dir(tmp_path):
    # A skill parked in the stash (disabled-skills/) must not escape the structural
    # lint: scanning a plugin's skills/ root also pulls in its sibling disabled-skills/.
    live = tmp_path / "skills"
    _make_skill(live, name="hs:good")
    stash = tmp_path / "disabled-skills"
    off = stash / "bad"
    off.mkdir(parents=True)
    (off / "SKILL.md").write_text(
        "---\nname: hs:bad\ndescription: short\n---\n# Bad\nbody\n", encoding="utf-8")
    result = css.check_path(str(live))
    names = {s["skill"] for s in result["skills"]}
    assert "good" in names and "bad" in names
    bad = next(s for s in result["skills"] if s["skill"] == "bad")
    assert "description-length" in {f["rule"] for f in bad["findings"]}
