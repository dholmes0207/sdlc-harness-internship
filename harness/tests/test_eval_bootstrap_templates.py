"""Invariants for the eval-bootstrap skill's Python template set.

These templates are re-implemented (not code-copied) from an unlicensed
reference pack, so the audit is made executable here: every rule the reference
got wrong — brace-collision under naive `{name}` substitution, an LLM judge
wired INTO the deterministic scoring path, stale hard-coded model ids, missing
imports — is pinned by a test that fails if the template regresses.

Substitution model: templates use `string.Template` `${...}` tokens for the
mechanical scaffold vars. A real-value context (``_CTX``) mirrors exactly what
the scaffolder must supply, so a template referencing an unmapped token trips
``.substitute`` here first.
"""

import ast
import hashlib
import importlib.util
import json
import re
import string
import subprocess
import sys

import pytest

from pathlib import Path

from eval_bootstrap_denylist import BRAND_RE as _BRAND, OCR_RE as _OCR

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "references" / "templates" / "python"

# The full template set this phase renders (13 re-audited + 5 new).
_EXPECTED = [
    "scorer.py.tmpl",
    "pipeline_mirror.py.tmpl",
    "runner.py.tmpl",
    "comparison.py.tmpl",
    "thresholds.py.tmpl",
    "ground_truth.json.tmpl",
    "test_scorer.py.tmpl",
    "judge_prompt.py.tmpl",
    "judge_runner.py.tmpl",
    "judge_rubric.md.tmpl",
    "quality_report.md.tmpl",
    "production-eval-setup.md.tmpl",
    "github-actions.yml.tmpl",
    "gitlab-ci.yml.tmpl",  # new: GitLab CI equivalent (phase-26 --forge)
    "extract_data_text.py.tmpl",   # new: pdf/docx sidecar, no OCR
    "run_production_evals.py.tmpl",  # new: CLI entrypoint (exit 0/1/2)
    "config_integrity.py.tmpl",     # new: card-hash re-verify before scoring
    "test_mirror_parity.py.tmpl",   # new: mirror<->production parity, skip-honest
    "test_config_conformance.py.tmpl",  # new: scorer<->card conformance guard (P7)
    "mirror-implementation-guide.md.tmpl",  # new: non-python mirror_lang lane guide (P22)
    "test_mirror_contract.py.tmpl",  # new: contract tests on the FILLED mirror (P24)
]

# Mechanical scaffold context — the exact key set the scaffolder must provide.
# `threshold` is still a required scaffold var (validated as an int by
# build_context) even though scorer.py.tmpl no longer renders it as a bare
# literal (FORK D, phase-7): DIMENSIONS/THRESHOLD are read from the approved
# card at import time instead — see config_integrity.py.tmpl.
_DIMENSIONS = {"accuracy": 40, "completeness": 25, "format": 15, "consistency": 10, "recall": 10}

_CTX = {
    "domain": "testdomain",
    "threshold": "70",
    "production_module": "src/rule_based.py",
    "mirror_module": "pipeline_mirror",
    "scorer_module": "scorer",
    "runner_module": "runner",
    "judge_prompt_module": "judge_prompt",
    "judge_runner_module": "judge_runner",
    "judge_model": "sonnet",
    "pipeline_model": "haiku",
    "mirror_lang": "python",
    "p0_rules": "pass  # no P0 rules configured yet",
    "ext": "txt",
    "dimensions_json": json.dumps(_DIMENSIONS, sort_keys=True),
    "primary_dimension": "accuracy",
    "domain_config_json": json.dumps({"normalizers": {}, "masks": {}}, sort_keys=True),
}


def _read(name):
    return (_TEMPLATES / name).read_text(encoding="utf-8")


def _render(name, ctx=None):
    """Render a template through string.Template with the scaffold context."""
    return string.Template(_read(name)).substitute(ctx or _CTX)


def _py_templates():
    return [n for n in _EXPECTED if n.endswith(".py.tmpl")]


def _all_template_files():
    return sorted(p for p in _TEMPLATES.glob("*.tmpl"))


# --- anchor: the whole set must exist (forces RED before render) --------------

def test_all_expected_templates_present():
    missing = [n for n in _EXPECTED if not (_TEMPLATES / n).exists()]
    assert not missing, "missing templates under templates/python/: %s" % missing


# --- #1 AST-valid after substitution (catches brace-collision / .format) ------

def test_invariant_1_ast_valid_after_substitution():
    for name in _py_templates():
        rendered = _render(name)
        assert "${" not in rendered, "%s left an unrendered ${...} token" % name
        ast.parse(rendered)  # raises SyntaxError on the source brace bug


# --- #2 py3.9 syntax floor ----------------------------------------------------

def test_invariant_2_py39_syntax():
    for name in _py_templates():
        rendered = _render(name)
        ast.parse(rendered, feature_version=(3, 9))  # PEP604 bare-union would raise


# --- #3 no banned brand anywhere under python/ --------------------------------

def test_invariant_3_no_brand():
    offenders = []
    for p in _all_template_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _BRAND.search(line):
                offenders.append("%s:%d" % (p.name, i))
    assert not offenders, "brand strings must be stripped: %s" % offenders


# --- #4 no OCR dependency -----------------------------------------------------

