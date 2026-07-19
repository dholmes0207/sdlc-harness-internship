"""Contract for the deterministic eval scaffolder (eval_scaffold.py).

The scaffolder stamps a working `evals/` tree into a target repo by mechanical
substitution — it copies + substitutes + makes directories and NOTHING else
(no data pipeline, no LLM). These tests pin: the per-strategy tree shape, that
every stamped .py is valid 3.9 with zero unrendered tokens, refuse-overwrite,
dry-run, raise-on-missing-key, unknown-stack rejection, that every judgment
value (threshold/production_module/p0_rules/domain_config/dimensions/
primary_dimension/judge_model/pipeline_model) is a REQUIRED arg with no code
default (L1), and — the one that ast.parse cannot prove — that the stamped
modules actually IMPORT each other.
"""

import ast
import importlib.util
import json
import subprocess
import sys

import pytest

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_scaffold.py"
_CONFIG_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_config.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_scaffold", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _judgment_args(strategy="ground-truth", **overrides):
    """Every required judgment value, valid-by-default, for a given strategy.

    Mirrors what a real caller supplies from an approved strategy card — tests
    override one field at a time to probe its validation in isolation.
    """
    args = dict(
        strategy=strategy,
        threshold=70,
        production_module="src/x.py",
        p0_rules="name must be non-null",
        domain_config={"normalizers": {}, "masks": {}},
        dimensions={"accuracy": 100},
        primary_dimension="accuracy",
        mirror_lang="python",
        forge="github",
    )
    if strategy in ("judge", "hybrid"):
        args["judge_model"] = "sonnet"
        args["pipeline_model"] = "haiku"
    args.update(overrides)
    return args


def _all_py(root):
    return list(Path(root).rglob("*.py"))


def _has_unrendered(root):
    bad = []
    for p in Path(root).rglob("*"):
        if p.is_file() and p.suffix in (".py", ".json"):
            if "${" in p.read_text(encoding="utf-8"):
                bad.append(str(p))
    return bad


# --- tree shape per strategy --------------------------------------------------

def test_ground_truth_tree_shape(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    et = tmp_path / "evals" / "eval_types" / "cv"
    for rel in ("pipeline_mirror.py", "scorer.py", "config_integrity.py", "runner.py",
                "tests/test_scorer.py", "tests/test_config_conformance.py",
                "tests/test_mirror_parity.py",
                "tests/production_fixtures/ground_truth.json"):
        assert (et / rel).exists(), "missing %s" % rel
    assert (tmp_path / "evals" / "scripts" / "run_production_evals.py").exists()
    assert (tmp_path / "evals" / "scripts" / "extract_data_text.py").exists()


def test_judge_tree_adds_judge_modules(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="x", **_judgment_args("judge"))
    et = tmp_path / "evals" / "eval_types" / "x"
    for rel in ("comparison.py", "thresholds.py", "judge_prompt.py",
                "judge_runner.py", "judge_rubric.md"):
        assert (et / rel).exists(), "judge strategy missing %s" % rel
    # config_integrity + the conformance guard are stamped in every strategy
    # tree (inherited from contract — every strategy has a scorer)
    assert (et / "config_integrity.py").exists()
    assert (et / "tests" / "test_config_conformance.py").exists()


def test_contract_tree_is_minimal(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="z", **_judgment_args("contract"))
    et = tmp_path / "evals" / "eval_types" / "z"
    assert (et / "scorer.py").exists()
    assert (et / "config_integrity.py").exists()
    assert (et / "tests" / "test_scorer.py").exists()
    assert (et / "tests" / "test_config_conformance.py").exists()
    # contract is minimal: no runner / judge machinery / mirror parity
    assert not (et / "runner.py").exists()
    assert not (et / "judge_runner.py").exists()
    assert not (et / "tests" / "test_mirror_parity.py").exists()
    assert not (et / "tests" / "test_mirror_contract.py").exists()


# --- rendered output is valid + fully substituted -----------------------------

def test_all_stamped_py_is_valid_py39_and_fully_rendered(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("hybrid"))
    for p in _all_py(tmp_path):
        ast.parse(p.read_text(encoding="utf-8"), feature_version=(3, 9))
    assert not _has_unrendered(tmp_path), _has_unrendered(tmp_path)


# --- guards -------------------------------------------------------------------

def test_refuse_overwrite_without_force(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    with pytest.raises(Exception):
        eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    # --force overwrites cleanly
    eb.scaffold(target=str(tmp_path), domain="cv", force=True, **_judgment_args("ground-truth"))


def test_dry_run_writes_nothing(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", dry_run=True, **_judgment_args("ground-truth"))
    assert not (tmp_path / "evals").exists()


def test_missing_key_raises_and_leaves_no_partial_tree(tmp_path):
    eb = _load()
    with pytest.raises(KeyError):
        # a template referencing an unmapped ${...} must raise before any write
        eb.render_text("scaffold self-check: ${definitely_not_a_key}",
                        eb.build_context("cv", **_judgment_args("ground-truth")))
    assert not (tmp_path / "evals").exists()


def test_self_target_fence_refuses_harness_repo(tmp_path):
    eb = _load()
    # a REAL harness tree (not just a dir named "harness") is the look-alike
    # footgun this belt guards against
    (tmp_path / "harness" / "plugins" / "hs" / "skills").mkdir(parents=True)
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    assert not (tmp_path / "evals").exists(), "fence must refuse before writing"


def test_target_with_plain_harness_dir_is_allowed(tmp_path):
    eb = _load()
    # a bare dir named "harness" is a common name in an ordinary user repo — it
    # must NOT trip the self-target fence; only a REAL harness tree should
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "notes.txt").write_text("just a folder", encoding="utf-8")
    plan = eb.scaffold(target=str(tmp_path), domain="cv", dry_run=True,
                        **_judgment_args("ground-truth"))
    assert plan, "a plain harness/ dir must not refuse scaffolding"


def test_target_with_real_harness_tree_is_refused(tmp_path):
    eb = _load()
    (tmp_path / "harness" / "plugins" / "hs" / "skills").mkdir(parents=True)
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path), domain="cv", dry_run=True,
                    **_judgment_args("ground-truth"))


