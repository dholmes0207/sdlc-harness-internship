"""Contract for mutation_matrix.py — the deterministic 3-layer mutation matrix
that meta-tests the eval gate itself (P0 kill / threshold +-epsilon / noise).

Builds real stamp trees via eval_scaffold.py + eval_config.py (same pattern as
harness/e2e/run_eval_bootstrap_slice.py), fills a REAL pipeline_mirror + scorer
(including a real check_p0_gates body per scenario), then drives
mutation_matrix.py's generate/run subcommands as real subprocesses and asserts
on their actual exit codes -- no import-cheating, no fake data.
"""

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCAFFOLD = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_scaffold.py"
_CONFIG = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_config.py"
_MUTATION = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "mutation_matrix.py"


def _run(args, **kw):
    return subprocess.run([sys.executable, str(_MUTATION)] + args,
                          capture_output=True, text=True, **kw)


def _load_mm():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mutation_matrix", _MUTATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- _apply_code_patch unit contract (Phase 3: deterministic source patch) --

def test_apply_code_patch_symbol_return_patches_all_defs():
    # a stamped mirror has TWO run_pipeline defs (template stub + Phase-3.5
    # append); Python calls the LAST one, so symbol_return MUST patch every
    # matching def, not just the first (shadowed) stub.
    mm = _load_mm()
    src = ("def run_pipeline(x):\n    raise NotImplementedError\n\n"
           "def run_pipeline(x):\n    return {'a': 1}\n")
    patched = mm._apply_code_patch(src, {"kind": "symbol_return",
                                         "symbol": "run_pipeline", "value": {}})
    ns = {}
    exec(compile(patched, "<patched>", "exec"), ns)
    assert ns["run_pipeline"]("z") == {}


def test_apply_code_patch_symbol_return_missing_symbol_refused():
    mm = _load_mm()
    with pytest.raises(ValueError):
        mm._apply_code_patch("y = 1\n", {"kind": "symbol_return",
                                         "symbol": "run_pipeline", "value": {}})


def test_apply_code_patch_regex_noop_refused():
    # a regex anchor that never matches must RAISE (a silent no-op would let a
    # mutation "pass" without ever changing the code -- a false clean).
    mm = _load_mm()
    with pytest.raises(ValueError):
        mm._apply_code_patch("x = 1\n", {"kind": "regex_sub", "anchor": "NOPE",
                                         "replacement": "y", "count": 1})


def test_apply_code_patch_regex_wrong_count_refused():
    # anchor matches 3 times but count declares 5 -> non-deterministic intent,
    # must RAISE rather than silently substitute a different number.
    mm = _load_mm()
    with pytest.raises(ValueError):
        mm._apply_code_patch("a\na\na\n", {"kind": "regex_sub", "anchor": "a",
                                           "replacement": "b", "count": 5})


def test_apply_code_patch_symbol_return_only_toplevel():
    # a class METHOD sharing the symbol name must NOT be patched — only the
    # module-level def (the stub+fill case) is a legitimate target.
    mm = _load_mm()
    src = ("def check_p0_gates(results):\n    return (True, [])\n\n"
           "class Helper:\n    def check_p0_gates(self, results):\n        return 'method'\n")
    patched = mm._apply_code_patch(src, {"kind": "symbol_return",
                                         "symbol": "check_p0_gates", "value": [False, []]})
    ns = {}
    exec(compile(patched, "<p>", "exec"), ns)
    assert ns["check_p0_gates"]([]) == [False, []]           # module-level patched
    assert ns["Helper"]().check_p0_gates([]) == "method"     # method untouched


def test_apply_code_patch_patches_control_flow_nested_def():
    # a module-level def wrapped in an if/try block is still a module-scope
    # binding and MUST be patched (top-level-only iteration would miss it).
    mm = _load_mm()
    src = ("if True:\n    def run_pipeline(x):\n        return {'real': 1}\n")
    patched = mm._apply_code_patch(src, {"kind": "symbol_return",
                                         "symbol": "run_pipeline", "value": {}})
    ns = {}
    exec(compile(patched, "<p>", "exec"), ns)
    assert ns["run_pipeline"]("z") == {}