def test_invariant_4_no_ocr():
    offenders = []
    for p in _all_template_files():
        if _OCR.search(p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert not offenders, "OCR deps (pytesseract/PIL) must not appear: %s" % offenders


# --- #5 R1 import allowlist for the eval-mock pipeline_mirror + runner ---------

def test_invariant_5_mirror_runner_no_external_io():
    banned = re.compile(r"\b(requests|boto3|psycopg2|openai|anthropic)\b|os\.environ\.get\([^)]*KEY|import\s+src\b")
    for name in ("pipeline_mirror.py.tmpl", "runner.py.tmpl"):
        src = _read(name)
        hits = [m.group(0) for m in banned.finditer(src)]
        assert not hits, "%s must stay dependency-free (R1): %s" % (name, hits)


# --- class-fix guard (P5): extractor -> pipeline_mirror vocabulary sweep ------

def test_no_extractor_vocabulary_left():
    """The extractor -> pipeline_mirror rename (P5) must sweep every site; this
    guard stays green forever after, catching a sibling a future edit misses.

    Scope: the template set (`*.tmpl`) + eval_scaffold.py only -- NOT prose refs
    (protocol.md, template-audit-log.md provenance) which are out of the
    mechanical guard by design. One explicit exclusion: extract_data_text.py.tmpl
    -- the pdf/docx text-sidecar tool, where "extractor" correctly names ITS OWN
    job (extracting sidecar text), unrelated to the renamed eval-mock.
    """
    banned = re.compile(r"\bextractor\b", re.IGNORECASE)
    exempt = {"extract_data_text.py.tmpl"}
    offenders = []
    for p in _all_template_files():
        if p.name in exempt:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                offenders.append("%s:%d: %s" % (p.name, i, line.strip()))
    scaffold_src = _SCAFFOLD_SCRIPT.read_text(encoding="utf-8")
    for i, line in enumerate(scaffold_src.splitlines(), 1):
        if banned.search(line):
            offenders.append("eval_scaffold.py:%d: %s" % (i, line.strip()))
    assert not offenders, (
        "leftover extractor vocabulary (class-fix sweep missed a site): %s" % offenders)


# --- #6 R2 the scorer/comparison layer is pure ---------------------------------

def test_invariant_6_pure_scorer():
    banned = re.compile(r"\b(requests|openai|anthropic|random|socket|urllib|httpx)\b")
    for name in ("scorer.py.tmpl", "comparison.py.tmpl"):
        src = _read(name)
        hits = [m.group(0) for m in banned.finditer(src)]
        assert not hits, "%s must be pure — no LLM/network/random (R2): %s" % (name, hits)
    scorer = _read("scorer.py.tmpl")
    assert "Decimal" in scorer and "ROUND_HALF_UP" in scorer, (
        "scorer must round with Decimal ROUND_HALF_UP (Excel-parity)")


# --- #7 determinism replay on the pure functions -------------------------------

def _load_rendered_module(name, mod_name, tmp_path, ctx=None):
    src = _render(name, ctx)
    path = tmp_path / (mod_name + ".py")
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- FORK D fixtures: scorer now reads DIMENSIONS/THRESHOLD from the verified
# card at import time (config_integrity.py, a sibling module) instead of a
# stamped literal — every test that imports scorer must provision a REAL
# nested tree (evals/eval_types/<domain>/{scorer.py,config_integrity.py} +
# evals/eval_config.json(+.sha256)) with that dir on sys.path. A flat
# _load_rendered_module can't work here: config_integrity's
# `Path(__file__).resolve().parents[2]` would resolve two levels ABOVE a flat
# tmp_path (never the intended evals/ root), and tmp_path is never on
# sys.path, so `from config_integrity import ...` inside scorer would
# ModuleNotFoundError. Mirrors test_import_resolution_judge_chain.

def _write_card_files(evals_dir, card):
    body = json.dumps(card, sort_keys=True, separators=(",", ":")).encode("utf-8")
    evals_dir.mkdir(parents=True, exist_ok=True)
    (evals_dir / "eval_config.json").write_bytes(body)
    (evals_dir / "eval_config.sha256").write_text(
        hashlib.sha256(body).hexdigest() + "\n", encoding="utf-8")


def _stamp_scorer_tree(tmp_path, domain, card):
    """Render scorer.py.tmpl + config_integrity.py.tmpl into a nested
    evals/eval_types/<domain>/ tree and write `card` as the approved
    eval_config.json(+.sha256) sidecar at evals/ — real layout, so
    config_integrity's parents[2] resolves to evals/. Returns the domain dir
    (caller puts it on sys.path before importing scorer)."""
    evals_dir = tmp_path / "evals"
    et = evals_dir / "eval_types" / domain
    et.mkdir(parents=True)
    ctx = dict(_CTX, domain=domain)
    (et / "scorer.py").write_text(_render("scorer.py.tmpl", ctx), encoding="utf-8")
    (et / "config_integrity.py").write_text(
        _render("config_integrity.py.tmpl", ctx), encoding="utf-8")
    _write_card_files(evals_dir, card)
    return et


def _reimport_scorer(et):
    """(Re-)import scorer from `et` on sys.path, evicting any cached
    scorer/config_integrity module first so a rewritten card is actually
    re-read at import time (not served from the sys.modules cache)."""
    sys.path.insert(0, str(et))
    sys.modules.pop("scorer", None)
    sys.modules.pop("config_integrity", None)
    try:
        import scorer
        return scorer
    finally:
        sys.path.remove(str(et))


def test_invariant_7_determinism_replay(tmp_path):
    # RT3-01: nested tree (see fixtures above), not the flat loader.
    et = _stamp_scorer_tree(tmp_path, "invariant7", {
        "domain": "invariant7", "threshold": 70,
        "dimensions": {"accuracy": 40, "completeness": 25, "format": 15,
                       "consistency": 10, "recall": 10},
    })
    scorer = _reimport_scorer(et)
    runner = _load_rendered_module("runner.py.tmpl", "runner", tmp_path)
    comparison = _load_rendered_module("comparison.py.tmpl", "comparison", tmp_path)

    # RT3-02: score([]) now RAISES in every state under FORK D (real dims ->
    # score_dimension NotImplementedError; empty dims -> the import guard) —
    # no fixture can compute score([]) against an unfilled template. Assert
    # determinism on the pure r1() helper instead.
    first_r1 = scorer.r1(77.55)
    for _ in range(5):
        assert scorer.r1(77.55) == first_r1

    first_norm = runner.normalize_generic("Nguyễn  Văn  An")
    for _ in range(5):
        assert runner.normalize_generic("Nguyễn  Văn  An") == first_norm

    cf1 = runner.compare_field("name", "Nguyen Van An", "Nguyễn Văn An")
    for _ in range(5):
        assert runner.compare_field("name", "Nguyen Van An", "Nguyễn Văn An") == cf1

    cn1 = comparison.normalize("Machine   Learning")
    for _ in range(5):
        assert comparison.normalize("Machine   Learning") == cn1


# --- FORK D: scorer reads dims/threshold from the verified card (not a
# stamped literal) — RED before the scorer.py.tmpl rewrite -------------------

def test_scorer_reads_dims_from_loader(tmp_path):
    dims = {"accuracy": 40, "completeness": 25, "format": 15, "consistency": 10, "recall": 10}
    et = _stamp_scorer_tree(tmp_path, "readsdims",
                            {"domain": "readsdims", "threshold": 70, "dimensions": dims})
    scorer = _reimport_scorer(et)
    assert scorer.DIMENSIONS == dims
    assert scorer.THRESHOLD == 70


def test_scorer_reflects_config_not_cached_literal(tmp_path):
    card = {"domain": "reflectcfg", "threshold": 70, "dimensions": {"accuracy": 100}}
    et = _stamp_scorer_tree(tmp_path, "reflectcfg", card)
    scorer1 = _reimport_scorer(et)
    assert scorer1.THRESHOLD == 70

    # tamper threshold + re-sign -> a FRESH import must see the NEW value —
    # proves a live loader-read at import time, not a cached stamped literal.
    evals_dir = tmp_path / "evals"
    _write_card_files(evals_dir, dict(card, threshold=55))
    scorer2 = _reimport_scorer(et)
    assert scorer2.THRESHOLD == 55

    # edit the json WITHOUT re-signing the sidecar -> hash mismatch closes the
    # "scorer logic edited, still green" hole for dims/threshold too, not just
    # the rest of the card.
    config_path = evals_dir / "eval_config.json"
    tampered = bytearray(config_path.read_bytes())
    tampered[0] ^= 0xFF
    config_path.write_bytes(bytes(tampered))
    with pytest.raises(Exception) as excinfo:
        _reimport_scorer(et)
    assert "drift" in str(excinfo.value).lower()


def test_scorer_empty_dims_raises(tmp_path):
    # card dims non-empty is a P1 requirement enforced upstream (eval_scaffold /
    # eval_config validators) — this test writes the config file BY HAND to
    # exercise the scorer's own defense-in-depth guard for a hash-valid card
    # that somehow still carries empty dimensions (RT3-10).
    et = _stamp_scorer_tree(tmp_path, "emptydims",
                            {"domain": "emptydims", "threshold": 70, "dimensions": {}})
    with pytest.raises(RuntimeError, match="empty dimensions"):
        _reimport_scorer(et)


def test_conformance_template_behavior(tmp_path):
    """Stamp test_config_conformance.py.tmpl into a nested tree and run it as
    a real subprocess pytest (RT3-01 pattern): passes against a valid card;
    tampering the card without re-signing fails BEFORE any score-shaped
    assertion can pass — no tautological "literal == config" check remains."""
    domain = "conformance"
    card = {
        "domain": domain, "threshold": 70,
        "dimensions": {"accuracy": 100},
        "p0_rules": [{"rule": "name must be non-null", "source": "card"}],
    }
    et = _stamp_scorer_tree(tmp_path, domain, card)
    tests_dir = et / "tests"
    tests_dir.mkdir()
    ctx = dict(_CTX, domain=domain)
    conformance_test = tests_dir / "test_config_conformance.py"
    conformance_test.write_text(
        _render("test_config_conformance.py.tmpl", ctx), encoding="utf-8")

    ok = subprocess.run([sys.executable, "-m", "pytest", str(conformance_test), "-q"],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # tamper the card WITHOUT re-signing -> the conformance test must fail to
    # even collect/run (drift caught at the test layer too), not silently pass.
    evals_dir = tmp_path / "evals"
    config_path = evals_dir / "eval_config.json"
    tampered = bytearray(config_path.read_bytes())
    tampered[0] ^= 0xFF
    config_path.write_bytes(bytes(tampered))

    drifted = subprocess.run([sys.executable, "-m", "pytest", str(conformance_test), "-q"],
                             capture_output=True, text=True)
    assert drifted.returncode != 0, drifted.stdout + drifted.stderr


# --- #8 R3 the P0 gate is a real fill-in, not vacuously dropped ----------------

def test_invariant_8_non_vacuous_p0():
    """Render-layer check only: the ${p0_rules} marker exists in the
    template. This does NOT prove the gate blocks anything at runtime -- a
    stamped tree that leaves check_p0_gates as the stub still carries this
    marker. Runtime proof lives in test_eval_bootstrap_mutation.py
    (test_placeholder_p0_tree_fails_matrix asserts the "gate blind"
    classification the presence check alone cannot catch)."""
    scorer = _read("scorer.py.tmpl")
    assert "${p0_rules}" in scorer, (
        "scorer must carry the ${p0_rules} placeholder — the P0 hard-gate is a "
        "domain fill-in, not silently removed. Note: this is a render-layer "
        "presence check only -- runtime proof lives in test_eval_bootstrap_mutation.py")
    assert "check_p0_gates" in scorer


# --- #9 R4 normalize_generic does NOT conflate diacritics by default; a vi
# profile (comment-out sample, registered explicitly) restores the old
# equivalence -- diacritic folding is a LANGUAGE norm (L1: judgment, not a
# code default). --------------------------------------------------------------

def test_invariant_9_normalize_generic_does_not_conflate_diacritics(tmp_path):
    runner = _load_rendered_module("runner.py.tmpl", "runner_n", tmp_path)
    # generic still folds case + collapses whitespace...
    assert runner.normalize_generic("Nguyễn  Văn  An") == runner.normalize_generic("nguyễn văn an")
    # ...but does NOT strip diacritics: a diacritic-stripped variant is a
    # DIFFERENT string under the generic normalizer.
    assert runner.normalize_generic("Nguyễn Văn An") != runner.normalize_generic("nguyen van an")
    assert runner.normalize_generic("  Hà Nội  ") != runner.normalize_generic("ha noi")


def test_invariant_9_vi_profile_conflates_diacritics_when_registered(tmp_path):
    """Registering the vi profile (the comment-out sample block, as the model
    would after uncommenting it via R9) restores the old diacritic-folding
    equivalence -- but only when a card explicitly opts a field into it."""
    runner = _load_rendered_module("runner.py.tmpl", "runner_n_vi", tmp_path)

    def normalize_vi_text(text):
        return runner.normalize_generic(runner.strip_diacritics(text))

    runner.NORMALIZERS["vi_text"] = normalize_vi_text
    runner.DOMAIN_CONFIG["normalizers"]["name"] = "vi_text"

    assert runner.normalize_field("name", "Nguyễn Văn An") == runner.normalize_field("name", "nguyen  van   an")
    assert runner.normalize_field("name", "  Hà Nội  ") == runner.normalize_field("name", "ha noi")


# --- #10 R5 PII masking never leaks the raw local-part / middle digits, now
# routed via DOMAIN_CONFIG (not a field-name guess) ----------------------------

def test_invariant_10_pii_mask(tmp_path):
    ctx = dict(_CTX, domain_config_json=json.dumps(
        {"normalizers": {}, "masks": {"email": "email", "phone": "phone"}}, sort_keys=True))
    runner = _load_rendered_module("runner.py.tmpl", "runner_pii", tmp_path, ctx=ctx)
    masked_email = runner.mask_value("email", "nguyenvana@gmail.com")
    assert masked_email.endswith("@gmail.com")
    assert "nguyenvana" not in masked_email
    assert "*" in masked_email

    masked_phone = runner.mask_value("phone", "0912345678")
    assert "2345" not in masked_phone
    assert "*" in masked_phone


# --- registry: config routing, unregistered normalizer, None-safety -----------

def test_normalize_field_routing_none_safe_and_unregistered_raises(tmp_path):
    """normalize_field: an unmapped field falls back to "generic"; None-safe;
    a normalizer name the card declares but that has no NORMALIZERS entry
    raises the DEDICATED NormalizerNotRegistered exception -- never a bare KeyError,
    so the CLI can catch it and exit 2, not let it crash the run as exit 1."""
    ctx = dict(_CTX, domain_config_json=json.dumps(
        {"normalizers": {"phone": "vi_phone"}, "masks": {}}, sort_keys=True))
    runner = _load_rendered_module("runner.py.tmpl", "runner_registry", tmp_path, ctx=ctx)

    # unmapped field -> falls back to "generic"
    assert runner.normalize_field("name", "  Hello  World ") == runner.normalize_generic("  Hello  World ")
    # None-safe
    assert runner.normalize_field("name", None) == ""

    # a normalizer NAMED on the card but never registered -> dedicated
    # exception type, not a bare KeyError
    assert issubclass(runner.NormalizerNotRegistered, Exception)
    with pytest.raises(runner.NormalizerNotRegistered, match="vi_phone"):
        runner.normalize_field("phone", "0912345678")


def test_mask_routing_by_config_not_fieldname(tmp_path):
    """Masking routes via DOMAIN_CONFIG only -- no more guessing by field name.
    A field that the OLD code always masked by exact-name match ("email") is
    now untouched unless the config explicitly declares it; an unrelated field
    name ("contact_email") stays untouched either way."""
    empty_masks_ctx = dict(_CTX, domain_config_json=json.dumps(
        {"normalizers": {}, "masks": {}}, sort_keys=True))
    runner = _load_rendered_module("runner.py.tmpl", "runner_mask_routing", tmp_path, ctx=empty_masks_ctx)
    assert runner.mask_value("email", "nguyenvana@gmail.com") == "nguyenvana@gmail.com"
    assert runner.mask_value("contact_email", "nguyenvana@gmail.com") == "nguyenvana@gmail.com"

    declared_ctx = dict(_CTX, domain_config_json=json.dumps(
        {"normalizers": {}, "masks": {"email": "email"}}, sort_keys=True))
    runner2 = _load_rendered_module("runner.py.tmpl", "runner_mask_routing2", tmp_path, ctx=declared_ctx)
    assert runner2.mask_value("email", "nguyenvana@gmail.com") != "nguyenvana@gmail.com"


# --- #11 R8 the judge runs a different model than the pipeline -----------------

def test_invariant_11_judge_different_model():
    judge = _read("judge_runner.py.tmpl")
    # a real runtime assert (not just a test) guards self-eval bias
    assert re.search(r"assert\s+.*judge.*!=.*pipeline|assert\s+.*pipeline.*!=.*judge",
                     judge, re.IGNORECASE), (
        "judge_runner must assert the judge model differs from the pipeline model (R8)")
    # and the scaffold defaults must themselves differ
    assert _CTX["judge_model"] != _CTX["pipeline_model"]


# --- #12 stack placement: every rendered .tmpl lives under templates/python/ ---

def test_invariant_12_stack_placement():
    skill = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap"
    stray = [str(p.relative_to(skill)) for p in skill.rglob("*.tmpl")
             if p.parent != _TEMPLATES]
    assert not stray, "templates must live only under templates/python/: %s" % stray


# --- #13 VL-2: the judge is ADVISORY ONLY — never in the scoring path ----------

def test_invariant_13_judge_advisory_only():
    judge = _read("judge_runner.py.tmpl")
    # the source multiplied the LLM score into final maturity + flipped `passed`
    assert "combined_multiplier" not in judge, (
        "VL-2: judge must not fold an LLM multiplier into maturity")
    assert not re.search(r'"maturity"\s*\]\s*\*', judge), (
        "VL-2: deterministic maturity must not be multiplied by a judge factor")
    assert "merged_passed" not in judge, (
        "VL-2: the judge must not compute a merged pass/fail verdict")
    # instead it attaches an advisory report that leaves the verdict untouched
    assert "judge_advisory" in judge, (
        "VL-2: judge findings must be attached as an advisory report")


# --- judge dims render from the card, not a hard-coded 5-name set (phase-8) ---
#
# judge_runner.py.tmpl's `from ${judge_prompt_module} import build_judge_prompt`
# renders to a plain `from judge_prompt import build_judge_prompt` — a real
# sibling-file import that a flat exec()/spec_from_file_location cannot satisfy
# (mirrors test_import_resolution_judge_chain's real-file pattern). Write both
# rendered modules onto tmp_path and put it on sys.path so the cross-import
# resolves for real.

def _load_judge_chain(tmp_path, ctx):
    (tmp_path / "judge_prompt.py").write_text(_render("judge_prompt.py.tmpl", ctx), encoding="utf-8")
    (tmp_path / "judge_runner.py").write_text(_render("judge_runner.py.tmpl", ctx), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop("judge_prompt", None)
    sys.modules.pop("judge_runner", None)
    try:
        import judge_runner
        return judge_runner
    finally:
        sys.path.remove(str(tmp_path))


_NEW_DIMS = {"faithfulness": 60, "fluency": 40}
_NEW_DIMS_CTX = dict(_CTX, dimensions_json=json.dumps(_NEW_DIMS, sort_keys=True),
                     primary_dimension="faithfulness")


def test_judge_dims_render_from_context(tmp_path):
    """A card declaring dims outside the old 5-name set must not KeyError —
    DIMENSIONS/PRIMARY_DIMENSION render from ${dimensions_json}/${primary_dimension},
    and attach_judge_advisory reads the PRIMARY dim, not a hard-coded 'accuracy'."""
    judge_runner = _load_judge_chain(tmp_path, _NEW_DIMS_CTX)
    assert judge_runner.DIMENSIONS == _NEW_DIMS
    assert judge_runner.PRIMARY_DIMENSION == "faithfulness"

    fake_output = {
        "overall_score": 82, "confidence": 0.9,
        "dimensions": {
            "faithfulness": {"score": 88, "confidence": 0.9, "findings": "ok"},
            "fluency": {"score": 75, "confidence": 0.85, "findings": "ok"},
        },
        "patterns": [], "recommendations": [], "p0_triggered": False,
    }
    deterministic = {"maturity": 90.0, "passed": True, "threshold": 70,
                      "p0_gate_passed": True, "p0_failures": [], "dimensions": {}}
    # RED today: KeyError('accuracy') — no "accuracy" dim exists on this card.
    merged = judge_runner.attach_judge_advisory(deterministic, fake_output)
    assert merged["maturity"] == 90.0 and merged["passed"] is True  # untouched (VL-2)
    assert merged["judge_advisory"]["p0_recommendation"] is False  # confidence+faithfulness both above threshold


def test_parse_validates_card_dims(tmp_path):
    """Required dims = exactly the card's DIMENSIONS: missing -> ValueError;
    an extra dim the card never declared -> tolerated, verdict kept (judge
    models often return extras — do not die on them)."""
    judge_runner = _load_judge_chain(tmp_path, _NEW_DIMS_CTX)
    base = {"overall_score": 80, "confidence": 0.9, "patterns": [],
            "recommendations": [], "p0_triggered": False}

    missing = dict(base, dimensions={
        "faithfulness": {"score": 80, "confidence": 0.9},
    })
    with pytest.raises(ValueError, match="fluency"):
        judge_runner.parse_judge_response(json.dumps(missing))

    extra = dict(base, dimensions={
        "faithfulness": {"score": 80, "confidence": 0.9},
        "fluency": {"score": 70, "confidence": 0.9},
        "accuracy": {"score": 50, "confidence": 0.5},  # unknown dim, not on the card
    })
    parsed = judge_runner.parse_judge_response(json.dumps(extra))
    assert parsed["dimensions"]["faithfulness"]["score"] == 80
    assert parsed["dimensions"]["fluency"]["score"] == 70


def test_prompt_lists_card_dims(tmp_path):
    """build_judge_prompt's rendered prompt names + weighs the card's OWN dims,
    and carries none of the old hard-coded 5-name set (e.g. 'robustness')."""
    (tmp_path / "judge_prompt.py").write_text(
        _render("judge_prompt.py.tmpl", _NEW_DIMS_CTX), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    sys.modules.pop("judge_prompt", None)
    try:
        import judge_prompt
        prompt = judge_prompt.build_judge_prompt([], {"items": []}, "# mirror source")
    finally:
        sys.path.remove(str(tmp_path))

    combined = prompt["system"] + prompt["user"]
    assert "faithfulness 60%" in combined
    assert "fluency 40%" in combined
    assert "robustness" not in combined


# --- #14 no dead names after substitution (stub ships into a linted repo) -----

def test_invariant_14_no_dead_names_after_substitution():
    """Rendered templates must carry no unused import / unused local.

    Each `.py.tmpl` is written verbatim into the user's repo, so a dead name
    trips their CI linter on day one. Filter to the two dead-name classes only
    — undefined-name noise from cross-module stubs is out of scope here.
    """
    import io

    pyflakes_api = pytest.importorskip("pyflakes.api")
    from pyflakes import reporter as pyflakes_reporter

    for name in _py_templates():
        rendered = _render(name)
        out, err = io.StringIO(), io.StringIO()
        pyflakes_api.check(rendered, name, pyflakes_reporter.Reporter(out, err))
        dead_name_markers = (
            "imported but unused",
            "assigned to but never used",
            "redefinition of unused",
        )
        offenders = [
            ln for ln in out.getvalue().splitlines()
            if any(m in ln for m in dead_name_markers)
        ]
        assert not offenders, "%s has dead names: %s" % (name, offenders)


# --- runner error-isolation: one bad case must never abort the whole run ------

def _load_runner():
    """Exec the rendered runner template as a live module.

    The cross-module imports (`from pipeline_mirror import run_pipeline`) are lazy —
    inside run_eval — so exec of the module body only defines functions, no deps needed.
    """
    import types
    src = _render("runner.py.tmpl")
    mod = types.ModuleType("runner_under_test")
    exec(compile(src, "runner.py.tmpl", "exec"), mod.__dict__)
    return mod


def test_mask_email_survives_empty_local_part():
    # '@gmail.com' / '@' have an empty local-part — must not IndexError-abort a run
    r = _load_runner()
    assert r.mask_email("@gmail.com") == "***@gmail.com"
    assert isinstance(r.mask_email("@"), str)
    # a normal address still masks
    assert r.mask_email("nguyenvana@gmail.com") == "n********a@gmail.com"


def test_run_eval_isolates_every_malformed_case(tmp_path, monkeypatch):
    """One bad case must never abort the whole run — every per-case failure mode
    is isolated as an errored result: binary read, bad sidecar, non-dict extract,
    a ground-truth item missing case_file, and a non-dict ground_truth value."""
    import json as _json
    import types

    r = _load_runner()
    fake_ex = types.ModuleType("pipeline_mirror")
    fake_ex.run_pipeline = lambda text: None if text == "hello" else {"name": "z"}
    fake_sc = types.ModuleType("scorer")
    fake_sc.score = lambda results: {"maturity": 0, "results": len(results)}
    monkeypatch.setitem(sys.modules, "pipeline_mirror", fake_ex)
    monkeypatch.setitem(sys.modules, "scorer", fake_sc)

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.pdf").write_bytes(b"%PDF-1.4\x80\x81\xfe\xff")   # binary, no sidecar
    (sample / "case2.txt").write_text("hello", encoding="utf-8")      # extract -> non-dict
    (sample / "case3.txt.txt").write_bytes(b"\x80\x81\xfe\xff")       # bad-UTF-8 sidecar
    for name in ("case5.txt", "case6.txt", "case7.txt"):
        (sample / name).write_text("data", encoding="utf-8")         # readable, bad GT type
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(_json.dumps({"items": [
        {"case_file": "case1.pdf", "ground_truth": {"name": "x"}},
        {"case_file": "case2.txt", "ground_truth": {"name": "y"}},
        {"case_file": "case3.txt", "ground_truth": {"name": "z"}},
        {"ground_truth": {"name": "w"}},                              # missing case_file
        {"case_file": "case5.txt", "ground_truth": ["not", "a", "dict"]},  # truthy non-dict
        {"case_file": "case6.txt", "ground_truth": []},              # falsy non-dict — must error
        {"case_file": "case7.txt", "ground_truth": ""},              # falsy non-dict — must error
        {"case_file": 123, "ground_truth": {"a": "b"}},              # non-str case_file (int)
        {"case_file": ["case8.txt"], "ground_truth": {"a": "b"}},    # non-str case_file (list)
    ]}), encoding="utf-8")

    # must NOT raise — all nine bad cases isolated as errors (no vacuous PASS,
    # no os.path.join TypeError abort on a non-str case_file)
    results, scored = r.run_eval(str(sample), str(gt_path))
    assert len(results) == 9
    assert all("error" in x for x in results), results
    assert not any(x["passed"] for x in results), "a malformed ground_truth must never score PASS"
    assert scored["results"] == 9


def test_run_eval_absent_or_null_ground_truth_is_vacuous_not_error(tmp_path, monkeypatch):
    """An absent / explicitly-null ground_truth means 'no fields defined' — a
    vacuous case with no comparisons, NOT an errored one (that is the legit
    distinction from the malformed non-dict values above)."""
    import json as _json
    import types

    r = _load_runner()
    fake_ex = types.ModuleType("pipeline_mirror")
    fake_ex.run_pipeline = lambda text: {"name": "z"}
    fake_sc = types.ModuleType("scorer")
    fake_sc.score = lambda results: {"n": len(results)}
    monkeypatch.setitem(sys.modules, "pipeline_mirror", fake_ex)
    monkeypatch.setitem(sys.modules, "scorer", fake_sc)

    sample = tmp_path / "s"
    sample.mkdir()
    (sample / "c1.txt").write_text("data", encoding="utf-8")
    (sample / "c2.txt").write_text("data", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(_json.dumps({"items": [
        {"case_file": "c1.txt"},                       # ground_truth absent
        {"case_file": "c2.txt", "ground_truth": None},  # explicitly null
    ]}), encoding="utf-8")

    results, _ = r.run_eval(str(sample), str(gt_path))
    assert len(results) == 2
    assert not any("error" in x for x in results), results


def test_load_ground_truth_rejects_non_list_items(tmp_path):
    import json as _json

    r = _load_runner()
    for bad in ({"items": None}, {"items": "c1"}, {"items": {"a": 1}}, []):
        gt_path = tmp_path / "gt.json"
        gt_path.write_text(_json.dumps(bad), encoding="utf-8")
        try:
            r.load_ground_truth(str(gt_path))
        except ValueError:
            continue
        raise AssertionError("load_ground_truth must reject %r" % (bad,))


def test_build_judge_prompt_tolerates_malformed_ground_truth():
    """The advisory judge-prompt summary must not raise on a malformed
    ground_truth (first item missing/null ground_truth, a non-dict item, empty)."""
    import types

    src = _render("judge_prompt.py.tmpl")
    mod = types.ModuleType("judge_prompt_under_test")
    exec(compile(src, "judge_prompt.py.tmpl", "exec"), mod.__dict__)
    for gt in (
        {"items": [{"case_file": "c1"}]},                      # first item has no ground_truth
        {"items": [{"case_file": "c1", "ground_truth": None}]},  # explicit null
        {"items": [{"case_file": "c1", "ground_truth": "oops"}]},  # truthy non-dict (str)
        {"items": [{"case_file": "c1", "ground_truth": [1, 2, 3]}]},  # truthy non-dict (list)
        {"items": [{"case_file": "c1", "ground_truth": 5}]},   # truthy non-dict (int)
        {"items": ["not-a-dict"]},                             # non-dict item
        {"items": []},
        {},
    ):
        prompt = mod.build_judge_prompt([], gt, "# mirror source")
        assert isinstance(prompt, dict) and "system" in prompt and "user" in prompt


def test_compare_field_displays_zero_not_blank():
    """A real 0 / False must render as itself in the report, not vanish to '' —
    otherwise a MISMATCH row shows an empty got= and hides the value."""
    r = _load_runner()
    row = r.compare_field("years", 0, 1)
    assert row["status"] == "MISMATCH"
    assert row["extracted"] == "0" and row["extracted_raw"] == "0"
    assert row["expected"] == "1"
    # None still becomes a blank cell
    blank = r.compare_field("years", None, None)
    assert blank["extracted"] == "" and blank["expected"] == ""


# --- config_integrity.py.tmpl: card-hash re-verify before scoring (P3) --------

_SCAFFOLD_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_scaffold.py"
_CONFIG_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_config.py"

# A minimal card that satisfies eval_config.py's validate_card (P2 schema) —
# the exact fields/types a real approved strategy card would carry.
_MIN_CARD = {
    "schema_version": "1",
    "domain": "cardtest",
    "strategy": "ground-truth",
    "surface": "extraction",
    "production_module": "src/cardtest.py",
    "production_entry": "extract",
    "mirror_lang": "python",
    "forge": "github",
    "threshold": 70,
    "p0_rules": [{"rule": "name must be non-null", "source": "card"}],
    "dimensions": {"accuracy": 100},
    "primary_dimension": "accuracy",
    "domain_config": {"normalizers": {}, "masks": {}},
    "case_matrix": [{"case": "c1", "input": "name: x", "expect": {"name": "x"}}],
    "epsilon": {"maturity": 1},
    "cited_lessons": [],
    "approved_by": "test",
    "approved_ts": "2026-07-14",
}


# --- an unregistered normalizer is a SETUP error (exit 2), never an
# eval-fail (exit 1) -- run_production_evals.py.tmpl's real CLI, full
# subprocess (no import cheating in the exit-code contract). -----------------

def test_normalizer_unfilled_exits_2_not_1(tmp_path):
    """A card declares field "phone" -> normalizer "vi_phone", which the
    stamped runner.py never registers (it is only a comment-out sample) --
    the CLI must exit 2 (P3 setup-error bucket), NOT 1 (eval-fail) and NOT
    crash uncaught."""
    domain = "unfilled"
    scaffold_cmd = [sys.executable, str(_SCAFFOLD_SCRIPT), "--target", str(tmp_path),
                    "--domain", domain, "--strategy", "ground-truth",
                    "--threshold", "70", "--production-module", "src/%s.py" % domain,
                    "--p0-rules", "name must be non-null",
                    "--dimensions", json.dumps({"accuracy": 100}),
                    "--primary-dimension", "accuracy",
                    "--domain-config", json.dumps(
                        {"normalizers": {"phone": "vi_phone"}, "masks": {}}),
                    "--mirror-lang", "python", "--forge", "github"]
    scaffolded = subprocess.run(scaffold_cmd, capture_output=True, text=True)
    assert scaffolded.returncode == 0, scaffolded.stdout + scaffolded.stderr

    eval_dir = tmp_path / "evals" / "eval_types" / domain
    with open(eval_dir / "pipeline_mirror.py", "a", encoding="utf-8") as f:
        f.write('\n\ndef run_pipeline(input_data):\n'
                '    return {"phone": input_data.strip()}\n')
    with open(eval_dir / "scorer.py", "a", encoding="utf-8") as f:
        f.write('\n\ndef score_dimension(dimension_name, results):\n'
                '    if not results:\n        return 0.0\n'
                '    passed = sum(1 for r in results if r.get("passed"))\n'
                '    return 100.0 * passed / len(results)\n')

    card = dict(_MIN_CARD, domain=domain, production_module="src/%s.py" % domain,
                domain_config={"normalizers": {"phone": "vi_phone"}, "masks": {}})
    card_path = tmp_path / "_card_unfilled.json"
    card_path.write_text(json.dumps(card), encoding="utf-8")
    written = subprocess.run(
        [sys.executable, str(_CONFIG_SCRIPT), "write", "--target", str(tmp_path),
         "--card", str(card_path)], capture_output=True, text=True)
    assert written.returncode == 0, written.stdout + written.stderr

    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "case1.txt").write_text("0912345678", encoding="utf-8")
    gt_path = eval_dir / "tests" / "production_fixtures" / "ground_truth.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"phone": "0912345678"}},
    ]}), encoding="utf-8")

    cli = tmp_path / "evals" / "scripts" / "run_production_evals.py"
    result = subprocess.run(
        [sys.executable, str(cli), "--sample-dir", str(samples), "--ground-truth", str(gt_path)],
        capture_output=True, text=True)
    assert result.returncode == 2, (
        "unregistered normalizer must exit 2 (setup-error), not %d:\n%s\n%s"
        % (result.returncode, result.stdout, result.stderr))
    assert "vi_phone" in result.stderr and "not registered" in result.stderr, result.stderr