def test_self_target_fence_refuses_orchestrator_score(tmp_path):
    eb = _load()
    (tmp_path / "orchestrator" / "critic").mkdir(parents=True)
    (tmp_path / "orchestrator" / "critic" / "score.py").write_text("# score", encoding="utf-8")
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    assert not (tmp_path / "evals").exists()


def test_domain_rejects_traversal_and_code_injection(tmp_path):
    eb = _load()
    # path-separator / traversal, quote/newline/backslash (breaks generated .py),
    # and % (corrupts a %-format judge prompt) — all refused before any write.
    # trailing \n / \t are the fullmatch-vs-`$` trap: re.match(...$) would pass them.
    for bad in ("../evil", "a/b", "..", 'a"b', "cv%extraction", "a\nb", "a b", "",
                "cv\n", "cv\t", "cv\r", "a\\b"):
        with pytest.raises(ValueError):
            eb.scaffold(target=str(tmp_path), domain=bad, **_judgment_args("contract"))
    assert not (tmp_path / "evals").exists(), "domain fence must refuse before writing"


def test_stack_rejects_absolute_and_traversal(tmp_path):
    eb = _load()
    # an absolute or `..` --stack would stamp templates from OUTSIDE the vetted
    # tree (arbitrary, possibly executable content) into the target
    fake = tmp_path / "fakestack"
    fake.mkdir()
    (fake / "scorer.py.tmpl").write_text("PWNED", encoding="utf-8")
    # an empty / '.' stack resolves to the template ROOT itself — also refused
    for bad in (str(fake), "../fakestack", "/etc", "", "."):
        with pytest.raises(ValueError):
            eb.scaffold(target=str(tmp_path / "tgt"), domain="cv", stack=bad,
                        **_judgment_args("contract"))
    assert not (tmp_path / "tgt").exists()


def test_self_target_fence_refuses_host_subfolder():
    eb = _load()
    host = eb._host_repo_root()
    assert host is not None, "host repo root must resolve from __file__"
    # a sub-folder of the actual host repo must trip the fence, not just the root
    for sub in ("orchestrator", "harness/data", "harness/plugins/hs/skills"):
        with pytest.raises(ValueError):
            eb.scaffold(target=str(host / sub), domain="cv", dry_run=True,
                        **_judgment_args("contract"))


def test_self_target_fence_refuses_out_descending_into_host():
    eb = _load()
    host = eb._host_repo_root()
    assert host is not None
    # target is the host's PARENT (passes the target-only self-target check), but
    # --out descends BACK into the host repo — the per-dest belt must catch it
    with pytest.raises(ValueError):
        eb.scaffold(target=str(host.parent), domain="cv",
                    out="%s/evals" % host.name, dry_run=True, **_judgment_args("contract"))