def test_apply_code_patch_async_def_patched():
    mm = _load_mm()
    src = "async def run_pipeline(x):\n    return {'real': 1}\n"
    patched = mm._apply_code_patch(src, {"kind": "symbol_return",
                                         "symbol": "run_pipeline", "value": {}})
    import asyncio
    ns = {}
    exec(compile(patched, "<p>", "exec"), ns)
    assert asyncio.run(ns["run_pipeline"]("z")) == {}


# -- bare config helpers (generate-only tests -- no stamp tree needed) -----

def _canonical_bytes(card: dict) -> bytes:
    return (json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
           + "\n").encode("utf-8")


def _write_bare_config(dir_: Path, card: dict) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    body = _canonical_bytes(card)
    config_path = dir_ / "eval_config.json"
    config_path.write_bytes(body)
    (dir_ / "eval_config.sha256").write_text(hashlib.sha256(body).hexdigest() + "\n",
                                             encoding="utf-8")
    return config_path


def _base_card(**overrides) -> dict:
    card = {
        "schema_version": "1",
        "domain": "widget",
        "strategy": "ground-truth",
        "surface": "extraction",
        "production_module": "src/widget.py",
        "production_entry": "extract",
        "mirror_lang": "python",
        "forge": "github",
        "threshold": 70,
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
        "epsilon": {"maturity": 5},
        "cited_lessons": [],
        "approved_by": "test",
        "approved_ts": "2026-07-14",
        # Phase 4 R3: every python card must be mutation-proven (>=1 mirror + >=1
        # p0-body code mutation) or `generate` refuses. Bake a valid pair into the
        # default so existing fixtures stay green; a test that probes the refuse
        # path overrides code_mutations explicitly.
        "code_mutations": _code_mutations_pair(),
    }
    card.update(overrides)
    return card


# -- full stamp-tree helpers (run tests -- a real subprocess-driven gate) --

_MIRROR_FILL = '''

def run_pipeline(input_data):
    out = {}
    for line in input_data.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out
'''


def _scorer_fill(check_p0_gates_body: str) -> str:
    return '''

def score_dimension(dimension_name, results):
    return 100.0


def check_p0_gates(results):
%s
''' % check_p0_gates_body


_VARIANT_REAL = """    failures = []
    for r in results:
        for f in r.get("fields", []):
            if f["field"] != "name":
                continue
            if f["status"] == "EXTRA":
                failures.append({"rule_index": 0, "msg": "name became null in %s" % r["case"]})
            elif f["status"] == "MISMATCH" and len(f.get("expected_raw") or "") > 10:
                failures.append({"rule_index": 1, "msg": "name overflow rule in %s" % r["case"]})
    return len(failures) == 0, failures
"""

_VARIANT_BLIND = """    return True, []
"""

_VARIANT_PANICKY = """    failures = []
    for r in results:
        for f in r.get("fields", []):
            if f.get("expected_raw") != f.get("extracted_raw"):
                failures.append({"rule_index": 0,
                                  "msg": "raw mismatch %s in %s" % (f["field"], r["case"])})
    return len(failures) == 0, failures
"""

# A check_p0_gates that BLOCKS whenever the name field is missing/extra/wrong —
# used by the code-vehicle tests: a clean mirror keeps every name MATCH (control
# passes, exit 0), while a broken mirror (run_pipeline -> {}) makes name MISS and
# this gate fires (exit 1), proving the eval reacts to broken CODE.
_VARIANT_NAME_P0 = """    failures = []
    for r in results:
        for f in r.get("fields", []):
            if f["field"] == "name" and f["status"] in ("MISS", "EXTRA", "MISMATCH"):
                failures.append({"rule_index": 0, "msg": "name p0 in %s" % r["case"]})
    return len(failures) == 0, failures
"""