def test_config_integrity_behavior(tmp_path):
    """Render + exec config_integrity.py.tmpl (flat fixture, `_load_rendered_module`
    pattern) against an explicit evals_root — load OK returns the card dict; a
    1-byte flip or a missing sidecar both raise ConfigDriftError."""
    mod = _load_rendered_module("config_integrity.py.tmpl", "config_integrity", tmp_path)
    evals_dir = tmp_path / "evals_behavior"
    evals_dir.mkdir()

    card = {"domain": "testdomain", "threshold": 70, "dimensions": {"accuracy": 100}}
    body = json.dumps(card, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (evals_dir / "eval_config.json").write_bytes(body)
    digest = hashlib.sha256(body).hexdigest() + "\n"
    (evals_dir / "eval_config.sha256").write_text(digest, encoding="utf-8")

    result = mod.load_verified_config(str(evals_dir))
    assert result == card
    assert mod.verified_dimensions(str(evals_dir)) == card["dimensions"]
    assert mod.verified_threshold(str(evals_dir)) == card["threshold"]

    # flip 1 byte in the json -> ConfigDriftError, message names re-run bootstrap
    bad = bytearray(body)
    bad[10] ^= 0xFF
    (evals_dir / "eval_config.json").write_bytes(bytes(bad))
    with pytest.raises(mod.ConfigDriftError, match="re-run bootstrap"):
        mod.load_verified_config(str(evals_dir))
    (evals_dir / "eval_config.json").write_bytes(body)  # restore

    # delete .sha256 -> ConfigDriftError
    (evals_dir / "eval_config.sha256").unlink()
    with pytest.raises(mod.ConfigDriftError, match="re-run bootstrap"):
        mod.load_verified_config(str(evals_dir))


def test_missing_card_vs_drift_messages(tmp_path):
    """The 3 distinct states — missing card, missing sidecar, hash mismatch —
    produce 3 distinct messages; a sidecar with trailing whitespace still
    parses (stripped)."""
    mod = _load_rendered_module("config_integrity.py.tmpl", "config_integrity_msgs", tmp_path)
    evals_dir = tmp_path / "evals_msgs"
    evals_dir.mkdir()

    # (a) card never written
    with pytest.raises(mod.ConfigDriftError) as exc_a:
        mod.load_verified_config(str(evals_dir))
    msg_a = str(exc_a.value)
    assert "bootstrap has not written the card" in msg_a
    assert "eval_config.py write" in msg_a

    card = {"threshold": 70, "dimensions": {"accuracy": 100}}
    body = json.dumps(card, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (evals_dir / "eval_config.json").write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()

    # (b) sidecar missing -> drift
    with pytest.raises(mod.ConfigDriftError) as exc_b:
        mod.load_verified_config(str(evals_dir))
    msg_b = str(exc_b.value)
    assert "re-run bootstrap" in msg_b

    # sidecar with trailing whitespace still parses fine (stripped, not a drift)
    (evals_dir / "eval_config.sha256").write_text(digest + "\n\n", encoding="utf-8")
    assert mod.load_verified_config(str(evals_dir)) == card

    # (c) hash mismatch -> drift, message names expected/actual
    (evals_dir / "eval_config.json").write_bytes(body + b" ")
    with pytest.raises(mod.ConfigDriftError) as exc_c:
        mod.load_verified_config(str(evals_dir))
    msg_c = str(exc_c.value)
    assert "re-run bootstrap" in msg_c
    assert "expected=" in msg_c and "actual=" in msg_c

    assert len({msg_a, msg_b, msg_c}) == 3, "all 3 states must produce distinct messages"


def test_stamped_cli_exits_2_on_drift(tmp_path):
    """subprocess on a real stamped tree (pattern: e2e slice): scaffold + fill
    domain stubs + write an approved card (P2 CLI) -> clean run exits 0; hand-
    edit eval_config.json after the fact -> exit 2 (NOT 1 — config drift is a
    distinct bucket from an eval fail)."""
    domain = "cardtest"
    scaffold_result = subprocess.run(
        [sys.executable, str(_SCAFFOLD_SCRIPT), "--target", str(tmp_path),
         "--domain", domain, "--strategy", "ground-truth",
         "--threshold", "70", "--production-module", "src/%s.py" % domain,
         "--p0-rules", "name must be non-null",
         "--dimensions", json.dumps({"accuracy": 100}),
         "--primary-dimension", "accuracy",
         "--domain-config", json.dumps({"normalizers": {}, "masks": {}}),
         "--mirror-lang", "python", "--forge", "github"],
        capture_output=True, text=True)
    assert scaffold_result.returncode == 0, scaffold_result.stderr

    eval_dir = tmp_path / "evals" / "eval_types" / domain
    with open(eval_dir / "pipeline_mirror.py", "a", encoding="utf-8") as f:
        f.write(
            "\n\ndef run_pipeline(input_data):\n"
            "    out = {}\n"
            "    for line in input_data.splitlines():\n"
            "        if \":\" in line:\n"
            "            key, value = line.split(\":\", 1)\n"
            "            out[key.strip()] = value.strip()\n"
            "    return out\n")
    with open(eval_dir / "scorer.py", "a", encoding="utf-8") as f:
        f.write(
            "\n\nDIMENSIONS = {\"accuracy\": 100}\n\n\n"
            "def score_dimension(dimension_name, results):\n"
            "    if not results:\n        return 0.0\n"
            "    passed = sum(1 for r in results if r.get(\"passed\"))\n"
            "    return 100.0 * passed / len(results)\n")

    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "case1.txt").write_text("name: x\n", encoding="utf-8")
    gt_path = eval_dir / "tests" / "production_fixtures" / "ground_truth.json"
    gt = {"description": "t", "items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}}]}
    gt_path.write_text(json.dumps(gt), encoding="utf-8")

    card_path = tmp_path / "card.json"
    card_path.write_text(json.dumps(_MIN_CARD), encoding="utf-8")
    write_result = subprocess.run(
        [sys.executable, str(_CONFIG_SCRIPT), "write", "--target", str(tmp_path),
         "--card", str(card_path)],
        capture_output=True, text=True)
    assert write_result.returncode == 0, write_result.stderr

    cli = tmp_path / "evals" / "scripts" / "run_production_evals.py"
    clean = subprocess.run(
        [sys.executable, str(cli), "--sample-dir", str(samples), "--ground-truth", str(gt_path)],
        capture_output=True, text=True)
    assert clean.returncode == 0, "clean run should PASS (exit 0):\n%s\n%s" % (
        clean.stdout, clean.stderr)

    # hand-edit the card after approval -> drift must block at exit 2, NOT 1
    config_path = tmp_path / "evals" / "eval_config.json"
    body = json.loads(config_path.read_text(encoding="utf-8"))
    body["threshold"] = 1
    config_path.write_text(json.dumps(body), encoding="utf-8")

    drifted = subprocess.run(
        [sys.executable, str(cli), "--sample-dir", str(samples), "--ground-truth", str(gt_path)],
        capture_output=True, text=True)
    assert drifted.returncode == 2, "drifted card must exit 2 (not 1):\n%s\n%s" % (
        drifted.stdout, drifted.stderr)
    assert "re-run bootstrap" in drifted.stderr


# --- P23: runner subprocess branch — the P21 mirror_contract.json consumer ----

def _card_hash_for(card):
    """The exact card_hash string mirror_contract.json carries for `card`,
    matching `_write_card_files`'s sidecar hash (same canonical-JSON bytes)."""
    body = json.dumps(card, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _stamp_runner_tree(tmp_path, domain, card, contract=None, mirror_file=None):
    """Render runner.py.tmpl + scorer.py.tmpl + config_integrity.py.tmpl into a
    nested evals/eval_types/<domain>/ tree with an approved card sidecar — the
    same real-layout requirement as `_stamp_scorer_tree` (run_eval's lazy
    `from config_integrity import ...` / `from scorer import ...` need `et` on
    sys.path AND config_integrity's parents[2] to resolve to evals/).
    Optionally stamps mirror_contract.json + a fake mirror script beside
    runner.py for the subprocess branch. Returns the domain dir.
    """
    evals_dir = tmp_path / "evals"
    et = evals_dir / "eval_types" / domain
    et.mkdir(parents=True)
    ctx = dict(_CTX, domain=domain)
    (et / "runner.py").write_text(_render("runner.py.tmpl", ctx), encoding="utf-8")
    (et / "scorer.py").write_text(_render("scorer.py.tmpl", ctx), encoding="utf-8")
    # fill the stub score_dimension the template ships with a trivial
    # pass-rate scorer -- run_eval always calls through to `score()`, and
    # the un-filled template raises NotImplementedError by design (P23's
    # own concern here is the mirror dispatch, not scoring logic).
    with open(et / "scorer.py", "a", encoding="utf-8") as f:
        f.write(
            "\n\ndef score_dimension(dimension_name, results):\n"
            "    if not results:\n        return 0.0\n"
            "    passed = sum(1 for r in results if r.get('passed'))\n"
            "    return 100.0 * passed / len(results)\n")
    (et / "config_integrity.py").write_text(
        _render("config_integrity.py.tmpl", ctx), encoding="utf-8")
    _write_card_files(evals_dir, card)
    if contract is not None:
        (et / "mirror_contract.json").write_text(
            json.dumps(contract, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    if mirror_file is not None:
        name, body = mirror_file
        (et / name).write_text(body, encoding="utf-8")
    return et


def _import_runner(et):
    """(Re-)import runner from `et` on sys.path via a REAL file-backed spec (so
    `__file__` is set — run_eval's mirror_contract.json lookup is relative to
    it), evicting any cached runner/scorer/config_integrity module first.
    Caller is responsible for `sys.path.remove(str(et))` when done — the
    lazy `from config_integrity import ...` / `from scorer import ...` inside
    run_eval need `et` on sys.path for the DURATION of the run_eval call, not
    just at import time (mirrors `_reimport_scorer`'s note on this file)."""
    sys.path.insert(0, str(et))
    for name in ("runner", "scorer", "config_integrity"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("runner", et / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["runner"] = mod
    spec.loader.exec_module(mod)
    return mod


# A python SCRIPT standing in for a target-lang mirror, invoked via `python3`
# per the fake contract's argv_template below. The `lang` field on the
# contract (not the interpreter argv actually runs) is what routes run_eval
# into the subprocess branch — the interpreter itself is irrelevant to what
# is under test here (the dispatch + parse-fence + isolation behavior).
_FAKE_MIRROR_ECHO_NAME = (
    "import sys, json\n"
    "with open(sys.argv[-1], encoding='utf-8') as f:\n"
    "    text = f.read()\n"
    "print(json.dumps({'name': text.split(':', 1)[1].strip() if ':' in text else ''}))\n"
)


def test_subprocess_branch_happy(tmp_path):
    """A mirror_contract.json with a non-python `lang` beside runner.py routes
    run_eval through `_run_mirror_case` instead of `from ${mirror_module}
    import run_pipeline`. Downstream of `extracted` (compare_field, scoring)
    is shared, lane-agnostic code — so an identical per-field comparison
    shape here IS the proof this is a drop-in, not a second behavior."""
    domain = "subhappy"
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": _card_hash_for(card),
        "lang": "shfake",
        "mirror_filename": "pipeline_mirror.shfake",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["name"]},
        "timeout_seconds": 10, "zero_network": True, "deterministic": True,
    }
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract,
                            mirror_file=("pipeline_mirror.shfake", _FAKE_MIRROR_ECHO_NAME))

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("name: x", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}}]}), encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        results, scored = runner_mod.run_eval(str(sample), str(gt_path))
    finally:
        sys.path.remove(str(et))

    assert len(results) == 1
    assert results[0]["passed"] is True
    assert results[0]["fields"] == [
        {"field": "name", "status": "MATCH", "extracted": "x", "expected": "x",
         "extracted_raw": "x", "expected_raw": "x"}]