def test_context_values_reject_code_injection(tmp_path):
    eb = _load()
    # judge_model / pipeline_model land in `MODEL = "..."`; production_module in a
    # string literal; p0_rules in a `# ...` comment — a quote/newline breaks the
    # generated module, so each must be validated like domain is
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path / "a"), domain="cv",
                    **_judgment_args("judge", judge_model='sonnet"\nimport os'))
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path / "b"), domain="cv",
                    **_judgment_args("ground-truth", production_module='src/x.py"\nBAD'))
    with pytest.raises(ValueError):
        eb.build_context("cv", **_judgment_args("ground-truth", p0_rules="line1\nline2"))
    # threshold lands unquoted at `THRESHOLD = ${threshold}` — the one bare-expression
    # slot; a non-int string would inject live code into scorer.py
    with pytest.raises(ValueError):
        eb.build_context("cv", **_judgment_args(
            "ground-truth", threshold="70\nimport os; os.system('id')"))
    with pytest.raises(ValueError):
        eb.build_context("cv", **_judgment_args("ground-truth", threshold=True))
    # dimensions keys also reach a bare-expression-adjacent json.dumps slot —
    # a newline-bearing key must be refused before it ever gets there
    with pytest.raises(ValueError):
        eb.build_context("cv", **_judgment_args(
            "ground-truth", dimensions={"a\nb": 100}, primary_dimension="a\nb"))


def test_out_rejects_absolute_and_traversal(tmp_path):
    eb = _load()
    # an absolute --out replaces target entirely; a `..` --out climbs out of it —
    # both are arbitrary-write primitives past the self-target fence
    outside = tmp_path / "OUTSIDE"
    for bad in (str(outside / "evals"), "../OUTSIDE/evals", "/tmp/pwned-evals"):
        with pytest.raises(ValueError):
            eb.scaffold(target=str(tmp_path), domain="cv", out=bad, **_judgment_args("contract"))
    assert not outside.exists(), "out fence must refuse before writing outside target"
    assert not (tmp_path / "evals").exists()


def test_cli_out_knob_removed(tmp_path):
    # The --out CLI knob is intentionally gone: eval_config, emit-mirror-contract
    # and the tier-1 memory dir all hardcode <target>/evals, so a non-default out
    # would stamp a tree whose own card can never be found.
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--target", str(tmp_path),
         "--domain", "cv", "--strategy", "ground-truth", "--out", "custom_evals",
         "--threshold", "70", "--production-module", "src/x.py",
         "--p0-rules", "name must be non-null",
         "--dimensions", '{"accuracy": 100}', "--primary-dimension", "accuracy",
         "--domain-config", '{"normalizers": {}, "masks": {}}',
         "--mirror-lang", "python", "--forge", "github"],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert "out" in result.stderr.lower()
    assert not (tmp_path / "custom_evals").exists()


def test_unknown_stack_rejected(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--target", str(tmp_path),
         "--domain", "cv", "--strategy", "ground-truth", "--stack", "go",
         "--threshold", "70", "--production-module", "src/x.py",
         "--p0-rules", "name must be non-null",
         "--dimensions", '{"accuracy": 100}', "--primary-dimension", "accuracy",
         "--domain-config", '{"normalizers": {}, "masks": {}}',
         "--mirror-lang", "python", "--forge", "github"],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "go" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "evals").exists()


# --- L1: every judgment value is a REQUIRED arg, no code default --------------

def test_build_context_refuses_missing_judgment():
    eb = _load()
    # threshold/production_module/p0_rules/domain_config/dimensions/primary_dimension
    # all absent — must fail loudly (missing required kwonly args), never fill in
    # a silent default the way the pre-L1 code did.
    with pytest.raises(TypeError):
        eb.build_context("cv", strategy="ground-truth")


def test_build_context_no_judgment_literals():
    src = _SCRIPT.read_text(encoding="utf-8")
    for banned in ('"sonnet"', '"haiku"', "default=70", "src/%s.py", "define P0 hard-gate"):
        assert banned not in src, "leftover judgment literal in eval_scaffold.py: %s" % banned


def test_dimensions_validation():
    eb = _load()
    base = _judgment_args("ground-truth")
    # weights don't sum to 100
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(base, dimensions={"accuracy": 50}))
    # empty dimensions
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(base, dimensions={}))
    # unsafe name character
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(base, dimensions={"a b": 100}))
    # a bool weight (sums to 100 numerically) must still be refused as non-int
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(base, dimensions={"accuracy": True, "other": 99}))
    # primary_dimension not a key of dimensions
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(
            base, dimensions={"accuracy": 100}, primary_dimension="other"))