def _scaffold_tree(target: Path, domain: str, dims: dict, primary_dim: str) -> Path:
    domain_config = {"normalizers": {}, "masks": {}}
    cmd = [sys.executable, str(_SCAFFOLD), "--target", str(target),
          "--domain", domain, "--strategy", "ground-truth",
          "--threshold", "70", "--production-module", "src/%s.py" % domain,
          "--p0-rules", "name must be non-null",
          "--dimensions", json.dumps(dims),
          "--primary-dimension", primary_dim,
          "--domain-config", json.dumps(domain_config),
          "--mirror-lang", "python", "--forge", "github"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, "scaffold failed: %s\\n%s" % (r.stdout, r.stderr)
    return target / "evals"


def _fill_tree(evals_dir: Path, domain: str, check_p0_gates_body: str) -> None:
    domain_dir = evals_dir / "eval_types" / domain
    with open(domain_dir / "pipeline_mirror.py", "a", encoding="utf-8") as f:
        f.write(_MIRROR_FILL)
    with open(domain_dir / "scorer.py", "a", encoding="utf-8") as f:
        f.write(_scorer_fill(check_p0_gates_body))


def _write_samples_and_gt(evals_dir: Path, domain: str, target: Path, case_matrix: list):
    samples = target / "data" / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    items = []
    for case in case_matrix:
        (samples / case["case"]).write_text(case["input"] + "\n", encoding="utf-8")
        items.append({"case_file": case["case"], "ground_truth": dict(case["expect"])})
    gt_path = evals_dir / "eval_types" / domain / "tests" / "production_fixtures" / "ground_truth.json"
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps({"description": "test", "items": items}, ensure_ascii=False),
                       encoding="utf-8")
    return samples, gt_path


def _write_full_card(target: Path, card: dict) -> Path:
    card_path = target / "_card.json"
    card_path.write_text(json.dumps(card), encoding="utf-8")
    r = subprocess.run([sys.executable, str(_CONFIG), "write", "--target", str(target),
                       "--card", str(card_path)], capture_output=True, text=True)
    assert r.returncode == 0, "eval_config write failed: %s\\n%s" % (r.stdout, r.stderr)
    return target / "evals" / "eval_config.json"


def _build_full_tree(target: Path, card: dict, check_p0_gates_body: str):
    domain = card["domain"]
    evals_dir = _scaffold_tree(target, domain, card["dimensions"], card["primary_dimension"])
    _fill_tree(evals_dir, domain, check_p0_gates_body)
    config_path = _write_full_card(target, card)
    samples, gt_path = _write_samples_and_gt(evals_dir, domain, target, card["case_matrix"])
    return {"evals_dir": evals_dir, "config_path": config_path, "samples": samples,
           "gt_path": gt_path}


def _generate(config_path: Path, out_path: Path):
    return _run(["generate", "--config", str(config_path), "--out", str(out_path)])


def _strip_code_layer(matrix_path: Path):
    """Drop the code layer from a generated matrix file so a test can exercise
    ONLY the classic p0/threshold/noise layers. The card still had to DECLARE a
    valid code_mutations pair for `generate` to pass the R3 coverage gate; this
    just keeps a non-code-focused run test from also executing the code vehicle
    (whose kill depends on the specific check_p0_gates body under test)."""
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["mutations"] = [m for m in matrix["mutations"] if m["layer"] != "code"]
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")


def _do_run(config_path, matrix_path, evals_dir, samples, gt_path, report_path=None):
    args = ["run", "--config", str(config_path), "--matrix", str(matrix_path),
           "--evals-root", str(evals_dir), "--sample-dir", str(samples),
           "--ground-truth", str(gt_path)]
    if report_path:
        args += ["--report", str(report_path)]
    return _run(args)


# ── generate: coverage / determinism ─────────────────────────────────────

def test_generate_coverage_fail_on_unkillable_rule(tmp_path):
    card = _base_card(p0_rules=[
        {"rule": "some untestable domain invariant", "source": "card"},
    ])
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 4, r.stderr
    assert "some untestable domain invariant" in r.stderr
    assert not (tmp_path / "matrix.json").exists()


