"""Dogfoods the full mutation-matrix cycle on a REAL stamped eval tree
(scaffold -> card write -> domain fill -> mutation generate/run), proving the
render-layer presence check in test_eval_bootstrap_templates.py
(``test_invariant_8_non_vacuous_p0`` -- the ``${p0_rules}`` marker exists in
the template) is not sufficient on its own: a stamped tree that keeps the
``check_p0_gates`` stub still renders that marker, yet a mutation run against
it must FAIL. This file is the runtime proof; the templates test stays the
render-layer counterpart.

Builds real stamp trees via eval_scaffold.py + eval_config.py (same pattern as
harness/e2e/run_eval_bootstrap_slice.py and test_mutation_matrix.py), then
drives mutation_matrix.py's generate/run subcommands -- and, for one test,
the stamped run_production_evals.py CLI directly -- as real subprocesses and
asserts on their actual exit codes. No import-cheating, no fake data.
"""

import json
import subprocess
import sys

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCAFFOLD = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_scaffold.py"
_CONFIG = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_config.py"
_MUTATION = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "mutation_matrix.py"

_DOMAIN = "widget"

# A real domain fill (pipeline_mirror + scorer) -- appended to the stamped
# stub files the same way run_eval_bootstrap_slice.py / test_mutation_matrix.py
# do (a model's Phase 3.5 fill is always additive text, never a rewrite).
_MIRROR_FILL = '''

def run_pipeline(input_data):
    out = {}
    for line in input_data.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out
'''

_SCORER_FILL_COMMON = '''

def score_dimension(dimension_name, results):
    if not results:
        return 0.0
    passed = sum(1 for r in results if r.get("passed"))
    return 100.0 * passed / len(results)
'''

# The real p0 body: attribution dicts, not free strings -- this is
# what lets mutation_matrix.py's `run` grep stdout for "'rule_index': N" and
# assert the P0-mutation trips the NAMED rule (single rule here -> index 0).
_SCORER_FILL_REAL_P0 = _SCORER_FILL_COMMON + '''

def check_p0_gates(results):
    failures = []
    for r in results:
        for f in r.get("fields", []):
            if f["field"] != "name":
                continue
            if f["status"] in ("EXTRA", "MISS"):
                failures.append({"rule_index": 0,
                                  "msg": "name is null in %s" % r["case"]})
    return len(failures) == 0, failures
'''

# The placeholder tree: mirror + score_dimension are filled for real, but
# check_p0_gates is left exactly as eval_scaffold stamped it (failures == []
# always) -- the "gate blind" fixture that closes the R3 presence-gate hole.


def _card(threshold=70, epsilon=None):
    return {
        "schema_version": "1",
        "domain": _DOMAIN,
        "strategy": "ground-truth",
        "surface": "extraction",
        "production_module": "src/%s.py" % _DOMAIN,
        "production_entry": "extract",
        "mirror_lang": "python",
        "forge": "github",
        "threshold": threshold,
        "p0_rules": [
            {"rule": "name must be non-null", "source": "card",
             "target_axis": "name", "violation_value": None},
        ],
        "dimensions": {"accuracy": 100},
        "primary_dimension": "accuracy",
        "domain_config": {"normalizers": {}, "masks": {}},
        "case_matrix": [
            {"case": "c1.txt", "input": "name: Ann\nemail: ann@example.com",
             "expect": {"name": "Ann", "email": "ann@example.com"}, "baseline": True},
            {"case": "c2.txt", "input": "name: Bob\nemail: bob@example.com",
             "expect": {"name": "Bob", "email": "bob@example.com"}},
        ],
        "epsilon": epsilon or {"maturity": 0.1},
        "cited_lessons": [],
        "approved_by": "test",
        "approved_ts": "2026-07-14",
        # Phase 4 R3: a python card must be mutation-proven (>=1 mirror + >=1
        # p0-body code mutation) or `generate` refuses. The real fill's
        # check_p0_gates fires on name MISS, so the broken mirror (-> {}) is
        # caught (exit 1) and both mutations kill under _fill_stubs.
        "code_mutations": [
            {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
             "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
             "expected_exit": 1},
            {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
             "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                       "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
             "expected_exit": 1},
        ],
    }