def test_domain_config_explicit_empty_ok_absent_refused():
    eb = _load()
    base = _judgment_args("ground-truth")
    with pytest.raises(ValueError):
        eb.build_context("cv", **dict(base, domain_config=None))
    # an explicit empty map for both sections is a valid (annotated) decision
    ctx = eb.build_context("cv", **dict(base, domain_config={"normalizers": {}, "masks": {}}))
    assert ctx["domain_config_json"]


def test_cli_missing_args_exit2(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--target", str(tmp_path), "--domain", "cv",
         "--strategy", "ground-truth", "--production-module", "src/x.py",
         "--p0-rules", "name must be non-null", "--dimensions", '{"accuracy": 100}',
         "--primary-dimension", "accuracy",
         "--domain-config", '{"normalizers": {}, "masks": {}}',
         "--mirror-lang", "python", "--forge", "github"],
        capture_output=True, text=True)
    # --threshold omitted (no default anymore) -> argparse required-arg exit
    assert result.returncode != 0
    assert "threshold" in (result.stdout + result.stderr).lower()


def test_judge_strategy_requires_both_models():
    eb = _load()
    base = _judgment_args("judge")
    only_judge_model = dict(base)
    del only_judge_model["pipeline_model"]
    with pytest.raises(ValueError):
        eb.build_context("cv", **only_judge_model)
    only_pipeline_model = dict(base)
    del only_pipeline_model["judge_model"]
    with pytest.raises(ValueError):
        eb.build_context("cv", **only_pipeline_model)


# --- import resolution: the stamped modules must import each other -------------

# A schema-valid card (P2 eval_config.py) matching the judge-strategy args
# `_judgment_args("judge")` scaffolds with below — FORK D (phase-7 ripple c):
# scorer.py now imports config_integrity at module load, which requires an
# approved card on disk before scorer can even be imported.
_JUDGE_CHAIN_CARD = {
    "schema_version": "1",
    "domain": "cv",
    "strategy": "judge",
    "surface": "extraction",
    "production_module": "src/x.py",
    "production_entry": "predict",
    "mirror_lang": "python",
    "forge": "github",
    "threshold": 70,
    "p0_rules": [{"rule": "name must be non-null", "source": "card"}],
    "dimensions": {"accuracy": 100},
    "primary_dimension": "accuracy",
    "judge_model": "sonnet",
    "pipeline_model": "haiku",
    "domain_config": {"normalizers": {}, "masks": {}},
    "case_matrix": [{"case": "c1", "input": "x", "expect": {"name": "x"}}],
    "epsilon": {"maturity": 1},
    "cited_lessons": [],
    "approved_by": "test",
    "approved_ts": "2026-07-14",
}