def test_generate_full_coverage(tmp_path):
    card = _base_card(
        p0_rules=[
            {"rule": "name must be non-null", "source": "card",
             "target_axis": "name", "violation_value": None},
            {"rule": "email must be non-null", "source": "card",
             "target_axis": "email", "violation_value": None},
        ],
        dimensions={"accuracy": 100},
        epsilon={"maturity": 5, "accuracy": 2},
        case_matrix=[
            {"case": "c1.txt", "input": "name: Ann\nemail: ann@example.com\nnote: ok",
             "expect": {"name": "Ann", "email": "ann@example.com", "note": "ok"},
             "baseline": True},
            {"case": "c2.txt", "input": "name: Bob\nemail: bob@example.com\nnote: ok",
             "expect": {"name": "Bob", "email": "bob@example.com", "note": "ok"}},
        ],
    )
    config_path = _write_bare_config(tmp_path, card)
    out_path = tmp_path / "matrix.json"
    r = _generate(config_path, out_path)
    assert r.returncode == 0, r.stderr
    matrix = json.loads(out_path.read_text(encoding="utf-8"))

    expected_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert matrix["card_hash"] == expected_hash

    p0_mutations = [m for m in matrix["mutations"] if m["layer"] == "p0"]
    assert {m["kills"] for m in p0_mutations} == {"p0:0", "p0:1"}

    threshold_mutations = [m for m in matrix["mutations"] if m["layer"] == "threshold"]
    assert {m["kills"] for m in threshold_mutations} == {"axis:maturity", "axis:accuracy"}
    for axis in ("maturity", "accuracy"):
        pair = [m for m in threshold_mutations if m["kills"] == "axis:%s" % axis]
        assert {m["expected_exit"] for m in pair} == {0, 1}

    noise_mutations = [m for m in matrix["mutations"] if m["layer"] == "noise"]
    assert len(noise_mutations) >= 1
    assert all(m["expected_exit"] == 0 for m in noise_mutations)


def test_generate_deterministic(tmp_path):
    card = _base_card()
    config_path = _write_bare_config(tmp_path, card)
    out1 = tmp_path / "m1.json"
    out2 = tmp_path / "m2.json"
    r1 = _generate(config_path, out1)
    r2 = _generate(config_path, out2)
    assert r1.returncode == 0 and r2.returncode == 0
    assert out1.read_bytes() == out2.read_bytes()


# ── generate: code vehicle layer (Phase 2) ───────────────────────────────

def _code_mutations_pair():
    return [
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1},
    ]


def test_generate_emits_code_layer(tmp_path):
    card = _base_card(code_mutations=_code_mutations_pair())
    config_path = _write_bare_config(tmp_path, card)
    out_path = tmp_path / "matrix.json"
    r = _generate(config_path, out_path)
    assert r.returncode == 0, r.stderr
    matrix = json.loads(out_path.read_text(encoding="utf-8"))
    code = [m for m in matrix["mutations"] if m["layer"] == "code"]
    assert len(code) == 2
    assert all(m["vehicle"] == "code" for m in code)
    assert {m["kills"] for m in code} == {"code:mirror", "code:p0"}
    assert {m["id"] for m in code} == {"cm-mirror", "cm-p0"}


def test_generate_no_code_mutations_omits_layer(tmp_path):
    # a non-python card (F3 lane) with no code_mutations still generates the
    # 3 classic layers and no code layer -- no refuse (that is python-only).
    card = _base_card(mirror_lang="node", code_mutations=[])
    config_path = _write_bare_config(tmp_path, card)
    out_path = tmp_path / "matrix.json"
    r = _generate(config_path, out_path)
    assert r.returncode == 0, r.stderr
    matrix = json.loads(out_path.read_text(encoding="utf-8"))
    assert not [m for m in matrix["mutations"] if m["layer"] == "code"]
    assert [m for m in matrix["mutations"] if m["layer"] == "p0"]


def test_generate_code_deterministic(tmp_path):
    card = _base_card(code_mutations=_code_mutations_pair())
    config_path = _write_bare_config(tmp_path, card)
    out1 = tmp_path / "m1.json"
    out2 = tmp_path / "m2.json"
    r1 = _generate(config_path, out1)
    r2 = _generate(config_path, out2)
    assert r1.returncode == 0 and r2.returncode == 0
    assert out1.read_bytes() == out2.read_bytes()