@pytest.mark.parametrize("bad_stdout", [
    "not json at all\n",
    '{"name": "a"}\n{"name": "b"}\n',
    "[1, 2, 3]\n",
], ids=["plain-text", "multi-doc", "json-array"])
def test_parse_fence_bad_stdout(tmp_path, bad_stdout):
    """A mirror that prints plain text / two JSON docs / a bare JSON array —
    anything that isn't exactly ONE JSON object — must isolate as a per-case
    error record, never abort the run; a later case still runs."""
    domain = "parsefence_%s" % re.sub(r"\W+", "", bad_stdout[:4])
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": _card_hash_for(card),
        "lang": "shfake",
        "mirror_filename": "pipeline_mirror.shfake",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["name"]},
        "timeout_seconds": 10, "zero_network": True, "deterministic": True,
    }
    mirror_script = (
        "import sys, json\n"
        "with open(sys.argv[-1], encoding='utf-8') as f:\n"
        "    text = f.read()\n"
        "if text.strip() == 'bad':\n"
        "    sys.stdout.write(%r)\n"
        "else:\n"
        "    print(json.dumps({'name': text.split(':', 1)[1].strip()}))\n"
    ) % bad_stdout
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract,
                            mirror_file=("pipeline_mirror.shfake", mirror_script))

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("bad", encoding="utf-8")
    (sample / "case2.txt").write_text("name: y", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}},
        {"case_file": "case2.txt", "ground_truth": {"name": "y"}},
    ]}), encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        results, scored = runner_mod.run_eval(str(sample), str(gt_path))
    finally:
        sys.path.remove(str(et))

    assert len(results) == 2
    assert "error" in results[0] and results[0]["passed"] is False, results[0]
    assert "error" not in results[1], results[1]
    assert results[1]["fields"][0]["status"] == "MATCH"


