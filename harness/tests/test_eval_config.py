"""Contract for the strategy-card persistence CLI (eval_config.py).

A user-approved strategy card is not allowed to live only in the conversation
(L4/card-hash lock, plan.md §0.1/§0.5): `write` validates the card's SHAPE
(reusing eval_scaffold's judgment-field validators), then persists it as
canonical JSON + a sha256 sidecar so every later turn can `verify` the pair
still matches. These tests pin: the write/verify/show/hash exit-code
contract, canonical-JSON determinism regardless of input key order, the
epsilon["maturity"] requirement, atomic-write (no torn file on a mid-write
crash), actionable schema errors, the self-target fence, unicode roundtrip,
and the strategy-conditional judge/pipeline_model nullability.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys

import pytest

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "eval_config.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_config", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args, input_text=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True, text=True, input=input_text)


def _sample_card(**overrides):
    """A minimal, valid `contract`-strategy card — every field the schema
    requires, judge/pipeline_model left null (contract strategy needs neither).
    Tests override one field at a time to probe its validation in isolation."""
    card = {
        "schema_version": "1.0",
        "domain": "cv_extraction",
        "strategy": "contract",
        "surface": "extraction",
        "production_module": "src/cv_extraction.py",
        "production_entry": "extract",
        "mirror_lang": "python",
        "mirror_invoke": None,
        "forge": "github",
        "threshold": 70,
        "p0_rules": [
            {"rule": "name must be non-null", "source": "code:src/cv_extraction.py:42",
             "target_axis": "accuracy", "violation_value": None},
        ],
        "dimensions": {"accuracy": 60, "completeness": 40},
        "primary_dimension": "accuracy",
        "judge_model": None,
        "pipeline_model": None,
        "domain_config": {"normalizers": {"phone": "phone_vi"}, "masks": {"email": "email"}},
        "case_matrix": [
            {"case": "case-1", "input": "raw text", "expect": {"name": "A"}},
        ],
        "epsilon": {"maturity": 0.1},
        "cited_lessons": [],
        "approved_by": "user:hieubt15",
        "approved_ts": "2026-07-14T10:00:00Z",
    }
    card.update(overrides)
    return card


def _write_card(path, card):
    path.write_text(json.dumps(card), encoding="utf-8")
    return path


# --- write/verify roundtrip + tamper detection --------------------------------

def test_write_verify_roundtrip(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    card_path = _write_card(tmp_path / "card.json", _sample_card())

    result = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert result.returncode == 0, result.stderr

    json_path = target / "evals" / "eval_config.json"
    sha_path = target / "evals" / "eval_config.sha256"
    assert json_path.is_file()
    assert sha_path.is_file()

    ok = _run(["verify", "--target", str(target)])
    assert ok.returncode == 0, ok.stderr

    # flip one byte inside a string value — stays valid JSON, hash must mismatch
    data = json_path.read_bytes()
    mutated = data.replace(b"cv_extraction", b"cw_extraction", 1)
    assert mutated != data
    json_path.write_bytes(mutated)
    tampered = _run(["verify", "--target", str(target)])
    assert tampered.returncode == 1
    combined = (tampered.stdout + tampered.stderr).lower()
    assert "expected" in combined and "actual" in combined

    # restore, then remove the sidecar entirely
    json_path.write_bytes(data)
    sha_path.unlink()
    missing = _run(["verify", "--target", str(target)])
    assert missing.returncode == 2


def test_canonical_determinism(tmp_path):
    card_a = _sample_card()
    # reverse key order at every dict level (recursively) — same content,
    # different insertion order; canonical dump must still be byte-identical
    def _reorder(obj):
        if isinstance(obj, dict):
            return {k: _reorder(obj[k]) for k in reversed(list(obj.keys()))}
        if isinstance(obj, list):
            return [_reorder(v) for v in obj]
        return obj

    card_b = _reorder(card_a)

    target_a = tmp_path / "a"
    target_b = tmp_path / "b"
    target_a.mkdir()
    target_b.mkdir()
    card_a_path = _write_card(tmp_path / "card_a.json", card_a)
    card_b_path = _write_card(tmp_path / "card_b.json", card_b)

    ra = _run(["write", "--target", str(target_a), "--card", str(card_a_path)])
    rb = _run(["write", "--target", str(target_b), "--card", str(card_b_path)])
    assert ra.returncode == 0, ra.stderr
    assert rb.returncode == 0, rb.stderr

    bytes_a = (target_a / "evals" / "eval_config.json").read_bytes()
    bytes_b = (target_b / "evals" / "eval_config.json").read_bytes()
    assert bytes_a == bytes_b


def test_epsilon_maturity_required_with_threshold(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    card = _sample_card(epsilon={"accuracy": 5})
    card_path = _write_card(tmp_path / "card.json", card)

    result = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert result.returncode == 2
    assert "maturity" in result.stderr.lower()
    assert not (target / "evals").exists()


def test_atomic_write_no_torn_file(tmp_path, monkeypatch):
    ec = _load()
    target = tmp_path / "target"
    target.mkdir()

    def _boom(*a, **kw):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(ec.os, "replace", _boom)
    rc = ec.cmd_write(str(target), json.dumps(_sample_card()))
    assert rc == 2
    json_path = target / "evals" / "eval_config.json"
    assert not json_path.exists(), "destination must stay absent on a mid-write crash"
    leftover = list((target / "evals").glob("*")) if (target / "evals").exists() else []
    assert all(name.suffix == ".tmp" for name in leftover), \
        "only a stray .tmp may survive a crash, never a torn destination"


def test_schema_validation_actionable(tmp_path):
    target = tmp_path / "target"
    target.mkdir()

    cases = []
    missing_case_matrix = _sample_card()
    del missing_case_matrix["case_matrix"]
    cases.append((missing_case_matrix, "case_matrix"))

    cases.append((_sample_card(p0_rules=[]), "p0_rules"))
    cases.append((_sample_card(p0_rules=[{"rule": "x"}]), "source"))
    cases.append((_sample_card(dimensions={"accuracy": 50, "completeness": 40}), "dimensions"))

    missing_approved_by = _sample_card()
    del missing_approved_by["approved_by"]
    cases.append((missing_approved_by, "approved_by"))

    for i, (card, needle) in enumerate(cases):
        card_path = _write_card(tmp_path / ("bad_%d.json" % i), card)
        result = _run(["write", "--target", str(target), "--card", str(card_path)])
        assert result.returncode == 2, "case %d (%s) should fail schema validation" % (i, needle)
        assert needle in result.stderr, \
            "case %d: expected %r named in stderr, got %r" % (i, needle, result.stderr)
    assert not (target / "evals").exists()


def test_self_target_fence(tmp_path):
    # a REAL harness tree (not just a dir named "harness") trips the fence —
    # a bare "harness" dir is a common name in an ordinary user repo and must
    # be allowed (see eval_scaffold.is_self_target)
    (tmp_path / "harness" / "plugins" / "hs" / "skills").mkdir(parents=True)
    card_path = _write_card(tmp_path / "card.json", _sample_card())
    result = _run(["write", "--target", str(tmp_path), "--card", str(card_path)])
    assert result.returncode == 2
    assert not (tmp_path / "evals").exists()


def test_unicode_body_roundtrip(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    diacritics = "Bài học: chuẩn hoá số điện thoại VN — ưu tiên độ chính xác"
    card = _sample_card(
        p0_rules=[{"rule": diacritics, "source": "memory:vn-phone-lesson",
                   "target_axis": "accuracy", "violation_value": None}],
    )
    card_path = _write_card(tmp_path / "card.json", card)

    result = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert result.returncode == 0, result.stderr

    json_path = target / "evals" / "eval_config.json"
    body = json_path.read_text(encoding="utf-8")
    assert diacritics in body, "ensure_ascii=False must keep diacritics literal, not \\u-escaped"
    assert "\\u" not in body


def test_strategy_conditional_nullability(tmp_path):
    target_ok = tmp_path / "ok"
    target_ok.mkdir()
    contract_card = _write_card(tmp_path / "contract.json", _sample_card())
    ok = _run(["write", "--target", str(target_ok), "--card", str(contract_card)])
    assert ok.returncode == 0, ok.stderr

    target_judge = tmp_path / "judge"
    target_judge.mkdir()
    judge_missing = _sample_card(strategy="judge", pipeline_model="haiku")
    judge_path = _write_card(tmp_path / "judge.json", judge_missing)
    bad_judge = _run(["write", "--target", str(target_judge), "--card", str(judge_path)])
    assert bad_judge.returncode == 2
    assert "judge_model" in bad_judge.stderr

    target_no_dims = tmp_path / "no_dims"
    target_no_dims.mkdir()
    no_dims = _sample_card()
    del no_dims["dimensions"]
    no_dims_path = _write_card(tmp_path / "no_dims.json", no_dims)
    bad_dims = _run(["write", "--target", str(target_no_dims), "--card", str(no_dims_path)])
    assert bad_dims.returncode == 2
    assert "dimensions" in bad_dims.stderr


# --- code_mutations schema (Phase 1: code-vehicle card field, optional) -------

def _valid_code_mutations():
    """Two well-formed entries — one per DP-2 target — using the main
    symbol_return patch kind (name-anchored, no re-hash coupling)."""
    return [
        {"id": "m-mirror", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1},
        {"id": "m-p0", "target": "check_p0_gates", "kills": "code:p0",
         "patch": {"kind": "symbol_return", "symbol": "check_p0_gates", "value": [True, []]},
         "expected_exit": 1},
    ]


def _write_and_validate(tmp_path, name, card):
    target = tmp_path / name
    target.mkdir()
    card_path = _write_card(tmp_path / (name + ".json"), card)
    return _run(["write", "--target", str(target), "--card", str(card_path)])


def test_code_mutations_optional(tmp_path):
    # a card WITHOUT code_mutations still validates — back-compat, every old card holds
    r = _write_and_validate(tmp_path, "opt", _sample_card())
    assert r.returncode == 0, r.stderr


def test_code_mutations_valid_accepts(tmp_path):
    r = _write_and_validate(tmp_path, "ok", _sample_card(code_mutations=_valid_code_mutations()))
    assert r.returncode == 0, r.stderr


def test_code_mutations_bad_target(tmp_path):
    muts = _valid_code_mutations()
    muts[0]["target"] = "scorer"
    r = _write_and_validate(tmp_path, "bad_target", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "code_mutations" in r.stderr and "target" in r.stderr


def test_code_mutations_missing_patch_kind(tmp_path):
    muts = _valid_code_mutations()
    del muts[0]["patch"]["kind"]
    r = _write_and_validate(tmp_path, "no_kind", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "code_mutations" in r.stderr and "kind" in r.stderr


def test_code_mutations_dup_id(tmp_path):
    muts = _valid_code_mutations()
    muts[1]["id"] = muts[0]["id"]
    r = _write_and_validate(tmp_path, "dup", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "code_mutations" in r.stderr and "id" in r.stderr


def test_code_mutations_not_list(tmp_path):
    r = _write_and_validate(tmp_path, "not_list", _sample_card(code_mutations={}))
    assert r.returncode == 2
    assert "code_mutations" in r.stderr


def test_code_mutations_regex_sub_requires_anchor_replacement(tmp_path):
    # a regex_sub patch missing anchor/replacement must be rejected at R7, not
    # crash later at run time (KeyError escaping as a false exit-1 verdict)
    muts = [
        {"id": "m", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "regex_sub"}, "expected_exit": 1}]
    r = _write_and_validate(tmp_path, "rx_bad", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "code_mutations" in r.stderr and ("anchor" in r.stderr or "replacement" in r.stderr)


def test_code_mutations_regex_sub_count_must_be_int(tmp_path):
    muts = [
        {"id": "m", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "regex_sub", "anchor": "x", "replacement": "y", "count": "1"},
         "expected_exit": 1}]
    r = _write_and_validate(tmp_path, "rx_cnt", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "count" in r.stderr


def test_code_mutations_id_must_be_path_safe(tmp_path):
    # the id is interpolated into a temp path (code-<id>/); a traversal id must
    # be rejected at R7, never reach shutil.copytree.
    muts = [
        {"id": "../../leak", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline", "value": {}},
         "expected_exit": 1}]
    r = _write_and_validate(tmp_path, "badid", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "id" in r.stderr


def test_code_mutations_regex_sub_rejects_invalid_pattern(tmp_path):
    # an anchor that is a non-empty string but not a valid regex must be
    # rejected at R7, not blow up at run time.
    muts = [
        {"id": "m", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "regex_sub", "anchor": "[unclosed(", "replacement": "y", "count": 1},
         "expected_exit": 1}]
    r = _write_and_validate(tmp_path, "rx_inv", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "anchor" in r.stderr and ("regex" in r.stderr.lower() or "pattern" in r.stderr.lower())


def test_code_mutations_symbol_return_rejects_nonfinite_value(tmp_path):
    # NaN/Infinity round-trips through repr() to a bare name (nan/inf) that would
    # NameError inside the patched source at run time -> reject at R7.
    muts = [
        {"id": "m", "target": "pipeline_mirror", "kills": "code:mirror",
         "patch": {"kind": "symbol_return", "symbol": "run_pipeline",
                   "value": float("nan")}, "expected_exit": 1}]
    r = _write_and_validate(tmp_path, "nan_val", _sample_card(code_mutations=muts))
    assert r.returncode == 2
    assert "value" in r.stderr and "finite" in r.stderr.lower()


# --- Tests After ---------------------------------------------------------------

def test_verify_is_read_only(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    card_path = _write_card(tmp_path / "card.json", _sample_card())
    result = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert result.returncode == 0, result.stderr

    json_path = target / "evals" / "eval_config.json"
    sha_path = target / "evals" / "eval_config.sha256"
    before = (json_path.read_bytes(), sha_path.read_bytes(),
              json_path.stat().st_mtime_ns, sha_path.stat().st_mtime_ns)

    verify_result = _run(["verify", "--target", str(target)])
    assert verify_result.returncode == 0

    after = (json_path.read_bytes(), sha_path.read_bytes(),
              json_path.stat().st_mtime_ns, sha_path.stat().st_mtime_ns)
    assert before == after


def test_show_p0_line_derivation(tmp_path):
    ec = _load()
    target = tmp_path / "target"
    target.mkdir()
    card = _sample_card(p0_rules=[
        {"rule": "name must be non-null", "source": "code:src/x.py:1"},
        {"rule": "dob must parse as a date", "source": "data:sample.csv"},
    ])
    card_path = _write_card(tmp_path / "card.json", card)
    result = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert result.returncode == 0, result.stderr

    show = _run(["show", "--target", str(target), "--p0-line"])
    assert show.returncode == 0, show.stderr
    line = show.stdout.strip()
    assert line == "name must be non-null; dob must parse as a date"

    sys.path.insert(0, str(_SCRIPT.parent))
    try:
        import eval_scaffold
        eval_scaffold._validate_p0_rules(line)  # must not raise
    finally:
        sys.path.pop(0)


def test_write_from_stdin(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    card_json = json.dumps(_sample_card())

    result = _run(["write", "--target", str(target), "--card", "-"], input_text=card_json)
    assert result.returncode == 0, result.stderr
    assert (target / "evals" / "eval_config.json").is_file()

    json_path = target / "evals" / "eval_config.json"
    body = json_path.read_bytes()
    sha_path = target / "evals" / "eval_config.sha256"
    expected = hashlib.sha256(body).hexdigest()
    assert sha_path.read_text(encoding="utf-8").strip() == expected


# --- emit-mirror-contract (P21: contract generated FROM the verified card) ----

def _write_and_emit(tmp_path, target_name, card, extra_args=None):
    target = tmp_path / target_name
    target.mkdir()
    card_path = _write_card(tmp_path / (target_name + "-card.json"), card)
    written = _run(["write", "--target", str(target), "--card", str(card_path)])
    assert written.returncode == 0, written.stderr
    emitted = _run(["emit-mirror-contract", "--target", str(target)] + (extra_args or []))
    return target, emitted


def test_emit_contract_from_card(tmp_path):
    card = _sample_card(domain="cv_extraction", mirror_lang="node")
    target, emitted = _write_and_emit(tmp_path, "t", card)
    assert emitted.returncode == 0, emitted.stderr

    contract_path = target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json"
    assert contract_path.is_file()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    sha_path = target / "evals" / "eval_config.sha256"
    expected_hash = "sha256:" + sha_path.read_text(encoding="utf-8").strip()
    assert contract["card_hash"] == expected_hash
    assert contract["lang"] == "node"
    assert contract["invoke"]["argv_template"] == ["node", "{mirror_path}"]
    assert contract["schema_version"] == "1.0"
    assert contract["timeout_seconds"] == 10
    assert contract["zero_network"] is True
    assert contract["deterministic"] is True


def test_emit_refuses_on_drift(tmp_path):
    card = _sample_card(mirror_lang="node")
    target, emitted = _write_and_emit(tmp_path, "t", card)
    assert emitted.returncode == 0, emitted.stderr

    json_path = target / "evals" / "eval_config.json"
    data = json_path.read_bytes()
    mutated = data.replace(b"cv_extraction", b"cw_extraction", 1)
    assert mutated != data
    json_path.write_bytes(mutated)

    drifted = _run(["emit-mirror-contract", "--target", str(target)])
    assert drifted.returncode in (1, 2), drifted.stderr
    contract_path = target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json"
    # the ORIGINAL contract from before the drift may exist; a re-emit under
    # drift must not overwrite it with anything derived from the tampered card
    if contract_path.is_file():
        stale = json.loads(contract_path.read_text(encoding="utf-8"))
        assert stale["lang"] == "node"


def test_unknown_lang_requires_card_invoke(tmp_path):
    no_invoke = _sample_card(mirror_lang="zig", mirror_invoke=None)
    target, emitted = _write_and_emit(tmp_path, "no_invoke", no_invoke)
    assert emitted.returncode == 2, emitted.stdout + emitted.stderr
    combined = (emitted.stdout + emitted.stderr).lower()
    assert "mirror_invoke" in combined
    assert "r7" in combined
    assert not (target / "evals" / "eval_types").exists()

    with_invoke = _sample_card(
        mirror_lang="zig",
        mirror_invoke={"argv_template": ["zig", "run", "{mirror_path}"]},
    )
    target2, emitted2 = _write_and_emit(tmp_path, "with_invoke", with_invoke)
    assert emitted2.returncode == 0, emitted2.stderr
    contract = json.loads(
        (target2 / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json")
        .read_text(encoding="utf-8"))
    assert contract["invoke"]["argv_template"] == ["zig", "run", "{mirror_path}"]


def test_output_schema_derived_from_cases(tmp_path):
    card = _sample_card(case_matrix=[
        {"case": "case-1", "input": "raw text", "expect": {"a": 1, "b": 2}},
        {"case": "case-2", "input": "raw text 2", "expect": {"b": 3, "c": 4}},
    ])
    target, emitted = _write_and_emit(tmp_path, "t", card)
    assert emitted.returncode == 0, emitted.stderr
    contract = json.loads(
        (target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json")
        .read_text(encoding="utf-8"))
    assert contract["output_schema"] == {
        "type": "object",
        "keys_from_dimensions_and_cases": ["a", "b", "c"],
    }


def test_contract_deterministic(tmp_path):
    card = _sample_card()
    target, first = _write_and_emit(tmp_path, "t", card)
    assert first.returncode == 0, first.stderr
    contract_path = target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json"
    bytes_1 = contract_path.read_bytes()

    second = _run(["emit-mirror-contract", "--target", str(target)])
    assert second.returncode == 0, second.stderr
    bytes_2 = contract_path.read_bytes()
    assert bytes_1 == bytes_2


def test_contract_has_mirror_filename(tmp_path):
    for lang, ext in (("python", "py"), ("node", "js"), ("go", "go")):
        card = _sample_card(mirror_lang=lang)
        target, emitted = _write_and_emit(tmp_path, "lang_" + lang, card)
        assert emitted.returncode == 0, emitted.stderr
        contract = json.loads(
            (target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json")
            .read_text(encoding="utf-8"))
        assert contract["mirror_filename"] == "pipeline_mirror.%s" % ext


def test_python_lang_contract_matches_native(tmp_path):
    card = _sample_card(mirror_lang="python")
    target, emitted = _write_and_emit(tmp_path, "t", card)
    assert emitted.returncode == 0, emitted.stderr
    contract = json.loads(
        (target / "evals" / "eval_types" / "cv_extraction" / "mirror_contract.json")
        .read_text(encoding="utf-8"))
    assert contract["lang"] == "python"
    assert contract["mirror_filename"] == "pipeline_mirror.py"
    assert contract["invoke"]["argv_template"] == ["python3", "{mirror_path}"]