def test_generate_code_preserves_patch(tmp_path):
    regex_patch = {"kind": "regex_sub", "anchor": "return out",
                   "replacement": "return {}", "count": 1}
    muts = [
        {"id": "cm-regex", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": regex_patch, "expected_exit": 1},
        # a p0-body entry so the mutation-proven coverage gate passes (R3)
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates", "value": [False, []]},
         "expected_exit": 1},
    ]
    card = _base_card(code_mutations=muts)
    config_path = _write_bare_config(tmp_path, card)
    out_path = tmp_path / "matrix.json"
    r = _generate(config_path, out_path)
    assert r.returncode == 0, r.stderr
    matrix = json.loads(out_path.read_text(encoding="utf-8"))
    mirror_entry = next(m for m in matrix["mutations"]
                        if m["layer"] == "code" and m["id"] == "cm-regex")
    assert mirror_entry["patch"] == regex_patch
    assert mirror_entry["target"] == "pipeline_mirror"


# ── generate: mutation-proven code coverage refuse (Phase 4, R3) ──────────

def test_generate_refuse_no_code_mutations(tmp_path):
    # a python card with NO code_mutations is not mutation-proven -> refuse (4)
    card = _base_card(code_mutations=[])
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 4, r.stderr
    assert "code coverage" in r.stderr.lower()
    assert not (tmp_path / "matrix.json").exists()


def test_generate_refuse_mirror_only(tmp_path):
    card = _base_card(code_mutations=[
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1}])
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 4, r.stderr
    assert "check_p0_gates" in r.stderr


def test_generate_refuse_p0_only(tmp_path):
    card = _base_card(code_mutations=[
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, []]},
         "expected_exit": 1}])
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 4, r.stderr
    assert "pipeline_mirror" in r.stderr


def test_generate_ok_both_present(tmp_path):
    card = _base_card(code_mutations=_code_mutations_pair())
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "matrix.json").exists()


def test_generate_nonpython_no_refuse(tmp_path):
    # F3 lang-guard: a non-python (subprocess-lane) card with no code_mutations
    # must NOT refuse -- the code vehicle is native-python only; other stacks
    # get a NOTE, not a hard refuse.
    card = _base_card(mirror_lang="node", code_mutations=[])
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "matrix.json").exists()
    assert "node" in r.stderr.lower() or "native" in r.stderr.lower()


def test_generate_refuse_nonpython_with_code_mutations(tmp_path):
    # a non-python card that DECLARES code_mutations must refuse: the code
    # vehicle only runs a native-python mirror, so those mutations would crash
    # at run time (pipeline_mirror.py does not exist for a node mirror).
    card = _base_card(mirror_lang="node", code_mutations=_code_mutations_pair())
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 4, r.stderr
    assert "python" in r.stderr.lower()
    assert not (tmp_path / "matrix.json").exists()


def test_hash_mismatch_refuses(tmp_path):
    card = _base_card(code_mutations=_code_mutations_pair())
    config_path = _write_bare_config(tmp_path, card)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(config_path, matrix_path).returncode == 0

    edited = copy.deepcopy(card)
    edited["threshold"] = 80
    body = _canonical_bytes(edited)
    config_path.write_bytes(body)
    (config_path.parent / "eval_config.sha256").write_text(
        hashlib.sha256(body).hexdigest() + "\\n", encoding="utf-8")

    r = _do_run(config_path, matrix_path, tmp_path / "nope-evals", tmp_path / "nope-samples",
               tmp_path / "nope-gt.json")
    assert r.returncode == 2, r.stderr
    assert "card_hash" in r.stderr or "drift" in r.stderr


# ── run: p0 kill / blind / panicky / rule attribution ────────────────────

def test_run_p0_mutation_kills(tmp_path):
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    _strip_code_layer(matrix_path)  # this test targets the p0-GT layer only

    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 0, "expected all mutations to match; stderr:\\n%s" % r.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["control_baseline_ok"] is True
    p0_result = next(x for x in report["results"] if x["kills"] == "p0:0")
    assert p0_result["ok"] is True
    assert p0_result["actual_exit"] == 1
    assert 0 in p0_result["rule_indices_fired"]