def test_timeout_isolated(tmp_path):
    """A mirror that sleeps past the contract's timeout_seconds must isolate
    that ONE case as a TIMEOUT error — later cases still run (never abort)."""
    domain = "timeoutcase"
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": _card_hash_for(card),
        "lang": "shfake",
        "mirror_filename": "pipeline_mirror.shfake",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["name"]},
        "timeout_seconds": 1, "zero_network": True, "deterministic": True,
    }
    mirror_script = (
        "import sys, json, time\n"
        "with open(sys.argv[-1], encoding='utf-8') as f:\n"
        "    text = f.read()\n"
        "if text.strip() == 'slow':\n"
        "    time.sleep(5)\n"
        "else:\n"
        "    print(json.dumps({'name': text.split(':', 1)[1].strip()}))\n"
    )
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract,
                            mirror_file=("pipeline_mirror.shfake", mirror_script))

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("slow", encoding="utf-8")
    (sample / "case2.txt").write_text("name: y", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}},
        {"case_file": "case2.txt", "ground_truth": {"name": "y"}},
    ]}), encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        results, scored = runner_mod.run_eval(str(sample), str(gt_path))
    finally:
        sys.path.remove(str(et))

    assert len(results) == 2
    assert "error" in results[0] and "timed out" in results[0]["error"], results[0]
    assert results[0]["passed"] is False
    assert "error" not in results[1], results[1]
    assert results[1]["fields"][0]["status"] == "MATCH"


