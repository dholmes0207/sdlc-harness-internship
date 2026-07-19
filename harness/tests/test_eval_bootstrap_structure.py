"""Structure + guardrail contract for the eval-bootstrap SKILL.md and references.

Pins the thin-core shape (valid frontmatter, workflow tier, non-injectable),
that the load-bearing directives are actually present (strategy-approval gate,
ask-then-pip, self-target fence, no-OCR), that every referenced drawer exists,
and that the skill dir stays brand/OCR-clean.
"""

import re
import subprocess
import sys

from pathlib import Path

from eval_bootstrap_denylist import BRAND_RE as _BRAND, OCR_RE as _OCR

_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap"
_SKILL_MD = _SKILL_DIR / "SKILL.md"
_REFS = _SKILL_DIR / "references"


def _frontmatter(md_path):
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    import yaml
    return yaml.safe_load(text.split("---\n", 2)[1])


def _body(md_path):
    return md_path.read_text(encoding="utf-8").split("---\n", 2)[2]


def test_frontmatter_valid_and_tagged():
    fm = _frontmatter(_SKILL_MD)
    assert fm["name"] == "hs:eval-bootstrap"
    assert fm["injectable"] is False
    assert fm["metadata"]["compliance-tier"] == "workflow"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert len(fm["description"]) <= 512


def test_description_has_trigger():
    fm = _frontmatter(_SKILL_MD)
    desc = fm["description"].lower()
    assert "use when" in desc or "use to" in desc
    assert "eval" in desc


def test_body_carries_loadbearing_directives():
    body = _body(_SKILL_MD).lower()
    # strategy-approval gate (R7) surfaced as an AskUserQuestion
    assert "askuserquestion" in body
    assert "strategy" in body
    # ask-then-pip (Q4 / VL-5): declare requirements + ask before install
    assert "requirements.txt" in body
    assert "pip" in body
    # self-target fence (VL-8)
    assert "orchestrator/critic/score.py" in body or "self-target" in body
    assert "harness/" in body
    # no-OCR data policy present (images are model-read, never machine-OCR'd)
    assert "model-read" in body or "model read" in body
    assert "pytesseract" not in body  # must NOT name/recommend an OCR library
    # backing to the scaffolder + the 4 wave-1 scripts (card/sandbox/memory/mutation)
    assert "eval_scaffold.py" in body
    assert "eval_config.py" in body
    assert "sandbox_run.py" in body
    assert "eval_memory.py" in body
    assert "mutation_matrix.py" in body
    # R9 sandbox evidence + the mutation matrix that meta-tests the P0 gate
    assert "evidence" in body
    assert "mutation" in body


def test_support_matrix_routes():
    fm = _frontmatter(_SKILL_MD)
    assert "python only (v1)" not in fm["description"].lower()
    body = _body(_SKILL_MD).lower()
    assert "python only (v1)" not in body
    assert "techstack" in body      # detect the stack first
    # support matrix: native (python) lane + subprocess (any-lang) lane
    assert "native" in body
    assert "subprocess" in body
    assert "--mirror-lang" in body
    # refuse lane still exists (impossible case), just not the default
    assert "refuse" in body
    # containment-by-OS disclosure (R9 jail vs best-effort fallback)
    assert "containment" in body
    assert "fallback" in body
    assert "bwrap" in body


def test_every_referenced_drawer_exists():
    body = _body(_SKILL_MD)
    for ref in re.findall(r"references/([A-Za-z0-9_.-]+\.md)", body):
        assert (_REFS / ref).exists(), "SKILL.md cites missing drawer references/%s" % ref


def test_expected_reference_drawers_present():
    for name in ("protocol.md", "strategy-classification.md", "data-workflow.md",
                 "llm-judge-design.md", "template-audit-log.md",
                 "sandbox-evidence.md", "eval-memory.md"):
        assert (_REFS / name).exists(), "missing references/%s" % name


def test_r9_sandbox_evidence_drawer_and_protocol_link():
    drawer = _REFS / "sandbox-evidence.md"
    assert drawer.exists(), "missing references/sandbox-evidence.md (R9 evidence protocol)"

    protocol = (_REFS / "protocol.md").read_text(encoding="utf-8")
    assert "references/sandbox-evidence.md" in protocol, \
        "protocol.md must point readers at the R9 evidence drawer"

    body = drawer.read_text(encoding="utf-8").lower()
    for phrase in ("sandbox_run.py", "evidence", "re-run", "never fills", "decision record"):
        assert phrase in body, "sandbox-evidence.md missing load-bearing phrase: %r" % phrase


def test_protocol_carries_inventory_and_p0_sourcing():
    protocol = (_REFS / "protocol.md").read_text(encoding="utf-8").lower()
    assert "inventory" in protocol
    assert "one card" in protocol
    # each P0 rule must carry a `source` anchor -- pin proximity, not full wording
    assert re.search(r"p0 hard-gate rules.{0,400}?source", protocol, re.DOTALL)
    assert "eval_config.py write" in protocol
    assert "case matrix" in protocol

    strategy = (_REFS / "strategy-classification.md").read_text(encoding="utf-8").lower()
    assert "epsilon" in strategy
    assert "pre-filled suggestion" in strategy
    assert "never fills" in strategy