def test_run_detects_blind_gate(tmp_path):
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_BLIND)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    _strip_code_layer(matrix_path)  # the "blind" verdict must come from the p0-GT layer

    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"])
    assert r.returncode == 1, r.stdout
    assert "gate blind" in r.stderr


def test_run_detects_panicky_gate(tmp_path):
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_PANICKY)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    _strip_code_layer(matrix_path)  # the "panicky" verdict must come from the noise/GT layer

    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"])
    assert r.returncode == 1, r.stdout
    assert "gate panicky" in r.stderr


def test_mutation_trips_the_named_rule(tmp_path):
    card = _base_card(p0_rules=[
        {"rule": "name must be non-null", "source": "card",
         "target_axis": "name", "violation_value": None},
        {"rule": "name must not overflow", "source": "card",
         "target_axis": "name", "violation_value": "a-string-longer-than-ten-characters"},
    ])
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    _strip_code_layer(matrix_path)  # this test targets rule-id attribution only

    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 0, "each rule should trip only its own rule_index; stderr:\\n%s" % r.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    r0 = next(x for x in report["results"] if x["kills"] == "p0:0")
    r1 = next(x for x in report["results"] if x["kills"] == "p0:1")
    assert r0["rule_indices_fired"] == [0]
    assert r1["rule_indices_fired"] == [1]


# ── run: threshold +-epsilon (record vehicle) ────────────────────────────