def _stamp_tree(tmp: Path) -> Path:
    """Scaffold a ground-truth stack with every judgment arg the P1 CLI
    requires (L1: no code-side default) into a tmp repo. Returns evals/."""
    cmd = [sys.executable, str(_SCAFFOLD), "--target", str(tmp),
           "--domain", _DOMAIN, "--strategy", "ground-truth",
           "--threshold", "70", "--production-module", "src/%s.py" % _DOMAIN,
           "--p0-rules", "name must be non-null",
           "--dimensions", json.dumps({"accuracy": 100}),
           "--primary-dimension", "accuracy",
           "--domain-config", json.dumps({"normalizers": {}, "masks": {}}),
           "--mirror-lang", "python", "--forge", "github"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "scaffold failed: %s\n%s" % (r.stdout, r.stderr)
    return tmp / "evals"


def _write_card(tmp: Path, card: dict = None) -> Path:
    """Persist an approved strategy card via eval_config.py write (P2) --
    ONE p0 rule, dims {"accuracy": 100}, threshold 70, epsilon {"maturity":
    0.1}, a 2-case case_matrix. Returns the written eval_config.json path."""
    card = card if card is not None else _card()
    card_path = tmp / "_card.json"
    card_path.write_text(json.dumps(card), encoding="utf-8")
    r = subprocess.run([sys.executable, str(_CONFIG), "write", "--target", str(tmp),
                       "--card", str(card_path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "eval_config write failed: %s\n%s" % (r.stdout, r.stderr)
    return tmp / "evals" / "eval_config.json"


def _write_samples_and_gt(tmp: Path, card: dict):
    """Ground truth + sample files matching the card's case_matrix -- the
    fixture pattern from run_eval_bootstrap_slice.py / test_mutation_matrix.py."""
    domain_dir = tmp / "evals" / "eval_types" / _DOMAIN
    samples = tmp / "data" / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    items = []
    for case in card["case_matrix"]:
        (samples / case["case"]).write_text(case["input"] + "\n", encoding="utf-8")
        items.append({"case_file": case["case"], "ground_truth": dict(case["expect"])})
    gt_path = domain_dir / "tests" / "production_fixtures" / "ground_truth.json"
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps({"description": "p18-dogfood", "items": items},
                                  ensure_ascii=False), encoding="utf-8")
    return samples, gt_path


def _fill_stubs(tmp: Path) -> None:
    """Append a REAL fill: key:value mirror + pass-rate scorer + a REAL
    check_p0_gates body with rule_index attribution."""
    domain_dir = tmp / "evals" / "eval_types" / _DOMAIN
    with open(domain_dir / "pipeline_mirror.py", "a", encoding="utf-8") as f:
        f.write(_MIRROR_FILL)
    with open(domain_dir / "scorer.py", "a", encoding="utf-8") as f:
        f.write(_SCORER_FILL_REAL_P0)


def _fill_stubs_placeholder(tmp: Path) -> None:
    """Same mirror + pass-rate scorer, but check_p0_gates is LEFT as the
    stamped stub (failures == [] always) -- the "placeholder p0" tree."""
    domain_dir = tmp / "evals" / "eval_types" / _DOMAIN
    with open(domain_dir / "pipeline_mirror.py", "a", encoding="utf-8") as f:
        f.write(_MIRROR_FILL)
    with open(domain_dir / "scorer.py", "a", encoding="utf-8") as f:
        f.write(_SCORER_FILL_COMMON)


def _generate(config_path: Path, out_path: Path):
    return subprocess.run([sys.executable, str(_MUTATION), "generate",
                          "--config", str(config_path), "--out", str(out_path)],
                         capture_output=True, text=True, timeout=60)


def _run_matrix(config_path: Path, matrix_path: Path, evals_dir: Path, samples: Path,
               gt_path: Path, report_path: Path = None):
    args = ["run", "--config", str(config_path), "--matrix", str(matrix_path),
           "--evals-root", str(evals_dir), "--sample-dir", str(samples),
           "--ground-truth", str(gt_path)]
    if report_path:
        args += ["--report", str(report_path)]
    return subprocess.run([sys.executable, str(_MUTATION)] + args,
                          capture_output=True, text=True, timeout=60)


# -- test_full_cycle_real_p0_gate_passes_matrix ---------------------------

def test_full_cycle_real_p0_gate_passes_matrix(tmp_path):
    """Real fill tree: generate exit 0 -> run exit 0 (the P0-mutation makes
    the CLI exit 1 as expected; noise exit 0; +-epsilon both directions
    correct -- `run` exit 0 means EVERY mutation matched its expectation)."""
    _stamp_tree(tmp_path)
    _fill_stubs(tmp_path)
    card = _card()
    config_path = _write_card(tmp_path, card)
    samples, gt_path = _write_samples_and_gt(tmp_path, card)

    matrix_path = tmp_path / "matrix.json"
    gen = _generate(config_path, matrix_path)
    assert gen.returncode == 0, "generate failed: %s" % gen.stderr

    report_path = tmp_path / "report.json"
    ran = _run_matrix(config_path, matrix_path, tmp_path / "evals", samples, gt_path,
                      report_path=report_path)
    assert ran.returncode == 0, (
        "expected every mutation to match its expected exit; stderr:\n%s" % ran.stderr)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["control_baseline_ok"] is True
    assert report["mismatches"] == []
    p0_result = next(x for x in report["results"] if x["kills"] == "p0:0")
    assert p0_result["actual_exit"] == 1 and p0_result["ok"] is True
    assert 0 in p0_result["rule_indices_fired"]
    noise_results = [x for x in report["results"] if x["layer"] == "noise"]
    assert noise_results and all(x["actual_exit"] == 0 for x in noise_results)
    threshold_results = {x["id"]: x for x in report["results"] if x["layer"] == "threshold"}
    assert threshold_results["threshold-maturity-minus"]["actual_exit"] == 1
    assert threshold_results["threshold-maturity-plus"]["actual_exit"] == 0


# -- test_full_cycle_code_vehicle_catches_broken_code (lỗ #3 end-to-end) ---

def test_full_cycle_code_vehicle_catches_broken_code(tmp_path):
    """Full cycle with a card carrying `code_mutations`: scaffold -> card
    (+code) -> real fill -> generate -> run. The code vehicle breaks the SOURCE
    (mirror + the p0 gate body) in temp copies and the real eval must catch
    both (exit 1) -- the runtime proof that closes lỗ #3 (the matrix now
    breaks CODE, not just the answer key)."""
    _stamp_tree(tmp_path)
    _fill_stubs(tmp_path)
    card = _card()
    card["code_mutations"] = [
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1},
    ]
    config_path = _write_card(tmp_path, card)
    samples, gt_path = _write_samples_and_gt(tmp_path, card)

    matrix_path = tmp_path / "matrix.json"
    gen = _generate(config_path, matrix_path)
    assert gen.returncode == 0, "generate failed: %s" % gen.stderr

    report_path = tmp_path / "report.json"
    ran = _run_matrix(config_path, matrix_path, tmp_path / "evals", samples, gt_path,
                      report_path=report_path)
    assert ran.returncode == 0, (
        "every code-vehicle mutation must match its expected exit; stderr:\n%s" % ran.stderr)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["control_baseline_ok"] is True
    code_results = {x["id"]: x for x in report["results"] if x["layer"] == "code"}
    assert set(code_results) == {"cm-mirror", "cm-p0"}
    assert all(x["vehicle"] == "code" for x in code_results.values())
    assert all(x["ok"] and x["actual_exit"] == 1 for x in code_results.values())


# -- test_placeholder_p0_tree_fails_matrix (the presence-gate kill) -------

def test_placeholder_p0_tree_fails_matrix(tmp_path):
    """Placeholder tree (check_p0_gates left as the stamped stub): `run`
    exits 1 and the report names the P0-mutation that did NOT kill ("gate
    blind"). THIS is the shot that kills the R3 presence-gate hole -- a
    stamped tree that still carries the `${p0_rules}` marker (invariant-8's
    render-layer check) is NOT proof the gate actually blocks anything."""
    _stamp_tree(tmp_path)
    _fill_stubs_placeholder(tmp_path)
    card = _card()
    config_path = _write_card(tmp_path, card)
    samples, gt_path = _write_samples_and_gt(tmp_path, card)

    matrix_path = tmp_path / "matrix.json"
    gen = _generate(config_path, matrix_path)
    assert gen.returncode == 0, "generate failed: %s" % gen.stderr

    ran = _run_matrix(config_path, matrix_path, tmp_path / "evals", samples, gt_path)
    assert ran.returncode == 1, (
        "placeholder p0 tree must fail the mutation run (stdout: %s)" % ran.stdout)
    assert "gate blind" in ran.stderr, (
        "the mismatch must be classified as a blind gate, not a panicky one: %s" % ran.stderr)


# -- test_seeded_gt_violation_blocks_cli -----------------------------------

def test_seeded_gt_violation_blocks_cli(tmp_path):
    """Hand-mutate the ground truth (name -> None) and run the stamped
    run_production_evals.py CLI directly (no mutation_matrix.py involved) --
    proves check_p0_gates works at runtime with the real p0 fill, on the
    stamped CLI a model/CI would actually invoke."""
    _stamp_tree(tmp_path)
    _fill_stubs(tmp_path)
    card = _card()
    _write_card(tmp_path, card)
    samples, gt_path = _write_samples_and_gt(tmp_path, card)

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    gt["items"][0]["ground_truth"]["name"] = None
    gt_path.write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")

    cli = tmp_path / "evals" / "scripts" / "run_production_evals.py"
    r = subprocess.run([sys.executable, str(cli), "--sample-dir", str(samples),
                       "--ground-truth", str(gt_path)],
                      capture_output=True, text=True, timeout=30)
    assert r.returncode == 1, "seeded p0 violation must block (exit 1): %s\n%s" % (r.stdout, r.stderr)
    assert "P0 GATE: BLOCK" in r.stdout, r.stdout
    assert "'rule_index': 0" in r.stdout, (
        "the block must carry rule_index attribution, not a free string: %s" % r.stdout)


# -- test_matrix_refuses_after_card_edit ------------------------------------

def test_matrix_refuses_after_card_edit(tmp_path):
    """Edit the card without re-writing the sha256 sidecar -> generate exits
    2 (the hash chain protects the mutation lane too, not just eval_config
    verify)."""
    _stamp_tree(tmp_path)
    _fill_stubs(tmp_path)
    card = _card()
    config_path = _write_card(tmp_path, card)

    # Simulate a hand edit of eval_config.json AFTER approval -- the sidecar
    # sha256 is deliberately left stale.
    body = config_path.read_bytes()
    tampered = bytearray(body)
    tampered[0] ^= 0xFF
    config_path.write_bytes(bytes(tampered))

    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 2, "card drift must refuse generate (exit 2): %s" % r.stderr
    assert "card_hash mismatch" in r.stderr, r.stderr
    assert not (tmp_path / "matrix.json").exists()
