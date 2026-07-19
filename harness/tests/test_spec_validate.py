"""hs:spec — the ~25-check structural validate suite (check_traceability,
check_consistency + its TIME/RISK/COMPETITION/PRODUCT/schema siblings,
check_fence, build_traceability_matrix, time_realism_anchors).

Script-vs-LLM split (validation-rules-spec.md:5-12): every check here is
script-owned (parse/graph/count/enum). LLM-judgment checks are out of this
phase's scope (references-only scaffold; no executable LLM code).
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_NAMES = [
    "encoding_utils", "id_grammar", "frontmatter_parser", "spec_graph", "dec_ledger",
    "check_consistency_schema", "check_consistency_time", "check_consistency_risk",
    "check_consistency_competition", "session_staleness", "check_consistency_product",
    "check_consistency", "check_traceability", "check_fence", "build_traceability_matrix",
    "time_realism_anchors", "competitive_drift_anchors", "time_advisory", "strict_gate",
]
_mods = load_skill_scripts(_SPEC_SCRIPTS, _NAMES)
spec_graph = _mods["spec_graph"]
check_traceability = _mods["check_traceability"]
check_consistency = _mods["check_consistency"]
check_fence = _mods["check_fence"]
build_traceability_matrix = _mods["build_traceability_matrix"]
time_realism_anchors = _mods["time_realism_anchors"]
time_advisory = _mods["time_advisory"]
competitive_drift_anchors = _mods["competitive_drift_anchors"]
session_staleness = _mods["session_staleness"]
strict_gate = _mods["strict_gate"]
check_consistency_product = _mods["check_consistency_product"]
dec_ledger = _mods["dec_ledger"]
encoding_utils = _mods["encoding_utils"]


# ---------------------------------------------------------------------------
# import smoke: every module in the check family loads clean (Risks: import-
# smoke test — sub-module cross-imports must resolve skill-local).
# ---------------------------------------------------------------------------

def test_all_check_modules_import_clean():
    for name in _NAMES:
        assert _mods[name] is not None


def test_check_consistency_carries_no_dead_reexports():
    # RISK_HIGH_RATIO / RISK_BLINDSPOT_MIN_STORIES / DEPENDS_ON_ALLOWED_TYPES /
    # COMPETITIVE_PARITY_ALLOWED_TYPES / COMP_ID_PATTERN / COMPETITION_ENUMS
    # used to be imported into check_consistency.py and never used or
    # re-exported — every genuine consumer already imports them from their
    # real sibling home (check_consistency_risk / _time / _competition).
    src = (_SPEC_SCRIPTS / "check_consistency.py").read_text(encoding="utf-8")
    for dead in (
        "RISK_HIGH_RATIO", "RISK_BLINDSPOT_MIN_STORIES", "DEPENDS_ON_ALLOWED_TYPES",
        "COMPETITIVE_PARITY_ALLOWED_TYPES", "COMP_ID_PATTERN", "COMPETITION_ENUMS",
    ):
        assert dead not in src, f"{dead} is a dead import in check_consistency.py"


# ---------------------------------------------------------------------------
# orphan_story / dangling_link (check_traceability)
# ---------------------------------------------------------------------------

def test_write_text_atomic_no_inplace_truncation(tmp_path, monkeypatch):
    # A fixed-path artifact (e.g. the traceability matrix) read by a human/CI
    # while a re-render is in flight must never be observed truncated. The atomic
    # writer copies to a same-dir temp then os.replace()s. Proof: force the swap
    # to fail; the existing file is left fully intact, never truncated.
    target = tmp_path / "sub" / "matrix.md"
    target.parent.mkdir(parents=True)
    target.write_text("OLD-COMPLETE-CONTENT", encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("swap failed")

    monkeypatch.setattr(encoding_utils.os, "replace", _boom)
    with pytest.raises(OSError):
        encoding_utils.write_text_atomic(target, "NEW" * 1000)
    assert target.read_text(encoding="utf-8") == "OLD-COMPLETE-CONTENT"
    # no orphaned temp left behind on the failed swap
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]


def test_write_text_atomic_writes_and_creates_parent(tmp_path):
    target = tmp_path / "a" / "b" / "matrix.md"
    encoding_utils.write_text_atomic(target, "hello\nworld\n")
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]


def test_write_text_atomic_preserves_existing_mode(tmp_path):
    # mkstemp creates its temp at 0600 (stdlib default) and os.replace carries
    # that mode onto the target — so without a chmod, every write would silently
    # downgrade an existing file to owner-only, unlike the plain write_text it
    # replaces (which keeps the existing file's mode). A human/CI-read artifact
    # must not lose group/other read on its first atomic rewrite.
    import os as _os
    import stat as _stat
    target = tmp_path / "m.md"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)
    encoding_utils.write_text_atomic(target, "new")
    assert _stat.S_IMODE(_os.stat(target).st_mode) == 0o644


def test_write_text_atomic_new_file_respects_umask(tmp_path):
    # A newly created file must follow the process umask like a plain write_text
    # (0o666 & ~umask), not mkstemp's fixed 0600.
    import os as _os
    import stat as _stat
    cur = _os.umask(0o022)
    _os.umask(cur)
    target = tmp_path / "n.md"
    encoding_utils.write_text_atomic(target, "x")
    assert _stat.S_IMODE(_os.stat(target).st_mode) == (0o666 & ~cur)


def test_write_text_atomic_new_file_does_not_mutate_umask(tmp_path, monkeypatch):
    # The new-file mode must come from a value captured ONCE at import, not a
    # per-call os.umask(0)/restore dance — that dance is process-global and races
    # under concurrent threads, corrupting OTHER file creations' modes and even
    # leaving the process umask stuck at 0 (world-writable). Guard: the write path
    # must never call os.umask.
    import os as _os
    calls = []
    monkeypatch.setattr(_os, "umask", lambda m: calls.append(m) or 0o022)
    encoding_utils.write_text_atomic(tmp_path / "brand-new.md", "hi")
    assert calls == []


def test_build_traceability_write_uses_atomic_writer(tmp_path, monkeypatch, capsys):
    # --write must route through the atomic writer, not a bare write_text that
    # truncates the fixed traceability-matrix.md path a concurrent reader holds.
    proj = make_proj(tmp_path)
    seen = {}
    real = encoding_utils.write_text_atomic

    def _spy(path, text, *a, **k):
        seen["path"] = Path(path)
        return real(path, text, *a, **k)

    monkeypatch.setattr(build_traceability_matrix, "write_text_atomic", _spy, raising=False)
    monkeypatch.setattr(sys, "argv", ["build_traceability_matrix.py", "--root", str(proj), "--write"])
    rc = build_traceability_matrix.main()
    capsys.readouterr()
    assert rc == 0
    target = proj / "docs" / "product" / "visuals" / "traceability-matrix.md"
    assert seen.get("path") == target        # went through the atomic writer
    assert target.read_text(encoding="utf-8").startswith("# Traceability Matrix")


def test_orphan_story_no_epic(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(
        story.read_text(encoding="utf-8").replace("epic: PRD-AUTH-E1\n", ""),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    assert any(f["check"] == "orphan_story" and f["severity"] == "error" for f in findings)


def test_dangling_link_unknown_epic(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(
        story.read_text(encoding="utf-8").replace(
            "epic: PRD-AUTH-E1", "epic: PRD-GHOST-E9"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    hits = [f for f in findings if f["check"] == "dangling_link"]
    assert hits and hits[0]["severity"] == "error"
    assert hits[0]["context"]["ref"] == "PRD-GHOST-E9"


# ---------------------------------------------------------------------------
# parent_type_mismatch (check_traceability): a parent reference that resolves
# to a REAL id of the WRONG TYPE. It passes the id-existence (dangling_link)
# check but silently corrupts the traceability matrix (the Epic column shows a
# PRD id). Must be flagged error, not waved through.
# ---------------------------------------------------------------------------

def test_parent_type_mismatch_story_epic_points_at_prd(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(
        story.read_text(encoding="utf-8").replace(
            "epic: PRD-AUTH-E1", "epic: PRD-AUTH"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    hits = [f for f in findings if f["check"] == "parent_type_mismatch"]
    assert hits and hits[0]["severity"] == "error"
    assert hits[0]["artifact_id"] == "PRD-AUTH-E1-S1"
    assert hits[0]["context"]["ref"] == "PRD-AUTH"
    # the id exists (a prd), so it is NOT also a dangling_link
    assert not [f for f in findings if f["check"] == "dangling_link"]


def test_parent_type_mismatch_epic_prd_points_at_story(tmp_path):
    proj = make_proj(tmp_path)
    epic = proj / "docs" / "product" / "epics" / "PRD-AUTH-E1.md"
    epic.write_text(
        epic.read_text(encoding="utf-8").replace(
            "prd: PRD-AUTH", "prd: PRD-AUTH-E1-S1"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    hits = [f for f in findings if f["check"] == "parent_type_mismatch"]
    assert any(
        h["artifact_id"] == "PRD-AUTH-E1" and h["context"]["ref"] == "PRD-AUTH-E1-S1"
        for h in hits
    )


def test_parent_type_mismatch_clean_fixture_has_none(tmp_path):
    proj = make_proj(tmp_path)
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    assert not [f for f in findings if f["check"] == "parent_type_mismatch"]


def test_parent_type_mismatch_prd_brd_goal_points_at_non_goal(tmp_path):
    # The THIRD parent-link field: a PRD's brd_goals naming a real id that is NOT
    # a goal (here an epic). Existence-checked only before, so it passed the CI
    # gate green while the traceability matrix showed the wrong doc in the goal
    # column. Must be flagged parent_type_mismatch (error), same as epic/prd refs.
    proj = make_proj(tmp_path)
    prd = proj / "docs" / "product" / "prds" / "auth.md"
    prd.write_text(
        prd.read_text(encoding="utf-8").replace(
            "brd_goals: [BRD-G1]", "brd_goals: [PRD-AUTH-E1]"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    hits = [f for f in findings if f["check"] == "parent_type_mismatch"]
    assert any(
        h["artifact_id"] == "PRD-AUTH" and h["context"]["ref"] == "PRD-AUTH-E1"
        for h in hits
    )
    # the id exists (an epic), so it is NOT also a dangling_link
    assert not [f for f in findings if f["check"] == "dangling_link"]


# ---------------------------------------------------------------------------
# dep_type_mismatch: a depends_on TARGET must be a PRD or Epic (same set
# depends_on is allowed to live ON). A resolved target of any other type (a
# story, a BRD goal) passed the id-existence (dep_dangling) + cycle checks, so
# strict_gate reported green on a semantically-broken dependency edge.
# ---------------------------------------------------------------------------

def _add_depends_on(proj, rel, dep_id):
    p = proj / "docs" / "product" / rel
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "brd_goals: [BRD-G1]", "brd_goals: [BRD-G1]\ndepends_on: [%s]" % dep_id),
        encoding="utf-8",
    )


def _seed_second_prd(proj):
    (proj / "docs" / "product" / "prds" / "bill.md").write_text(
        "---\nid: PRD-BILL\ntype: prd\nbrd_goals: [BRD-G1]\nstatus: approved\nlang: en\n"
        "owner: Jane Doe\nversion: 1.0.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n"
        "personas: [shopper]\nscope: in\nmoscow: must\nhorizon: now\nmetrics: [x]\n"
        "---\n\n# Bill PRD\n\nbody\n",
        encoding="utf-8",
    )


def test_dep_type_mismatch_prd_depends_on_story(tmp_path):
    proj = make_proj(tmp_path)
    _add_depends_on(proj, "prds/auth.md", "PRD-AUTH-E1-S1")  # a story, wrong kind
    graph = spec_graph.build_graph(proj)
    findings = check_consistency.check(graph)
    hits = [f for f in findings if f["check"] == "dep_type_mismatch"]
    assert hits and hits[0]["severity"] == "error"
    assert hits[0]["artifact_id"] == "PRD-AUTH"
    assert hits[0]["context"]["ref"] == "PRD-AUTH-E1-S1"
    # target exists, so NOT a dep_dangling
    assert not [f for f in check_traceability.check(graph) if f["check"] == "dep_dangling"]


def test_dep_type_mismatch_gate_blocks(tmp_path):
    # the whole point: strict_gate's collected findings must now carry the error.
    proj = make_proj(tmp_path)
    _add_depends_on(proj, "prds/auth.md", "PRD-AUTH-E1-S1")
    findings, _graph = strict_gate.collect_findings(proj)
    assert any(
        f["check"] == "dep_type_mismatch" and f["severity"] == "error" for f in findings
    )


def test_dep_target_type_valid_prd_to_prd_not_flagged(tmp_path):
    proj = make_proj(tmp_path)
    _seed_second_prd(proj)
    _add_depends_on(proj, "prds/auth.md", "PRD-BILL")  # PRD → PRD, valid
    graph = spec_graph.build_graph(proj)
    findings = check_consistency.check(graph)
    assert not [f for f in findings if f["check"] == "dep_type_mismatch"]


# ---------------------------------------------------------------------------
# missing_ac / low_ac_count (check_consistency, story acceptance_criteria)
# ---------------------------------------------------------------------------

def test_missing_ac_error(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    text = story.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out = []
    skipping = False
    for line in lines:
        if line.startswith("acceptance_criteria:"):
            skipping = True
            out.append("acceptance_criteria: []\n")
            continue
        if skipping and line.startswith("  - "):
            continue
        skipping = False
        out.append(line)
    story.write_text("".join(out), encoding="utf-8")

    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    assert any(f["check"] == "missing_ac" and f["severity"] == "error" for f in findings)


def test_low_ac_count_warn(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    text = story.read_text(encoding="utf-8")
    text = text.replace(
        '  - "Given five failed attempts, when they try again, '
        'then they are rate-limited for 15 minutes."\n',
        "",
    )
    story.write_text(text, encoding="utf-8")

    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    hits = [f for f in findings if f["check"] == "low_ac_count"]
    assert hits and hits[0]["severity"] == "warn"
    assert hits[0]["context"]["count"] == 1


# ---------------------------------------------------------------------------
# goal_without_metric (check_consistency_schema, via check_consistency.check)
# ---------------------------------------------------------------------------

def test_goal_without_metric_error(tmp_path):
    proj = make_proj(tmp_path)
    brd = proj / "docs" / "product" / "brd.md"
    brd.write_text(
        brd.read_text(encoding="utf-8").replace("metrics: [arr]", "metrics: []"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_consistency.check(graph)
    hits = [f for f in findings if f["check"] == "goal_without_metric"
            and f["artifact_id"] == "BRD-G1"]
    assert hits and hits[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# dep_cycle — iterative, never RecursionError on a long chain
# ---------------------------------------------------------------------------

def test_dep_cycle_two_prds(tmp_path):
    proj = make_proj(tmp_path)
    auth = proj / "docs" / "product" / "prds" / "auth.md"
    auth.write_text(
        auth.read_text(encoding="utf-8").replace(
            "metrics: [signup-conversion]\n",
            "metrics: [signup-conversion]\ndepends_on: [PRD-BILLING]\n",
        ),
        encoding="utf-8",
    )
    billing = proj / "docs" / "product" / "prds" / "billing.md"
    billing.write_text(
        """---