def test_threshold_epsilon_pair(tmp_path):
    card = _base_card(epsilon={"maturity": 5})
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0

    full_matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    threshold_only = dict(full_matrix)
    threshold_only["mutations"] = [m for m in full_matrix["mutations"] if m["layer"] == "threshold"]
    assert len(threshold_only["mutations"]) == 2
    filtered_path = tmp_path / "matrix-threshold.json"
    filtered_path.write_text(json.dumps(threshold_only), encoding="utf-8")

    r = _do_run(tree["config_path"], filtered_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 0, r.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    minus = next(x for x in report["results"] if x["id"] == "threshold-maturity-minus")
    plus = next(x for x in report["results"] if x["id"] == "threshold-maturity-plus")
    assert minus["actual_exit"] == 1 and minus["ok"] is True
    assert plus["actual_exit"] == 0 and plus["ok"] is True


def test_threshold_multidim_rounding_no_false_blind(tmp_path):
    # Equal-weight multi-dimension card + tight epsilon: scorer.score() rounds
    # EACH dimension before summing, so a naive threshold-epsilon target rounds
    # back up to the threshold and a CORRECT gate (which passes) gets falsely
    # flagged 'blind'. The minus target must produce an ACTUAL maturity strictly
    # below the threshold so a correct gate blocks (exit 1) as expected.
    card = _base_card(dimensions={"a": 50, "b": 50}, primary_dimension="a",
                      threshold=80, epsilon={"maturity": 0.1})
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0

    full_matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    threshold_only = dict(full_matrix)
    threshold_only["mutations"] = [m for m in full_matrix["mutations"] if m["layer"] == "threshold"]
    filtered_path = tmp_path / "matrix-threshold.json"
    filtered_path.write_text(json.dumps(threshold_only), encoding="utf-8")

    r = _do_run(tree["config_path"], filtered_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    minus = next(x for x in report["results"] if x["id"] == "threshold-maturity-minus")
    plus = next(x for x in report["results"] if x["id"] == "threshold-maturity-plus")
    assert minus["ok"] is True, "correct gate falsely flagged 'blind' by rounding-blind minus target"
    assert plus["ok"] is True
    assert r.returncode == 0, r.stderr


def test_threshold_zero_skips_unsatisfiable_minus(tmp_path):
    # threshold==0: no fixed score can push maturity strictly below 0, so the
    # minus probe has no true just-fail boundary -- it must be skipped rather
    # than emitted and then falsely flagged when the correct gate passes.
    card = _base_card(threshold=0, epsilon={"maturity": 0.1})
    config_path = _write_bare_config(tmp_path, card)
    assert _generate(config_path, tmp_path / "matrix.json").returncode == 0
    matrix = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    ids = {m["id"] for m in matrix["mutations"] if m["layer"] == "threshold"}
    assert "threshold-maturity-minus" not in ids
    assert "threshold-maturity-plus" in ids


def test_generate_warns_when_noise_layer_empty(tmp_path):
    # A card with no non-P0 string field yields zero noise mutations; the
    # 'gate does not panic on noise' layer is then silently absent -- generate
    # must WARN rather than report success as if the layer were exercised.
    card = _base_card(
        p0_rules=[{"rule": "amount must be non-null", "source": "card",
                   "target_axis": "amount", "violation_value": None}],
        case_matrix=[
            {"case": "c1.txt", "input": "amount: 100", "expect": {"amount": 100}, "baseline": True},
            {"case": "c2.txt", "input": "amount: 200", "expect": {"amount": 200}},
        ],
    )
    config_path = _write_bare_config(tmp_path, card)
    r = _generate(config_path, tmp_path / "matrix.json")
    assert r.returncode == 0, r.stderr
    assert "noise-layer" in r.stderr
    matrix = json.loads((tmp_path / "matrix.json").read_text(encoding="utf-8"))
    assert not [m for m in matrix["mutations"] if m["layer"] == "noise"]


def test_gt_patch_missing_case_is_input_error_not_crash(tmp_path):
    # A gt mutation whose target case is absent from ground_truth is a card/gt
    # naming misalignment (setup error) -> clean exit 2, not an uncaught
    # ValueError traceback + exit 1 (which the contract reserves for gate
    # mismatch).
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["mutations"] = [{"id": "gt-ghost", "layer": "noise", "vehicle": "gt",
                            "kills": None, "expected_exit": 0,
                            "patch": {"case": "ghost.txt", "field": "name", "value": "  x  "}}]
    bad_path = tmp_path / "matrix-bad.json"
    bad_path.write_text(json.dumps(matrix), encoding="utf-8")

    r = _do_run(tree["config_path"], bad_path, tree["evals_dir"], tree["samples"], tree["gt_path"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    assert "ghost.txt" in r.stderr


# ── run: env scrub ────────────────────────────────────────────────────────

def test_env_scrubbed_in_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_FOO", "leak-probe")
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0

    report_path = tmp_path / "report.json"
    _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
           tree["gt_path"], report_path=report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "HARNESS_FOO" in report["meta"]["harness_keys_in_caller"]
    assert report["meta"]["harness_keys_in_subprocess"] == []


# ── run: code vehicle (Phase 3 — patch SOURCE, run the real eval) ─────────

def test_run_code_mirror_kills(tmp_path):
    # break run_pipeline -> {} in a temp copy; the eval must go red (name MISS
    # trips the P0 gate). expected_exit=1 matches -> ok:true.
    card = _base_card(code_mutations=[
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1}])
    tree = _build_full_tree(tmp_path, card, _VARIANT_NAME_P0)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 0, "stderr:\n%s" % r.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["control_baseline_ok"] is True
    cm = next(x for x in report["results"] if x["id"] == "cm-mirror")
    assert cm["layer"] == "code" and cm["vehicle"] == "code"
    assert cm["ok"] is True
    assert cm["actual_exit"] == 1


def test_run_code_p0_kills(tmp_path):
    # break check_p0_gates -> always BLOCK in a temp copy; a clean run (real
    # gate) passes (control exit 0) and the neutered gate flips it to red
    # (exit 1) -> the p0 gate is proven load-bearing (verdict flips both ways).
    card = _base_card(code_mutations=[
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1}])
    tree = _build_full_tree(tmp_path, card, _VARIANT_NAME_P0)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 0, "stderr:\n%s" % r.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["control_baseline_ok"] is True   # clean gate -> exit 0
    cm = next(x for x in report["results"] if x["id"] == "cm-p0")
    assert cm["ok"] is True
    assert cm["actual_exit"] == 1                    # neutered gate -> exit 1 (flip)


def test_run_code_blind_gate_flagged(tmp_path):
    # a HARMLESS source patch (mirror still correct) with expected_exit=1 must
    # be flagged MISMATCH/"blind" -- proof the runner actually runs the eval
    # instead of blind-trusting the declared expectation.
    card = _base_card(code_mutations=[
        {"id": "cm-noop", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "regex_sub", "anchor": r"def run_pipeline\(input_data\):",
                   "replacement": "def run_pipeline(input_data):  # harmless", "count": 1},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1}])
    tree = _build_full_tree(tmp_path, card, _VARIANT_NAME_P0)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"], report_path=tmp_path / "report.json")
    assert r.returncode == 1, "a harmless patch under expected_exit=1 must MISMATCH"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    cm = next(x for x in report["results"] if x["id"] == "cm-noop")
    assert cm["ok"] is False
    assert cm["actual_exit"] == 0                    # mirror still correct -> green
    assert "cm-noop" in report["mismatches"]


def test_run_code_tree_immutable(tmp_path):
    # R-A leak guard: the ORIGINAL source files must be byte-identical before
    # and after a full code-vehicle run (all patching happens in temp copies).
    card = _base_card(code_mutations=[
        {"id": "cm-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "cm-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates",
                   "value": [False, [{"rule_index": 0, "msg": "forced"}]]},
         "expected_exit": 1}])
    tree = _build_full_tree(tmp_path, card, _VARIANT_NAME_P0)
    domain_dir = tree["evals_dir"] / "eval_types" / card["domain"]
    sources = ("pipeline_mirror.py", "scorer.py")

    def _hashes():
        return {s: hashlib.sha256((domain_dir / s).read_bytes()).hexdigest() for s in sources}

    before = _hashes()
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
           tree["gt_path"], report_path=tmp_path / "report.json")
    assert _hashes() == before, "code vehicle leaked a patch into the original tree"