def test_contract_hash_mismatch_dies_loud(tmp_path):
    """A contract whose card_hash no longer matches the current approved
    config must die LOUD and EARLY — before any case runs — with a message
    naming the re-emit command; never silently score against a stale
    contract."""
    domain = "hashmismatch"
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": "sha256:" + "0" * 64,  # deliberately wrong
        "lang": "shfake",
        "mirror_filename": "pipeline_mirror.shfake",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["name"]},
        "timeout_seconds": 10, "zero_network": True, "deterministic": True,
    }
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract,
                            mirror_file=("pipeline_mirror.shfake", _FAKE_MIRROR_ECHO_NAME))

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("name: x", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}}]}), encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        with pytest.raises(Exception, match="re-emit"):
            runner_mod.run_eval(str(sample), str(gt_path))
    finally:
        sys.path.remove(str(et))


def test_python_lane_untouched(tmp_path, monkeypatch):
    """No mirror_contract.json beside the runner (the `_load_runner()`
    bare-exec fixture never defines `__file__` either, which must read the
    same as "no contract" — the regression this pins) — run_eval takes the
    pre-P23 direct-import lane unchanged: `from pipeline_mirror import
    run_pipeline`, byte-identical result shape to every existing import-lane
    test in this file."""
    import types

    r = _load_runner()
    fake_mirror = types.ModuleType("pipeline_mirror")
    fake_mirror.run_pipeline = lambda text: {"name": text.split(":", 1)[1].strip()}
    fake_scorer = types.ModuleType("scorer")
    fake_scorer.score = lambda results: {"maturity": 100, "results": len(results)}
    monkeypatch.setitem(sys.modules, "pipeline_mirror", fake_mirror)
    monkeypatch.setitem(sys.modules, "scorer", fake_scorer)

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("name: x", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"name": "x"}}]}), encoding="utf-8")

    results, scored = r.run_eval(str(sample), str(gt_path))
    assert results == [{
        "case": "case1.txt", "notes": "", "passed": True,
        "fields": [{"field": "name", "status": "MATCH", "extracted": "x",
                    "expected": "x", "extracted_raw": "x", "expected_raw": "x"}],
    }]
    assert scored == {"maturity": 100, "results": 1}


_ENV_PROBE_MIRROR_SCRIPT = (
    "import sys, os, json\n"
    "with open(sys.argv[-1], encoding='utf-8') as f:\n"
    "    f.read()\n"
    "print(json.dumps({'env_keys': ','.join(sorted(os.environ.keys()))}))\n"
)