id: PRD-BILLING
type: prd
brd_goals: [BRD-G2]
depends_on: [PRD-AUTH]
status: approved
lang: en
owner: Jane Doe
version: 1.0.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper]
scope: in
moscow: must
horizon: now
---

# Billing PRD

Lets shoppers pay.
""",
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    hits = [f for f in findings if f["check"] == "dep_cycle"]
    assert hits and hits[0]["severity"] == "error"
    assert set(hits[0]["context"]["cycle"][:-1]) == {"PRD-AUTH", "PRD-BILLING"}


def test_dep_cycle_long_chain_no_recursion_error():
    # 3000-deep linear depends_on chain, no cycle: the iterative cycle walk
    # (Tarjan SCC + explicit-stack backtracking, no recursion) must not raise
    # RecursionError (the historical hazard of a recursive walk).
    n = 3000
    adj = {f"N{i}": [f"N{i + 1}"] for i in range(n)}
    adj[f"N{n}"] = []
    cycles = check_traceability.find_dep_cycles(adj)
    assert cycles == []


def test_as_id_list_dedupes_duplicate_targets():
    # A hand-edited `depends_on: [PRD-X, PRD-X]` (copy-paste) must collapse to
    # one id so a single real edge cannot produce two dep_dangling/dep_cycle
    # findings -- mirrors serves_resolver.resolve_serves' `serves` dedupe.
    assert spec_graph._as_id_list(["PRD-X", "PRD-X", "PRD-A"]) == ["PRD-A", "PRD-X"]


def test_find_dep_cycles_dedupes_duplicate_edge():
    # A duplicated edge (PRD-A -> PRD-B listed twice) is ONE cycle, not two --
    # the neighbor iterator must not yield the same target twice.
    cycles = check_traceability.find_dep_cycles(
        {"PRD-A": ["PRD-B", "PRD-B"], "PRD-B": ["PRD-A"]}
    )
    assert cycles == [["PRD-A", "PRD-B", "PRD-A"]]


def test_duplicate_dangling_depends_on_reported_once(tmp_path):
    # End-to-end: a PRD with `depends_on: [PRD-GHOST, PRD-GHOST]` (both absent)
    # must produce exactly ONE dep_dangling finding, not two.
    proj = make_proj(tmp_path)
    auth = proj / "docs" / "product" / "prds" / "auth.md"
    auth.write_text(
        auth.read_text(encoding="utf-8").replace(
            "metrics: [signup-conversion]\n",
            "metrics: [signup-conversion]\ndepends_on: [PRD-GHOST, PRD-GHOST]\n",
        ),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    dangling = [f for f in findings if f["check"] == "dep_dangling"]
    assert len(dangling) == 1


# ---------------------------------------------------------------------------
# "always exit 0" holds under a broken downstream pipe too: every big-JSON
# CLI must route its stdout write through encoding_utils.emit_json (which
# swallows BrokenPipeError) instead of a bare `print(json.dumps(...))`
# (which lets the traceback + non-zero exit through on `script | head`).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script_name", [
    "check_traceability.py", "check_fence.py", "build_traceability_matrix.py",
    "time_advisory.py", "time_realism_anchors.py", "competitive_drift_anchors.py",
])
def test_big_json_cli_emits_via_emit_json_not_bare_print(script_name):
    src = (_SPEC_SCRIPTS / script_name).read_text(encoding="utf-8")
    assert "print(json.dumps(" not in src, (
        f"{script_name} still prints JSON directly — swap to emit_json() so "
        f"a closed downstream pipe (`{script_name} | head`) exits 0, not a "
        f"BrokenPipeError traceback"
    )
    assert "emit_json(" in src, f"{script_name} must emit its output via emit_json()"


# ---------------------------------------------------------------------------
# analytical scripts always exit 0 (JSON on stdout)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod_name", [
    "check_traceability", "check_consistency", "check_fence",
    "build_traceability_matrix", "time_realism_anchors",
    "competitive_drift_anchors", "time_advisory", "session_staleness",
])
def test_checkers_always_exit_0(tmp_path, monkeypatch, capsys, mod_name):
    proj = make_proj(tmp_path)
    mod = _mods[mod_name]
    monkeypatch.setattr(sys, "argv", ["prog", "--root", str(proj)])
    rc = mod.main()
    assert rc == 0
    capsys.readouterr()  # drain the JSON so it doesn't pollute the run log


def test_checkers_exit_0_even_on_an_error_bearing_tree(tmp_path, monkeypatch, capsys):
    # The structural checkers themselves never gate — even a spec riddled with
    # errors still returns 0 JSON. Only strict_gate.py enforces exit 2.
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(
        story.read_text(encoding="utf-8").replace("epic: PRD-AUTH-E1\n", ""),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["prog", "--root", str(proj)])
    assert check_traceability.main() == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["prog", "--root", str(proj)])
    assert check_consistency.main() == 0
    capsys.readouterr()


# ---------------------------------------------------------------------------
# A hand-edited exotic YAML mapping key that yaml.safe_load accepts but json's
# encoder rejects (a `!!binary`-tagged explicit key -> a `bytes` dict key) must
# NOT crash the emit/snapshot boundary. json rejects a non-str/int/float/bool/
# None key BEFORE the `default=` value-hook runs, so deterministic_json_default
# alone can't rescue it; dumps_json key-coerces and retries. Regression:
# without the fix, check_consistency/check_traceability/spec_graph and
# write_snapshot raised an uncaught TypeError (exit 1), breaking "always exit 0".
# ---------------------------------------------------------------------------

def test_dumps_json_survives_json_incoercible_dict_key():
    import json as _json
    dumps_json = _mods["encoding_utils"].dumps_json
    out = dumps_json({"competitive_parity": {b"hello": "behind"}}, ensure_ascii=False)
    assert _json.loads(out)["competitive_parity"] == {"b'hello'": "behind"}


def test_dumps_json_happy_path_is_byte_identical_to_plain_dumps():
    import json as _json
    eu = _mods["encoding_utils"]
    graph = {"nodes": [{"id": "BRD-1", "deps": ["A", "B"]}], "generated_at": "x"}
    assert eu.dumps_json(graph, indent=2, ensure_ascii=False) == _json.dumps(
        graph, indent=2, ensure_ascii=False, default=eu.deterministic_json_default)


def test_dumps_json_still_type_preserves_set_values():
    # The key-coercion fallback must not bypass deterministic_json_default's
    # value handling: a set value is still a type-preserved sorted list.
    import json as _json
    dumps_json = _mods["encoding_utils"].dumps_json
    assert _json.loads(dumps_json({"k": {1, "1"}})) == {"k": [1, "1"]}


def test_binary_key_frontmatter_keeps_checkers_exit_0(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    auth = proj / "docs" / "product" / "prds" / "auth.md"
    auth.write_text(
        auth.read_text(encoding="utf-8").replace(
            "metrics: [signup-conversion]\n",
            'metrics: [signup-conversion]\n'
            'competitive_parity:\n  ? !!binary "aGVsbG8="\n  : behind\n',
        ),
        encoding="utf-8",
    )
    for mod in (check_consistency, check_traceability):
        monkeypatch.setattr(sys, "argv", ["prog", "--root", str(proj)])
        assert mod.main() == 0
        capsys.readouterr()


def test_write_snapshot_survives_binary_key_and_stays_idempotent(tmp_path):
    graph = {"generated_at": "20260713T000000Z",
             "nodes": [{"id": "PRD-1", "competitive_parity": {b"hello": "behind"}}]}
    p1 = spec_graph.write_snapshot(graph, tmp_path)
    p2 = spec_graph.write_snapshot(graph, tmp_path)
    assert p1.name == p2.name  # content-hash idempotent, no crash


# ---------------------------------------------------------------------------
# `dumps_json` is the fail-soft home for the WHOLE hostile-hand-edit class at
# the emit/snapshot boundary, not just the bytes-key crash. Each of these is a
# distinct way a `yaml.safe_load`-legal spec breaks a bare `json.dumps`; all
# must degrade to valid JSON + exit 0 rather than crash or emit invalid JSON.
# ---------------------------------------------------------------------------

def test_dumps_json_preserves_colliding_coerced_keys():
    # A coerced exotic key (bytes -> "b'hello'") colliding with a sibling string
    # key must NOT silently drop one entry — disambiguate, keep both.
    import json as _json
    dumps_json = _mods["encoding_utils"].dumps_json
    d = {"cp": {b"hello": "from_bin"}}
    d["cp"]["b'hello'"] = "from_str"
    got = _json.loads(dumps_json(d))["cp"]
    assert len(got) == 2 and set(got.values()) == {"from_bin", "from_str"}


def test_dumps_json_breaks_circular_reference():
    # A YAML anchor cycle -> json.dumps ValueError("Circular reference"); must
    # degrade to a marker + valid JSON, not escape as a crash.
    import yaml
    import json as _json
    dumps_json = _mods["encoding_utils"].dumps_json
    circ = yaml.safe_load("personas: &a\n  - self: *a\n    name: hi\n")
    out = dumps_json(circ)
    assert "[circular reference]" in out
    _json.loads(out)  # valid JSON, no crash


def test_dumps_json_sanitizes_nan_inf_to_strict_valid_json():
    # NaN/Infinity float VALUES are json-native but RFC-8259-invalid (bare
    # NaN/Infinity tokens json.dumps emits without consulting default=). Must be
    # sanitized so a STRICT parser (one that rejects bare constants) accepts it.
    import yaml
    import json as _json
    dumps_json = _mods["encoding_utils"].dumps_json
    out = dumps_json(yaml.safe_load("{a: .nan, b: .inf, c: -.inf, d: 1.5}"))
    _json.loads(out, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    assert "NaN" not in out and "Infinity" not in out


def test_dumps_json_finite_float_happy_path_unchanged():
    import json as _json
    eu = _mods["encoding_utils"]
    g = {"score": 0.75, "n": 3, "tags": ["a", "b"]}
    assert eu.dumps_json(g) == _json.dumps(g, default=eu.deterministic_json_default)


def test_frontmatter_parser_cli_exit_0_on_exotic_key(tmp_path, monkeypatch, capsys):
    # frontmatter_parser's own debug CLI must degrade like the checker CLIs, not
    # crash on the bytes-key vector (it now routes through dumps_json).
    import json as _json
    fp = _mods["frontmatter_parser"]
    f = tmp_path / "h.md"
    f.write_text('---\n? !!binary "aGVsbG8="\n: v\ntitle: t\n---\nbody\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(f)])
    assert fp.main() == 0
    _json.loads(capsys.readouterr().out)  # valid JSON


# ---------------------------------------------------------------------------
# session_staleness.superseding_decisions: an active DEC block missing its
# `id:` field must be skipped, never crash the sweep with a bare KeyError.
# ---------------------------------------------------------------------------

def test_superseding_decisions_missing_id_is_skipped_not_crashed(tmp_path):
    ledger = tmp_path / "docs" / "product" / "decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n---\nstatus: active\ndate: 2026-02-01\nactor: po\n"
        "ts: 2026-02-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## (missing id)\n\nSome ruling body.\n",
        encoding="utf-8",
    )
    session_updated = dt.date(2026, 1, 1)
    result = session_staleness.superseding_decisions(tmp_path, session_updated)
    assert result == []


# ---------------------------------------------------------------------------
# session_staleness.parse_session_updated: a malformed-yaml `.session.md`
# degrades to None, never crashes the sweep. yaml.safe_load raises well past
# `yaml.YAMLError`/`ValueError` on certain malformed scalars (an explicit-tag
# `!!timestamp` scalar raises a bare AttributeError) — the reader must be
# fail-soft on the WHOLE PyYAML exception family, not a hand-picked subset.
# ---------------------------------------------------------------------------

def test_parse_session_updated_bad_timestamp_tag_degrades_to_none(tmp_path):
    session = tmp_path / "docs" / "product" / ".session.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        "---\nupdated: !!timestamp 'not a ts'\n---\n\nSession notes.\n",
        encoding="utf-8",
    )
    assert session_staleness.parse_session_updated(tmp_path) is None


def test_parse_session_updated_out_of_range_date_degrades_to_none(tmp_path):
    session = tmp_path / "docs" / "product" / ".session.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        "---\nupdated: 2026-13-99\n---\n\nSession notes.\n",
        encoding="utf-8",
    )
    assert session_staleness.parse_session_updated(tmp_path) is None


# ---------------------------------------------------------------------------
# session_staleness.superseding_decisions sorted by NUMERIC DEC id, not
# lexicographic string order -- a string sort puts "DEC-10" before "DEC-2"
# (the character '1' < '2'), which orders a supersede-candidate list
# numerically backwards once the register passes DEC-9.
# ---------------------------------------------------------------------------

def test_superseding_decisions_sorted_numerically_not_lexicographically(tmp_path):
    ledger = tmp_path / "docs" / "product" / "decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n---\nid: DEC-10\nstatus: active\ndate: 2026-02-01\nactor: po\n"
        "ts: 2026-02-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## DEC-10 -- later ruling\n\nBody.\n"
        "\n---\nid: DEC-2\nstatus: active\ndate: 2026-02-02\nactor: po\n"
        "ts: 2026-02-02T00:00:00+00:00\naffects: Y\n---\n\n"
        "## DEC-2 -- earlier-numbered ruling\n\nBody.\n",
        encoding="utf-8",
    )
    session_updated = dt.date(2026, 1, 1)
    result = session_staleness.superseding_decisions(tmp_path, session_updated)
    assert [r["id"] for r in result] == ["DEC-2", "DEC-10"]


# ---------------------------------------------------------------------------
# check_persona_portraits: a non-string frontmatter `id:` must never crash
# the dict-keyed carrier lookup (a list id is unhashable).
# ---------------------------------------------------------------------------

def test_duplicate_dangling_brd_goal_reported_once(tmp_path):
    # A hand-edited `brd_goals: [BRD-G9, BRD-G9]` (dangling, duplicated) must
    # produce ONE dangling_link, not two -- same dedupe class as depends_on.
    proj = make_proj(tmp_path)
    auth = proj / "docs" / "product" / "prds" / "auth.md"
    auth.write_text(
        auth.read_text(encoding="utf-8").replace(
            "brd_goals: [BRD-G1]", "brd_goals: [BRD-G9, BRD-G9]"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_traceability.check(graph)
    dl = [f for f in findings if f["check"] == "dangling_link" and "BRD-G9" in f.get("detail", "")]
    assert len(dl) == 1


def test_duplicate_persona_without_portrait_reported_once(tmp_path):
    # `personas: [ghostie, ghostie]` (duplicated, no portrait heading) must
    # produce ONE persona_without_portrait finding, not two.
    proj = make_proj(tmp_path)
    vision = proj / "docs" / "product" / "vision.md"
    vision.write_text(
        vision.read_text(encoding="utf-8").replace(
            "personas: [shopper, store-admin]", "personas: [ghostie, ghostie]"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    findings = check_consistency_product.check_persona_portraits(graph)
    pw = [f for f in findings if f["check"] == "persona_without_portrait"
          and "ghostie" in f.get("detail", "")]
    assert len(pw) == 1


def test_check_risks_dedupes_byte_identical_entry():
    # A copy-pasted byte-identical risk row is one defect, not two -- but two
    # risks differing in free-text `description` must stay distinct.
    ccr = _mods["check_consistency_risk"]
    node = {"id": "PRD-X", "file": "prds/x.md", "risks": [
        {"description": "r", "impact": "critical", "likelihood": "med", "status": "open"},
        {"description": "r", "impact": "critical", "likelihood": "med", "status": "open"},
    ]}
    ue = [f for f in ccr.check_risks(node) if f["check"] == "unknown_enum"]
    assert len(ue) == 1
    node2 = dict(node, risks=[
        {"description": "a", "impact": "critical", "likelihood": "med", "status": "open"},
        {"description": "b", "impact": "critical", "likelihood": "med", "status": "open"},
    ])
    ue2 = [f for f in ccr.check_risks(node2) if f["check"] == "unknown_enum"]
    assert len(ue2) == 2


def test_stable_dedup_key_deterministic_on_set_value():
    # A YAML `!!set` value iterates in hash-seed order; the dedup key must not
    # depend on that -- the set is canonicalized to a sorted list.
    key = spec_graph.stable_dedup_key({"m": {"c", "a", "b"}})
    assert '["a", "b", "c"]' in key
    assert (spec_graph.stable_dedup_key({"m": {"a", "b"}})
            == spec_graph.stable_dedup_key({"m": {"b", "a"}}))
    # A set member's TYPE is preserved (unlike a JSON-forced dict key): an int
    # member and a string member that str-collide stay distinct, and a set
    # holding both sorts deterministically.
    assert (spec_graph.stable_dedup_key({"m": {1}})
            != spec_graph.stable_dedup_key({"m": {"1"}}))
    assert (spec_graph.stable_dedup_key({"m": {1, "1"}})
            == spec_graph.stable_dedup_key({"m": {"1", 1}}))


def test_write_snapshot_serializes_set_field_deterministically(tmp_path):
    # A hand-edited `!!set`-valued field must not make the snapshot content-hash
    # (its filename) hash-seed-dependent -- the set is serialized as a sorted
    # list, not the hash-ordered `str(set)`, so identical graphs stay idempotent.
    eu = _mods["encoding_utils"]
    assert eu.deterministic_json_default({"c", "a", "b"}) == ["a", "b", "c"]
    # A nested set/frozenset member is canonicalized recursively so its outer
    # order can't flap either.
    assert eu.deterministic_json_default(
        {frozenset({"b", "a"}), frozenset({"d", "c"})}) == [["a", "b"], ["c", "d"]]
    graph = {"generated_at": "2026-07-13T00:00:00Z",
             "nodes": [{"id": "X", "status": {"c", "a", "b"}}], "edges": []}
    body = spec_graph.write_snapshot(graph, tmp_path).read_text(encoding="utf-8")
    compact = body.replace("\n", "").replace(" ", "")
    assert '["a","b","c"]' in compact
    assert "{'" not in body  # no raw Python set repr leaked into the snapshot


def test_unknown_goal_keys_deduped_on_str_collision(tmp_path):
    # Two distinct dict keys that str-coerce to the same text (`1:` beside
    # `"1":`) must yield ONE unknown_goal_key finding, not two.
    proj = make_proj(tmp_path)
    brd = proj / "docs" / "product" / "brd.md"
    brd.write_text(
        brd.read_text(encoding="utf-8").replace(
            "    metrics: [arr]\n",
            "    metrics: [arr]\n    1: stray\n    \"1\": stray2\n", 1),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    goal = next(n for n in graph["nodes"] if n.get("id") == "BRD-G1" and n.get("type") == "goal")
    assert goal["unknown_goal_keys"].count("1") == 1
    findings = _mods["check_consistency_schema"].check_goals(graph)
    ugk = [f for f in findings if f["check"] == "unknown_goal_key"
           and f.get("context", {}).get("key") == "1"]
    assert len(ugk) == 1


def test_stable_dedup_key_tolerates_mixed_type_mapping_keys():
    # A hand-edited mixed-type mapping key (`1: note` beside `description:`) is
    # valid YAML; the dedup key must not raise the str-vs-int TypeError that a
    # plain json.dumps(sort_keys=True) would, and must stay order-stable.
    assert (spec_graph.stable_dedup_key({"a": 1, 2: "b"})
            == spec_graph.stable_dedup_key({2: "b", "a": 1}))
    ccr = _mods["check_consistency_risk"]
    node = {"id": "PRD-X", "file": "prds/x.md", "risks": [
        {"description": "x", 1: "note", "impact": "critical"},
        {"description": "x", 1: "note", "impact": "critical"},
    ]}
    ue = [f for f in ccr.check_risks(node) if f["check"] == "unknown_enum"]
    assert len(ue) == 1  # no crash, deduped to one


def test_check_competitors_dedupes_malformed_but_keeps_dup_id():
    # Byte-identical malformed competitor rows collapse to one finding each;
    # a repeated VALID id still fires dup_id every time (intended feature).
    ccc = _mods["check_consistency_competition"]
    fs = ccc.check_competitors({"competitors": [
        {"id": "BAD ID", "name": "X", "threat": "extreme"},
        {"id": "BAD ID", "name": "X", "threat": "extreme"},
    ]})
    assert len([f for f in fs if f["check"] == "unknown_enum"]) == 1
    assert len([f for f in fs if f["check"] == "invalid_id"]) == 1
    dup = ccc.check_competitors({"competitors": [
        {"id": "COMP-X", "name": "A", "threat": "high"},
        {"id": "COMP-X", "name": "A", "threat": "high"},
    ]})
    assert len([f for f in dup if f["check"] == "dup_id"]) == 1


def test_check_product_subsystems_dedupes_duplicate_row(tmp_path):
    # A copy-pasted duplicate subsystem table row must flag one drift, not two.
    proj = make_proj(tmp_path)
    product = proj / "docs" / "product" / "PRODUCT.md"
    product.write_text(
        product.read_text(encoding="utf-8")
        + "\n\n## Subsystems\n\n| ID | Horizon |\n| --- | --- |\n"
        + "| AUTH | later |\n| AUTH | later |\n",
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    drift = [f for f in check_consistency_product.check_product_subsystems(graph)
             if f["check"] == "subsystem_horizon_drift"]
    assert len(drift) == 1


def test_check_product_subsystems_dedup_does_not_mask_distinct_drift(tmp_path):
    # A matching row (AUTH|now) followed by a DRIFTING row (AUTH|later) for the
    # same subsystem must still surface the drift -- id-only dedupe would swallow
    # it by row order. Key on (id, horizon), not id alone.
    proj = make_proj(tmp_path)
    product = proj / "docs" / "product" / "PRODUCT.md"
    product.write_text(
        product.read_text(encoding="utf-8")
        + "\n\n## Subsystems\n\n| ID | Horizon |\n| --- | --- |\n"
        + "| AUTH | now |\n| AUTH | later |\n",
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    drift = [f for f in check_consistency_product.check_product_subsystems(graph)
             if f["check"] == "subsystem_horizon_drift"]
    assert len(drift) == 1


def test_persona_portraits_list_id_does_not_crash(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    vision = proj / "docs" / "product" / "vision.md"
    vision.write_text(
        vision.read_text(encoding="utf-8").replace("id: VISION\n", "id: [VISION, EXTRA]\n"),
        encoding="utf-8",
    )
    graph = spec_graph.build_graph(proj)
    # Must not raise TypeError: unhashable type: 'list' (the raw frontmatter
    # id used to be passed straight into a dict.get() carrier lookup).
    findings = check_consistency_product.check_persona_portraits(graph)
    assert isinstance(findings, list)

    # The whole gate stays fail-soft too: exits EXIT_BLOCKED with a clean
    # finding set, never a raw traceback / non-2 exit.
    monkeypatch.setattr(sys, "argv", ["strict_gate.py", "--root", str(proj)])
    rc = strict_gate.main()
    capsys.readouterr()
    assert rc == strict_gate.EXIT_BLOCKED


# ---------------------------------------------------------------------------
# time_realism_anchors — script half of the anchored LLM scaffold
# ---------------------------------------------------------------------------

def _add_story(proj: Path, n: int, epic_id: str = "PRD-AUTH-E1") -> None:
    path = proj / "docs" / "product" / "stories" / f"PRD-AUTH-E1-S{n}.md"
    path.write_text(
        f"""---