def test_skill_dir_brand_and_ocr_clean():
    offenders = []
    for p in _SKILL_DIR.rglob("*.md"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            # the audit log legitimately names stripped brands as provenance
            if p.name == "template-audit-log.md":
                continue
            if _BRAND.search(line) or _OCR.search(line):
                offenders.append("%s:%d" % (p.relative_to(_SKILL_DIR), i))
    assert not offenders, "brand/OCR strings under skill dir: %s" % offenders


def test_memory_protocol_l4_keywords_pinned():
    protocol = (_REFS / "protocol.md").read_text(encoding="utf-8")
    protocol_lower = protocol.lower()
    assert protocol_lower.index("recall") < protocol_lower.index("strategy"), \
        "protocol.md Phase 1 must recall memory before proposing a strategy"
    for phrase in ("eval_memory.py", "cited_lessons", "decision record"):
        assert phrase.lower() in protocol_lower, \
            "protocol.md missing load-bearing memory phrase: %r" % phrase

    memory = (_REFS / "eval-memory.md").read_text(encoding="utf-8").lower()
    for phrase in ("append", "recall", "--limit", "evals/_memory"):
        assert phrase in memory, "eval-memory.md missing load-bearing phrase: %r" % phrase


def test_check_skill_structure_no_hard_finding():
    script = _ROOT / "scripts" / "check_skill_structure.py"
    result = subprocess.run(
        [sys.executable, str(script), str(_SKILL_DIR), "--strict"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# -- Phase 7: sandbox-evidence path is self-explaining in client repos --------

# Build the legacy token from fragments so THIS test file is not itself a hit
# for any absence-guard grepping the tree for the old name.
_LEGACY_EVIDENCE = "_r9" + "_evidence"
_NEW_EVIDENCE = "_sandbox" + "_evidence"


def test_sandbox_evidence_convention_renamed():
    # the client-facing evidence path must not carry the opaque internal name;
    # the R1-R9 dictionary lives in SKILL.md and does NOT travel to the client.
    for name in ("references/sandbox-evidence.md", "SKILL.md", "references/protocol.md"):
        text = (_SKILL_DIR / name).read_text(encoding="utf-8")
        assert _LEGACY_EVIDENCE not in text, \
            "%s still ships the opaque %s path" % (name, _LEGACY_EVIDENCE)
    combined = "".join(
        (_SKILL_DIR / n).read_text(encoding="utf-8")
        for n in ("references/sandbox-evidence.md", "SKILL.md", "references/protocol.md"))
    assert _NEW_EVIDENCE in combined, "the self-explaining %s path is absent" % _NEW_EVIDENCE


def test_internal_name_selfexplain_rule_present():
    # a general rule that internal names (R-numbers, gate codes) reaching a
    # client artifact must self-explain or carry the dictionary with them.
    text = (_SKILL_DIR / "references" / "sandbox-evidence.md").read_text(encoding="utf-8").lower()
    assert "self-explain" in text or "self-explanatory" in text
    assert "client" in text or "consuming repo" in text or "target repo" in text


def test_sandbox_print_selfexplains():
    # the runtime print that lands in a client repo must carry the skill name so
    # a reader who never saw the R1-R9 table still knows where it came from.
    text = (_SKILL_DIR / "scripts" / "sandbox_run.py").read_text(encoding="utf-8")
    assert "hs:eval-bootstrap" in text


# -- Phase 8: R10 post-audit is a real skill phase, honestly bounded ----------

def test_skill_has_phase6_r10():
    body = _body(_SKILL_MD)
    assert "Phase 6" in body and "R10" in body
    low = body.lower()
    assert "post-audit" in low or "post audit" in low
    assert "sealed-room" in low or "sealed room" in low


def test_r10b_three_locks_present():
    drawer = _REFS / "r10-postaudit.md"
    assert drawer.exists(), "missing references/r10-postaudit.md"
    text = drawer.read_text(encoding="utf-8")
    low = text.lower()
    # lock 1: a human MUST approve a derived spec (no machine self-grading)
    assert "derived-spec" in low or "derived spec" in low
    assert "approve" in low or "reviewer" in low
    # lock 2: falsifiable read-trail, honestly labelled best-effort (NOT a hard seal)
    assert "read-trail" in low or "read trail" in low
    assert "best-effort" in low
    assert "hard seal" not in low and "sealed jail" not in low and "cannot read" not in low
    # lock 3: confidence label HIGH vs LOW
    assert "HIGH" in text and "LOW" in text
    assert "confidence" in low


def test_r10b_references_revalidator():
    combined = "".join(
        (_SKILL_DIR / n).read_text(encoding="utf-8")
        for n in ("SKILL.md", "references/protocol.md", "references/r10-postaudit.md"))
    assert "hs:independent-revalidator" in combined
    low = combined.lower()
    assert "subagent_type" in low or "agent" in low


def test_r10a_names_three_mechanical_checks():
    combined = "".join(
        (_SKILL_DIR / n).read_text(encoding="utf-8")
        for n in ("references/protocol.md", "references/r10-postaudit.md"))
    assert "mutation_matrix.py" in combined
    assert "ci_wiring_check.py" in combined
    assert "gt_notes_lint.py" in combined