def test_run_missing_ground_truth_is_input_error(tmp_path):
    # a missing/unreadable ground_truth makes the control baseline gt run raise
    # OSError -> must be a setup error (exit 2), not a false exit-1 verdict.
    card = _base_card()
    tree = _build_full_tree(tmp_path, card, _VARIANT_REAL)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tmp_path / "no-such-gt.json")
    assert r.returncode == 2, "a missing ground truth is a setup error (exit 2)"
    assert "Traceback" not in r.stderr


def test_apply_code_patch_malformed_source_raises_setup_error():
    # symbol_return ast.parse on a syntactically broken source raises
    # SyntaxError -- which MUST be classed as a setup error so cmd_run maps it
    # to exit 2, not a false exit-1 gate verdict.
    mm = _load_mm()
    with pytest.raises(SyntaxError):
        mm._apply_code_patch("def check_p0_gates(:\n    broken\n",
                             {"kind": "symbol_return", "symbol": "check_p0_gates",
                              "value": [False, []]})
    assert SyntaxError in mm._SETUP_ERRORS


def test_run_code_invalid_regex_is_input_error_not_gate_verdict(tmp_path):
    # DEFENSE IN DEPTH: an invalid regex is normally rejected at R7, but if a
    # matrix somehow carries one at run time it raises re.error -> that is a
    # SETUP error (exit 2), NEVER exit 1 (which would be misread as "gate
    # blind"). Inject it into the generated matrix to bypass the R7 gate.
    card = _base_card(code_mutations=_code_mutations_pair())
    tree = _build_full_tree(tmp_path, card, _VARIANT_NAME_P0)
    matrix_path = tmp_path / "matrix.json"
    assert _generate(tree["config_path"], matrix_path).returncode == 0
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    for m in matrix["mutations"]:
        if m.get("id") == "cm-mirror":
            m["patch"] = {"kind": "regex_sub", "anchor": "[unclosed(",
                          "replacement": "x", "count": 1}
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")
    r = _do_run(tree["config_path"], matrix_path, tree["evals_dir"], tree["samples"],
               tree["gt_path"])
    assert r.returncode == 2, "a bad regex is a setup error (exit 2), not a gate mismatch"
    assert "Traceback" not in r.stderr