id: PRD-AUTH-E1-S{n}
type: story
epic: {epic_id}
status: draft
lang: en
owner: Jane Doe
version: 0.1.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper]
scope: in
moscow: must
size: S
horizon: now
acceptance_criteria:
  - "Given a case, when it happens, then it resolves."
  - "Given another case, when it happens, then it resolves too."
---

# Story {n}
""",
        encoding="utf-8",
    )


def test_time_realism_anchor_eligible_days_remaining(tmp_path):
    proj = make_proj(tmp_path)
    epic = proj / "docs" / "product" / "epics" / "PRD-AUTH-E1.md"
    text = epic.read_text(encoding="utf-8")
    text = text.replace("horizon: now\n", "horizon: now\nsize: L\ntarget_date: 2026-06-15\n")
    epic.write_text(text, encoding="utf-8")
    for n in range(2, 7):  # S1 already exists; add S2..S6 -> 6 total children
        _add_story(proj, n)

    graph = spec_graph.build_graph(proj)
    anchors = time_realism_anchors.build_anchors(graph, dt.date(2026, 6, 1))
    epic_anchor = next(a for a in anchors if a["artifact_id"] == "PRD-AUTH-E1")
    assert epic_anchor["eligible"] is True
    assert epic_anchor["size"] == "L"
    assert epic_anchor["days_remaining"] == 14
    assert epic_anchor["child_story_count"] == 6
    assert epic_anchor["target_date"] == "2026-06-15"


def test_time_realism_anchor_ineligible_missing_target_date(tmp_path):
    proj = make_proj(tmp_path)
    graph = spec_graph.build_graph(proj)
    anchors = time_realism_anchors.build_anchors(graph, dt.date(2026, 6, 1))
    epic_anchor = next(a for a in anchors if a["artifact_id"] == "PRD-AUTH-E1")
    # The base fixture epic has no target_date/size -> not eligible, no hallucinated data.
    assert epic_anchor["eligible"] is False
    assert epic_anchor["days_remaining"] is None


def _drift_graph(parity_map):
    return {
        "competitors": [{"id": "COMP-A", "name": "Acme"}, {"id": "COMP-B", "name": "Beta"}],
        "nodes": [{
            "id": "PRD-1", "type": "prd", "file": "prd.md",
            "scope": "core-value", "status": "approved",
            "competitive_parity": parity_map,
        }],
    }


def test_competitive_drift_anchor_malformed_parity_is_not_real_data():
    # A hand-edited PRD can carry a non-scalar competitive_parity value (a YAML
    # list or mapping). check_consistency flags it as invalid_type separately, but
    # the anchors feeder must NOT count it toward competitors_with_data -- the
    # eligibility floor is the anti-hallucination gate; garbage must leave it
    # ineligible, not push the LLM drift-warn onto data that is not real.
    anchor = competitive_drift_anchors.build_anchors(
        _drift_graph({"COMP-A": ["behind"], "COMP-B": {"x": 1}}))[0]
    assert anchor["competitors_with_data"] == 0
    assert anchor["eligible"] is False


def test_competitive_drift_anchor_unknown_enum_parity_is_not_real_data():
    # A typo'd scalar (not in the ahead/parity/behind enum) is an unknown_enum
    # value check_consistency flags -- it is not a real verdict either and must
    # not count toward the floor. Only the real enum verdicts do.
    anchor = competitive_drift_anchors.build_anchors(
        _drift_graph({"COMP-A": "aheadX", "COMP-B": "behnd"}))[0]
    assert anchor["competitors_with_data"] == 0
    assert anchor["eligible"] is False


def test_competitive_drift_anchor_real_verdicts_still_count():
    # The tightened filter must not break the happy path: two genuine enum
    # verdicts still make the PRD eligible with competitors_with_data == 2.
    anchor = competitive_drift_anchors.build_anchors(
        _drift_graph({"COMP-A": "ahead", "COMP-B": "behind"}))[0]
    assert anchor["competitors_with_data"] == 2
    assert anchor["eligible"] is True
    assert anchor["all_behind_competitors"] == ["Beta"]


def _realism_graph(size):
    return {
        "nodes": [{
            "id": "PRD-AUTH-E1", "type": "epic", "file": "e.md", "horizon": "now",
            "status": "draft", "size": size, "target_date": "2026-06-15",
        }],
        "edges": [],
    }


@pytest.mark.parametrize("bad_size", ["XXL", ["L"], {"x": 1}, 3])
def test_time_realism_anchor_invalid_size_is_not_eligible(bad_size):
    # size is enum-validated ({S,M,L} in check_consistency); a hand-edited epic
    # can carry a typo'd or non-scalar size. The realism anchor must treat an
    # off-enum size as "no usable size" (mirroring target_date's parse-or-null),
    # not pass the presence check and feed the LLM a garbage scope.
    anchor = time_realism_anchors.build_anchors(_realism_graph(bad_size), dt.date(2026, 6, 1))[0]
    assert anchor["eligible"] is False
    assert anchor["size"] is None


def test_time_realism_anchor_valid_size_still_eligible():
    # Happy path unchanged: a real enum size stays eligible and is emitted as-is.
    anchor = time_realism_anchors.build_anchors(_realism_graph("L"), dt.date(2026, 6, 1))[0]
    assert anchor["eligible"] is True
    assert anchor["size"] == "L"


# ---------------------------------------------------------------------------
# check_fence — no KIT_PREFIX exclusion for .claude/
# ---------------------------------------------------------------------------

def test_check_fence_does_not_except_dotclaude(tmp_path):
    proj = make_proj(tmp_path)
    kit_dir = proj / ".claude"
    kit_dir.mkdir()
    (kit_dir / "touched.txt").write_text("x", encoding="utf-8")

    findings = check_fence.scan(proj)
    hits = [f for f in findings if f["file"] == ".claude/touched.txt"]
    assert hits, ".claude/ must be surfaced, not excepted (no KIT_PREFIX exclusion)"
    assert hits[0]["check"] == "fence_breach"
    assert hits[0]["severity"] == "warn"
    assert not hasattr(check_fence, "KIT_PREFIX")


def test_check_fence_ignores_docs_product_itself(tmp_path):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(story.read_text(encoding="utf-8") + "\nExtra.\n", encoding="utf-8")
    findings = check_fence.scan(proj)
    assert findings == [], "an in-boundary spec edit must never trip the fence"


# ---------------------------------------------------------------------------
# grep-guard: 0 dot-claude skill/hook refs in the spec skill's checker scripts
# and references  # learn: CI hard-constraint #1
# ---------------------------------------------------------------------------

_CHECKER_SCRIPT_NAMES = [
    "check_traceability.py", "check_consistency.py", "check_consistency_schema.py",
    "check_consistency_time.py", "check_consistency_risk.py",
    "check_consistency_competition.py", "check_consistency_product.py",
    "check_fence.py", "build_traceability_matrix.py", "time_realism_anchors.py",
    "competitive_drift_anchors.py", "time_advisory.py", "session_staleness.py",
    "strict_gate.py",
]
_CHECKER_REFERENCE_NAMES = ["validation-rules-spec.md", "workflow-validate.md"]
_BANNED = (".claude/skills/", ".claude/hooks/")  # learn: CI hard-constraint #1 banned literals


@pytest.mark.parametrize("rel", _CHECKER_SCRIPT_NAMES)
def test_no_dotclaude_refs_in_ported_scripts(rel):
    text = (_SPEC_SCRIPTS / rel).read_text(encoding="utf-8")
    for banned in _BANNED:
        assert banned not in text, f"{rel} still carries {banned!r}"


@pytest.mark.parametrize("rel", _CHECKER_REFERENCE_NAMES)
def test_no_dotclaude_refs_in_ported_references(rel):
    text = (_SPEC_SCRIPTS.parent / "references" / rel).read_text(encoding="utf-8")
    for banned in _BANNED:
        assert banned not in text, f"{rel} still carries {banned!r}"


def test_brd_goals_non_string_entry_flagged_invalid_type():
    # A non-string brd_goals entry (a bare number/bool from a hand-edit) is a
    # broken ID-reference link; check_traceability defers the shape error to
    # check_consistency's LIST_FIELDS home, which must emit invalid_type rather
    # than let the broken link sail through --strict with no finding.
    node = {"id": "PRD-BAD", "type": "prd", "file": "prds/bad.md",
            "brd_goals": [123, "BRD-G1", True]}
    findings = check_consistency.check({"nodes": [node], "edges": []})
    inv = [f for f in findings if f["check"] == "invalid_type" and "brd_goals[]" in f["detail"]]
    assert len(inv) == 2 and all(f["severity"] == "error" for f in inv)   # 123 and True
    # an all-string brd_goals list stays clean (no false positive)
    ok = {"id": "PRD-OK", "type": "prd", "file": "prds/ok.md", "brd_goals": ["BRD-G1"]}
    clean = check_consistency.check({"nodes": [ok], "edges": []})
    assert [f for f in clean if f["check"] == "invalid_type"] == []


def _bounded(fn, seconds=4):
    """Run fn() under a SIGALRM so a blocking-read regression FAILS (raises)
    instead of hanging the whole suite forever."""
    import signal

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_parse_session_updated_skips_fifo_session_file(tmp_path):
    # A committed .session.md that is a FIFO/symlink->/dev/zero would block
    # read_text forever, hanging the staleness sweep (wired into the validate
    # gate). parse_session_updated must skip a non-regular file (fail-soft None).
    import os
    (tmp_path / "docs" / "product").mkdir(parents=True)
    os.mkfifo(tmp_path / "docs" / "product" / ".session.md")
    assert _bounded(lambda: session_staleness.parse_session_updated(tmp_path)) is None


def test_session_md_gitignore_skips_fifo_gitignore(tmp_path):
    # The .gitignore reader is wired into check_consistency.check (validate gate);
    # a FIFO .gitignore blocks read_text (its except OSError never fires -- the
    # read waits, it does not raise). Must skip it without hanging.
    import os
    (tmp_path / "docs" / "product").mkdir(parents=True)
    (tmp_path / "docs" / "product" / ".session.md").write_text("x", encoding="utf-8")
    os.mkfifo(tmp_path / ".gitignore")
    out = _bounded(lambda: check_consistency._session_md_gitignore({"root_path": str(tmp_path)}))
    assert isinstance(out, list)