# The allowlist the runner is expected to forward -- kept here as the single
# source the two env tests below both check against, so a future allowlist
# edit only needs updating in one place.
# LC_CTYPE is not forwarded by the runner -- CPython's own C-locale UTF-8
# coercion (PEP 538) can inject it into a fresh interpreter's os.environ at
# startup, independent of what env dict subprocess.run() was given.
_EXPECTED_ENV_ALLOWLIST = {"PATH", "HOME", "TMPDIR", "XDG_CACHE_HOME",
                           "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "LC_CTYPE"}


def _run_env_probe(tmp_path, domain):
    """Stamp a runner tree with a mirror that dumps its own env keys, run one
    case through it, and return the set of env keys the child process saw."""
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": _card_hash_for(card),
        "lang": "shfake",
        "mirror_filename": "pipeline_mirror.shfake",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["env_keys"]},
        "timeout_seconds": 10, "zero_network": True, "deterministic": True,
    }
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract,
                            mirror_file=("pipeline_mirror.shfake", _ENV_PROBE_MIRROR_SCRIPT))

    sample = tmp_path / "samples"
    sample.mkdir()
    (sample / "case1.txt").write_text("irrelevant", encoding="utf-8")
    gt_path = tmp_path / "gt.json"
    # ground_truth is deliberately a placeholder the dynamic env-key list will
    # never equal -- this test reads `extracted_raw`, not the MATCH verdict.
    gt_path.write_text(json.dumps({"items": [
        {"case_file": "case1.txt", "ground_truth": {"env_keys": "<n/a>"}}]}), encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        results, scored = runner_mod.run_eval(str(sample), str(gt_path))
    finally:
        sys.path.remove(str(et))

    assert len(results) == 1
    assert "error" not in results[0], results[0]
    return set(results[0]["fields"][0]["extracted_raw"].split(","))


def test_env_minimal(tmp_path, monkeypatch):
    """The subprocess mirror must see an ALLOWLIST-ONLY environment -- NOT the
    full parent environment. Poison the PARENT process's os.environ with a
    fake secret/API key first and assert it never reaches the child (R1: zero
    inherited secrets, not just "no literal os.environ.get(KEY) call" in the
    runner's own source), and assert every key that DOES reach the child is
    inside the known allowlist (never a stray forwarded var)."""
    monkeypatch.setenv("HARNESS_TEST_SECRET", "sekrit-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")

    env_keys = _run_env_probe(tmp_path, "envminimal")

    assert "PATH" in env_keys
    assert "HARNESS_TEST_SECRET" not in env_keys
    assert "ANTHROPIC_API_KEY" not in env_keys
    assert env_keys <= _EXPECTED_ENV_ALLOWLIST, (
        "mirror child saw env keys outside the allowlist: %s"
        % (env_keys - _EXPECTED_ENV_ALLOWLIST))


def test_mirror_env_forwards_cache_dirs_not_secrets(tmp_path, monkeypatch):
    """go/ruby/rust mirrors need HOME or XDG_CACHE_HOME for their build/module
    cache -- PATH-only env makes `go run` die with "build cache is required
    ... neither $XDG_CACHE_HOME nor $HOME are defined", so every case errors
    and the whole run blocks. Set HOME + XDG_CACHE_HOME in the parent env
    alongside a fake secret/API key and a HARNESS_* var, and assert the cache
    dirs DO reach the child while the secret and HARNESS_* do NOT -- the
    allowlist must add exactly the cache-relevant keys, never open the door
    to the full environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fake-cache"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HARNESS_SECRET", "sekrit-should-not-leak")

    env_keys = _run_env_probe(tmp_path, "envcache")

    assert "HOME" in env_keys
    assert "XDG_CACHE_HOME" in env_keys
    assert "ANTHROPIC_API_KEY" not in env_keys
    assert "HARNESS_SECRET" not in env_keys
    assert env_keys <= _EXPECTED_ENV_ALLOWLIST, (
        "mirror child saw env keys outside the allowlist: %s"
        % (env_keys - _EXPECTED_ENV_ALLOWLIST))


def test_mirror_filename_traversal_rejected(tmp_path):
    """A hand-edited mirror_contract.json is not trusted input -- the emitter
    always writes a safe basename, but nothing stops a human from setting
    `mirror_filename` to something like "../evil" by hand. The resolved path
    must never land outside the eval dir; the runner must raise instead of
    silently reading (or later writing near) a file elsewhere on disk."""
    domain = "traversal"
    card = {"domain": domain, "threshold": 70, "dimensions": {"accuracy": 100}}
    contract = {
        "schema_version": "1.0",
        "card_hash": _card_hash_for(card),
        "lang": "shfake",
        "mirror_filename": "../evil",
        "invoke": {"argv_template": ["python3", "{mirror_path}"],
                   "input": "arg-file-utf8", "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["name"]},
        "timeout_seconds": 10, "zero_network": True, "deterministic": True,
    }
    et = _stamp_runner_tree(tmp_path, domain, card, contract=contract)
    # a real file at the escape target -- a broken guard would actually read it
    (et.parent / "evil").write_text("should never be read", encoding="utf-8")

    runner_mod = _import_runner(et)
    try:
        with pytest.raises(RuntimeError, match="outside"):
            runner_mod._run_mirror_case(contract, "irrelevant")
    finally:
        sys.path.remove(str(et))



# --- test_mirror_parity.py.tmpl: skip-honest mirror<->production parity (P6) --

_PARITY_CORRECT_MIRROR = (
    "def run_pipeline(input_data):\n"
    "    return {'label': input_data.strip().upper()}\n"
)
_PARITY_WRONG_MIRROR = (
    "def run_pipeline(input_data):\n"
    "    return {'label': 'WRONG'}\n"
)
_PARITY_CORRECT_PRODUCTION = (
    "def predict(input_data):\n"
    "    return {'label': input_data.strip().upper()}\n"
)


def _write_canonical_card(evals_dir, card):
    body = json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    body_bytes = body.encode("utf-8")
    (evals_dir / "eval_config.json").write_bytes(body_bytes)
    digest = hashlib.sha256(body_bytes).hexdigest() + "\n"
    (evals_dir / "eval_config.sha256").write_text(digest, encoding="utf-8")


def _build_parity_tree(tmp_path, *, mirror_body, write_production, production_body=""):
    """Stamp a minimal tree for the parity template under tmp_path (the repo
    root): evals/eval_types/paritytest/{config_integrity.py, pipeline_mirror.py,
    tests/test_mirror_parity.py} + an approved card, and (optionally) a fake
    production module under tmp_path/src/ — omitted entirely to exercise the
    skip-honesty path. Returns the path to the stamped parity test file."""
    domain = "paritytest"
    evals_dir = tmp_path / "evals"
    et = evals_dir / "eval_types" / domain
    tests_dir = et / "tests"
    tests_dir.mkdir(parents=True)

    ctx = dict(_CTX, domain=domain, production_module="src/paritytest_prod.py")
    (et / "config_integrity.py").write_text(
        _render("config_integrity.py.tmpl", ctx), encoding="utf-8")
    (et / "pipeline_mirror.py").write_text(mirror_body, encoding="utf-8")
    (tests_dir / "test_mirror_parity.py").write_text(
        _render("test_mirror_parity.py.tmpl", ctx), encoding="utf-8")

    card = dict(_MIN_CARD)
    card.update({
        "domain": domain,
        "production_module": "src/paritytest_prod.py",
        "production_entry": "predict",
        "case_matrix": [
            {"case": "c1", "input": "hello", "expect": {"label": "HELLO"}},
            {"case": "c2", "input": "world", "expect": {"label": "WORLD"}},
        ],
    })
    _write_canonical_card(evals_dir, card)

    if write_production:
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "paritytest_prod.py").write_text(production_body, encoding="utf-8")

    return tests_dir / "test_mirror_parity.py"


def _run_pytest_on(path):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(path), "-v", "-rs"],
        capture_output=True, text=True)


def test_parity_template_behavior_match(tmp_path):
    """(a) mirror output == production -> subprocess exit 0, output shows passed."""
    test_file = _build_parity_tree(
        tmp_path, mirror_body=_PARITY_CORRECT_MIRROR, write_production=True,
        production_body=_PARITY_CORRECT_PRODUCTION)
    result = _run_pytest_on(test_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout


def test_parity_template_behavior_mismatch(tmp_path):
    """(b) mirror deliberately wrong on 1 field -> subprocess exit != 0 (parity FAIL)."""
    test_file = _build_parity_tree(
        tmp_path, mirror_body=_PARITY_WRONG_MIRROR, write_production=True,
        production_body=_PARITY_CORRECT_PRODUCTION)
    result = _run_pytest_on(test_file)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "failed" in result.stdout


def test_parity_template_behavior_skip_honest(tmp_path):
    """(c) production import fails (module absent) -> subprocess exit 0 BUT
    output contains "skipped" AND the "NOT a pass" message — skip-honest,
    never a silent green."""
    test_file = _build_parity_tree(
        tmp_path, mirror_body=_PARITY_CORRECT_MIRROR, write_production=False)
    result = _run_pytest_on(test_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout.lower()
    assert "NOT a pass" in result.stdout


# --- rubric neutral core + extraction-as-appendix-example ----------------------
#
# judge_rubric.md.tmpl is copied VERBATIM (its suffix is not in _SUBSTITUTED --
# see eval_scaffold.py), so it must be domain-neutral in the part every domain
# reads: no hard-coded extraction formula or extraction-only dim anchors before
# the appendix. The old 5-dim extraction anchors survive as a calibration
# example, not the default -- moved verbatim into an appendix instead of deleted.

def test_rubric_neutral_core_plus_extraction_appendix():
    text = _read("judge_rubric.md.tmpl")
    marker = "## Example: extraction"
    assert marker in text, "rubric must keep the extraction calibration appendix"
    assert "19/20 fields correct" in text, (
        "the old extraction accuracy anchor must survive verbatim somewhere in the rubric")

    core = text[:text.index(marker)]
    assert "matched_fields / total_fields" not in core, (
        "the neutral core must not carry the extraction-only accuracy formula")
    assert "19/20 fields correct" not in core, (
        "the extraction anchor must live only in the appendix, not the neutral core"
    )


# --- thresholds.py.tmpl generalized: no fabricated domain numbers --------------
#
# L1: the tree's REAL pass threshold is THRESHOLD in the scorer (read from the
# approved card) -- thresholds.py is only an auxiliary multi-metric comparator,
# never a second source of judgment numbers. get_thresholds("default") must
# return an empty comparator; the old extraction numbers may survive only as a
# commented-out example (never as a live default).

def test_thresholds_no_live_domain_numbers(tmp_path):
    rendered = _render("thresholds.py.tmpl")
    live_lines = "\n".join(
        ln for ln in rendered.splitlines() if not ln.strip().startswith("#"))
    banned = ("0.75", "0.80", "0.70", "70.0")
    hits = [n for n in banned if n in live_lines]
    assert not hits, (
        "thresholds.py.tmpl leaks a fabricated domain number outside comments: %s" % hits)

    mod = _load_rendered_module("thresholds.py.tmpl", "thresholds_no_live_numbers", tmp_path)
    default = mod.get_thresholds("default")
    assert default.metric_thresholds == {}, (
        "get_thresholds('default') must return an empty comparator, not fabricated numbers")


def test_thresholds_generic_check(tmp_path):
    mod = _load_rendered_module("thresholds.py.tmpl", "thresholds_generic_check", tmp_path)
    thresholds = mod.QualityThresholds({"f1": 0.75})

    passed, failures = thresholds.check({"f1": 0.9})
    assert passed and not failures

    passed, failures = thresholds.check({"f1": 0.7})
    assert not passed
    assert any("f1" in f for f in failures), (
        "a threshold failure message must name the failing metric: %s" % failures)


# --- P24: contract tests on the FILLED mirror (denylist + determinism + -------
# import-fence + parity) ------------------------------------------------------

_DENYLIST_SCRIPT = (
    _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "sandbox_denylist.py")


def _load_denylist():
    spec = importlib.util.spec_from_file_location("sandbox_denylist", _DENYLIST_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _flatten_regex_tokens(common, lang_tokens):
    tokens = set(common)
    for values in lang_tokens.values():
        tokens.update(values)
    return tokens


def test_denylist_tables_in_sync():
    """The contract test template's inline regex-lane copy (SELF-CONTAINED --
    the target repo has no sandbox_denylist.py) must carry the EXACT same
    token set as sandbox_denylist.py's real regex lane. Mechanical, not a
    judgment call: an edit to either side without the other reds this
    immediately -- the accepted drift risk the phase design calls out."""
    denylist_mod = _load_denylist()
    real_tokens = _flatten_regex_tokens(
        denylist_mod._REGEX_COMMON_TOKENS, denylist_mod._REGEX_LANG_TOKENS)

    tree = ast.parse(_read("test_mirror_contract.py.tmpl"))
    common_literal = None
    lang_literal = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name = node.targets[0].id
            if name == "_REGEX_COMMON_TOKENS":
                common_literal = ast.literal_eval(node.value)
            elif name == "_REGEX_LANG_TOKENS":
                lang_literal = ast.literal_eval(node.value)
    assert common_literal is not None and lang_literal is not None, (
        "test_mirror_contract.py.tmpl must define _REGEX_COMMON_TOKENS / "
        "_REGEX_LANG_TOKENS at module level")
    template_tokens = _flatten_regex_tokens(common_literal, lang_literal)

    assert template_tokens == real_tokens, (
        "denylist token drift between test_mirror_contract.py.tmpl and "
        "sandbox_denylist.py -- template-only=%s denylist-only=%s"
        % (sorted(template_tokens - real_tokens), sorted(real_tokens - template_tokens)))


def _contract_card_hash(evals_dir):
    """Read back the sidecar hash `_write_canonical_card` just wrote -- the
    single source of truth, rather than re-deriving the canonical bytes a
    second way (which would risk a silent byte-format mismatch, e.g. a
    missing trailing newline, between the two computations)."""
    return "sha256:" + (evals_dir / "eval_config.sha256").read_text(encoding="utf-8").strip()


def _build_contract_tree(tmp_path, *, domain, lang, mirror_filename, mirror_body,
                          argv_template, write_production=True, production_body="",
                          case_matrix=None, timeout_seconds=10):
    """Stamp a minimal tree for test_mirror_contract.py.tmpl: config_integrity.py
    + runner.py (the P23 subprocess-call helper this template reuses) + the
    approved card + mirror_contract.json + the (optional) fake mirror, under
    tmp_path (the repo root). `mirror_body=None` exercises the skip-honesty
    path (mirror not filled yet). Returns the stamped test file path."""
    evals_dir = tmp_path / "evals"
    et = evals_dir / "eval_types" / domain
    tests_dir = et / "tests"
    tests_dir.mkdir(parents=True)

    ctx = dict(_CTX, domain=domain, production_module="src/%s_prod.py" % domain)
    (et / "config_integrity.py").write_text(
        _render("config_integrity.py.tmpl", ctx), encoding="utf-8")
    (et / "runner.py").write_text(_render("runner.py.tmpl", ctx), encoding="utf-8")
    (tests_dir / "test_mirror_contract.py").write_text(
        _render("test_mirror_contract.py.tmpl", ctx), encoding="utf-8")

    card = dict(_MIN_CARD)
    card.update({
        "domain": domain,
        "production_module": "src/%s_prod.py" % domain,
        "production_entry": "predict",
        "case_matrix": case_matrix or [
            {"case": "c1", "input": "hello", "expect": {"label": "HELLO"}},
        ],
    })
    _write_canonical_card(evals_dir, card)

    contract = {
        "schema_version": "1.0",
        "card_hash": _contract_card_hash(evals_dir),
        "lang": lang,
        "mirror_filename": mirror_filename,
        "invoke": {"argv_template": argv_template, "input": "arg-file-utf8",
                   "output": "stdout-json-utf8"},
        "entry_semantics": "one case input on stdin/arg-file -> ONE JSON object on stdout",
        "output_schema": {"type": "object", "keys_from_dimensions_and_cases": ["label"]},
        "timeout_seconds": timeout_seconds, "zero_network": True, "deterministic": True,
    }
    (et / "mirror_contract.json").write_text(
        json.dumps(contract, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    if mirror_body is not None:
        (et / mirror_filename).write_text(mirror_body, encoding="utf-8")

    if write_production:
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / ("%s_prod.py" % domain)).write_text(production_body, encoding="utf-8")

    return tests_dir / "test_mirror_contract.py"


# A python SCRIPT standing in for the target-lang mirror, invoked via `python3`
# per the fake contract's argv_template (same trick as the P23 fixtures: the
# `lang` field routes the DENYLIST/import-fence table, not the interpreter).
_CONTRACT_CLEAN_MIRROR = (
    "import sys, json\n"
    "with open(sys.argv[-1], encoding='utf-8') as f:\n"
    "    text = f.read()\n"
    "print(json.dumps({'label': text.strip().upper()}))\n"
)
_CONTRACT_CLEAN_PRODUCTION = (
    "def predict(input_data):\n"
    "    return {'label': input_data.strip().upper()}\n"
)
_CONTRACT_NONDETERMINISTIC_MIRROR = (
    "import sys, json, random\n"
    "with open(sys.argv[-1], encoding='utf-8') as f:\n"
    "    text = f.read()\n"
    "print(json.dumps({'label': text.strip().upper() + str(random.random())}))\n"
)
_CONTRACT_NETWORK_IMPORT_MIRROR = (
    "import sys, json\n"
    "# const cp = require('child_process');  -- forbidden node network/exec import\n"
    "with open(sys.argv[-1], encoding='utf-8') as f:\n"
    "    text = f.read()\n"
    "print(json.dumps({'label': text.strip().upper()}))\n"
)


def test_contract_template_kills_nondeterministic_mirror(tmp_path):
    """A mirror standing in for the target lang that prints RANDOM output
    (deterministic transform + a random suffix) must fail the stamped
    determinism 2-run block -- the mechanical layer the R9 evidence gate
    leans on to catch this class of bug."""
    test_file = _build_contract_tree(
        tmp_path, domain="nondet", lang="node", mirror_filename="pipeline_mirror.jsfake",
        mirror_body=_CONTRACT_NONDETERMINISTIC_MIRROR,
        argv_template=["python3", "{mirror_path}"], write_production=False)
    result = _run_pytest_on(test_file)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "test_determinism_two_run" in result.stdout
    assert "non-deterministic" in result.stdout


def test_contract_template_kills_network_import(tmp_path):
    """A mirror source containing a banned network/exec-capable token
    (`child_process`, a real js/node denylist token) must fail the denylist
    block."""
    test_file = _build_contract_tree(
        tmp_path, domain="netimport", lang="node", mirror_filename="pipeline_mirror.jsfake",
        mirror_body=_CONTRACT_NETWORK_IMPORT_MIRROR,
        argv_template=["python3", "{mirror_path}"], write_production=False)
    result = _run_pytest_on(test_file)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "test_denylist_clean" in result.stdout
    assert "child_process" in result.stdout


def test_contract_template_skips_unfilled(tmp_path):
    """No mirror file on disk (the R9 fill flow has not run yet) -- the WHOLE
    module skips with a message that says so; a SKIP is NOT a pass."""
    test_file = _build_contract_tree(
        tmp_path, domain="unfilled", lang="node", mirror_filename="pipeline_mirror.jsfake",
        mirror_body=None, argv_template=["python3", "{mirror_path}"], write_production=False)
    result = _run_pytest_on(test_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout.lower()
    assert "not filled" in result.stdout.lower()


def test_contract_template_clean_mirror_passes(tmp_path):
    """A clean, deterministic fake mirror with a matching production entry
    passes all three blocks -- the green counterpart to the RED tests
    above."""
    test_file = _build_contract_tree(
        tmp_path, domain="cleanpass", lang="node", mirror_filename="pipeline_mirror.jsfake",
        mirror_body=_CONTRACT_CLEAN_MIRROR, argv_template=["python3", "{mirror_path}"],
        write_production=True, production_body=_CONTRACT_CLEAN_PRODUCTION)
    result = _run_pytest_on(test_file)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "failed" not in result.stdout.lower()



# --- phase-26: --forge CI generalize -- github/gitlab marker parity ----------

_GITHUB_CI_TMPL = "github-actions.yml.tmpl"
_GITLAB_CI_TMPL = "gitlab-ci.yml.tmpl"

# Fake values for every single-brace {...} marker in the CI templates (these
# are copied VERBATIM -- never string.Template-substituted -- so a plain text
# .replace() here stands in for the human/model bootstrap fill).
_CI_MARKER_FAKES = {
    "{domain}": "cv",
    "{python_version}": "3.11",
    "{dependency_install_command}": "pip install -e .[dev]",
    "{unit_test_command}": "pytest tests/",
    "{eval_test_command}": "pytest evals/",
    "{run_evals_command}": "python evals/scripts/run_production_evals.py",
    "{setup_steps}": "true",
}


def _ci_markers(name):
    return set(re.findall(r"\{[a-z_]+\}", _read(name)))


def test_marker_parity_between_forges():
    github_markers = _ci_markers(_GITHUB_CI_TMPL)
    gitlab_markers = _ci_markers(_GITLAB_CI_TMPL)
    assert github_markers, "github CI template must carry markers"
    assert github_markers == gitlab_markers, (
        "the two forges must never drift on their marker contract: "
        "github-only=%s gitlab-only=%s"
        % (github_markers - gitlab_markers, gitlab_markers - github_markers))


def test_setup_steps_marker_present():
    for name in (_GITHUB_CI_TMPL, _GITLAB_CI_TMPL):
        assert "{setup_steps}" in _read(name), (
            "%s must carry the {setup_steps} marker -- the target-language "
            "interpreter install fill-in point" % name)


def test_ci_templates_parse_as_yaml_after_fill():
    """The .yml templates are copied verbatim (never string.Template
    substituted) -- this is the smoke test that proves a filled template is
    still valid YAML, standing in for the real bootstrap-time text fill."""
    yaml = pytest.importorskip("yaml")
    for name in (_GITHUB_CI_TMPL, _GITLAB_CI_TMPL):
        text = _read(name)
        for marker, fake in _CI_MARKER_FAKES.items():
            text = text.replace(marker, fake)
        assert "{" not in text and "}" not in text, (
            "%s left an unfilled marker after substitution" % name)
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict), "%s must parse to a YAML mapping" % name


def test_scorer_r1_rejects_non_finite(tmp_path, monkeypatch):
    # r1() must fail loud on NaN/Inf (a dimension score is a real number),
    # not raise a cryptic decimal.InvalidOperation deep inside quantize().
    import math
    import types
    src = _render("scorer.py.tmpl")
    fake_ci = types.ModuleType("config_integrity")
    fake_ci.load_verified_config = lambda root=None: {"dimensions": {"a": 100}, "threshold": 70}
    monkeypatch.setitem(sys.modules, "config_integrity", fake_ci)
    domain_dir = tmp_path / "eval_types" / "d"
    domain_dir.mkdir(parents=True)
    ns = {"__file__": str(domain_dir / "scorer.py")}
    exec(compile(src, "scorer.py", "exec"), ns)
    r1 = ns["r1"]
    assert r1(77.55) == 77.6
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            r1(bad)


def test_scorer_p0_example_uses_dict_shape():
    # The in-file P0 example must append a dict {"rule_index", "msg"} -- the
    # shape the conformance test and mutation rule-id attribution require --
    # never a bare string that silently breaks both.
    src = _render("scorer.py.tmpl")
    assert '"rule_index"' in src and '"msg"' in src
    assert 'failures.append("P0:' not in src