def test_import_resolution_judge_chain(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("judge"))
    # scaffold() only stamps the tree — it never writes eval_config.json (that
    # is eval_config.py's job in the real bootstrap flow). Write the approved
    # card here before importing the chain; the tree is already nested so
    # config_integrity's parents[2] resolves to evals/.
    card_path = tmp_path / "_card.json"
    card_path.write_text(json.dumps(_JUDGE_CHAIN_CARD), encoding="utf-8")
    write_result = subprocess.run(
        [sys.executable, str(_CONFIG_SCRIPT), "write", "--target", str(tmp_path),
         "--card", str(card_path)],
        capture_output=True, text=True)
    assert write_result.returncode == 0, write_result.stderr

    et = tmp_path / "evals" / "eval_types" / "cv"
    # a subprocess with the eval dir on sys.path must import the whole chain
    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "import scorer, pipeline_mirror, comparison, thresholds\n"
        "import runner, judge_prompt, judge_runner\n"
        "print('OK')\n" % str(et)
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_run_production_evals_imports_from_scripts_dir(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv", **_judgment_args("ground-truth"))
    script = tmp_path / "evals" / "scripts" / "run_production_evals.py"
    # importing the CLI module must resolve runner via its own sys.path setup
    probe = (
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('rpe', %r)\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print('OK')\n" % str(script)
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- --mirror-lang: two lanes (python unchanged, non-python -> guide) ---------

def test_mirror_lang_required():
    eb = _load()
    args = _judgment_args("ground-truth")
    del args["mirror_lang"]
    with pytest.raises(TypeError):
        eb.build_context("cv", **args)


def test_mirror_lang_validated(tmp_path):
    eb = _load()
    with pytest.raises(ValueError):
        eb.scaffold(target=str(tmp_path), domain="cv",
                    **_judgment_args("ground-truth", mirror_lang="node; rm -rf"))
    assert not (tmp_path / "evals").exists()


def test_python_lane_unchanged(tmp_path):
    """mirror_lang="python" (the explicit, non-default form) must stamp the
    exact same tree as before --mirror-lang existed -- a regression lock."""
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv",
                **_judgment_args("ground-truth", mirror_lang="python"))
    et = tmp_path / "evals" / "eval_types" / "cv"
    for rel in ("pipeline_mirror.py", "scorer.py", "config_integrity.py", "runner.py",
                "tests/test_scorer.py", "tests/test_config_conformance.py",
                "tests/test_mirror_parity.py",
                "tests/production_fixtures/ground_truth.json"):
        assert (et / rel).exists(), "python lane missing %s" % rel
    assert not (et / "mirror-implementation-guide.md").exists()
    assert not (et / "tests" / "test_mirror_contract.py").exists()
    assert (tmp_path / "evals" / "scripts" / "run_production_evals.py").exists()
    assert (tmp_path / "evals" / "scripts" / "extract_data_text.py").exists()


def test_nonpython_lane_tree_shape(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv",
                **_judgment_args("ground-truth", mirror_lang="node"))
    et = tmp_path / "evals" / "eval_types" / "cv"
    # no python mirror / mirror-parity test for a non-python lane
    assert not (et / "pipeline_mirror.py").exists()
    assert not (et / "tests" / "test_mirror_parity.py").exists()
    # the guide + contract test (P24) are stamped in their place instead
    assert (et / "mirror-implementation-guide.md").exists()
    assert (et / "tests" / "test_mirror_contract.py").exists()
    # python infra (scoring stays Python regardless of mirror_lang) still stamped
    for rel in ("scorer.py", "config_integrity.py", "runner.py",
                "tests/test_scorer.py", "tests/test_config_conformance.py"):
        assert (et / rel).exists(), "non-python lane missing python infra %s" % rel
    assert (tmp_path / "evals" / "scripts" / "run_production_evals.py").exists()


def test_guide_keywords(tmp_path):
    eb = _load()
    eb.scaffold(target=str(tmp_path), domain="cv",
                **_judgment_args("ground-truth", mirror_lang="node"))
    guide = (tmp_path / "evals" / "eval_types" / "cv" / "mirror-implementation-guide.md").read_text(
        encoding="utf-8")
    for kw in ("mirror_contract.json", "stdout", "deterministic", "sandbox"):
        assert kw in guide, "guide missing keyword %r" % kw


# --- --forge: mutually-exclusive CI file selection, no default (phase-26) -----

def test_forge_selects_ci_template(tmp_path):
    eb = _load()
    github_target = tmp_path / "gh"
    eb.scaffold(target=str(github_target), domain="cv",
                **_judgment_args("ground-truth", forge="github"))
    ci_dir = github_target / "evals" / "ci"
    assert (ci_dir / "production-evals.yml").exists()
    assert not (ci_dir / ".gitlab-ci-evals.yml").exists(), (
        "forge=github must never also stamp the gitlab CI file")

    gitlab_target = tmp_path / "gl"
    eb.scaffold(target=str(gitlab_target), domain="cv",
                **_judgment_args("ground-truth", forge="gitlab"))
    gl_ci_dir = gitlab_target / "evals" / "ci"
    assert (gl_ci_dir / ".gitlab-ci-evals.yml").exists()
    assert not (gl_ci_dir / "production-evals.yml").exists(), (
        "forge=gitlab must never also stamp the github CI file")


def test_forge_required(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--target", str(tmp_path),
         "--domain", "cv", "--strategy", "ground-truth",
         "--threshold", "70", "--production-module", "src/x.py",
         "--p0-rules", "name must be non-null",
         "--dimensions", '{"accuracy": 100}', "--primary-dimension", "accuracy",
         "--domain-config", '{"normalizers": {}, "masks": {}}',
         "--mirror-lang", "python"],
        capture_output=True, text=True)
    # --forge omitted (no default anymore) -> argparse required-arg exit
    assert result.returncode != 0
    assert "forge" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "evals").exists()
