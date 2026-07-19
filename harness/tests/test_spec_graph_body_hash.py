"""Tests for the per-node body_hash and the shared changed-field rule.

body_hash is the cache key the memory layer depends on, and it closes the
latent impact-pass gap where a body-only edit (frontmatter unchanged) went
undetected. The changed-field rule lives in exactly one home —
spec_graph.CHANGED_FIELDS + spec_graph.changed_nodes — so every delta
consumer reads the same rule and they cannot drift apart.

Mirrors the source suite's body_hash + changed-field coverage near-verbatim;
the delta-render test is left out because no consumer here reads that renderer.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from spec_graph import (  # noqa: E402
    build_graph,
    CHANGED_FIELDS,
    changed_nodes,
)

import spec_graph as sg  # noqa: E402
from conftest import VALID  # noqa: E402

# The set-determinism regression below fixes harness/plugins/hs/skills/spec/scripts/
# spec_graph.py (the hs:spec skill's own copy, OWNED source) — a DIFFERENT physical
# module from the harness-internal `sg` imported above (harness/scripts/spec_graph.py,
# used for this repo's own docs/product/). Load the skill copy in isolation (same
# save/restore-sys.modules loader test_spec_graph.py uses) so it does not collide
# with the `spec_graph` name already bound above.
_ROOT = Path(__file__).resolve().parents[2]
_SKILL_SCRIPTS = _ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
_skill_mods = load_skill_scripts(
    _SKILL_SCRIPTS,
    ["encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph"],
)
skill_spec_graph = _skill_mods["spec_graph"]


def _node(graph, node_id):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    raise AssertionError(f"node {node_id} not in graph")


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# ---------- node carries a deterministic body_hash ----------

def test_node_carries_body_hash():
    """Every artifact node carries body_hash == sha256(body)[:8], and the same
    spec built twice yields the same hash (deterministic cache key)."""
    g1 = build_graph(VALID)
    g2 = build_graph(VALID)
    story1 = _node(g1, "PRD-AUTH-E1-S1")
    story2 = _node(g2, "PRD-AUTH-E1-S1")
    assert isinstance(story1["body_hash"], str)
    assert len(story1["body_hash"]) == 8
    assert story1["body_hash"] == story2["body_hash"], "hash must be deterministic"

    # Recompute against the artifact's actual body to prove the formula.
    from frontmatter_parser import parse_file
    body = parse_file(VALID / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md")["body"] or ""
    assert story1["body_hash"] == _sha8(body)


# ---------- body_hash changes when only the body changes ----------

def test_body_hash_changes_with_body(tmp_path):
    """Edit the body only (frontmatter byte-identical) → body_hash differs."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(VALID, proj)
    story_path = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"

    before = build_graph(proj)
    hash_before = _node(before, "PRD-AUTH-E1-S1")["body_hash"]

    text = story_path.read_text(encoding="utf-8")
    story_path.write_text(text + "\n\nAn extra clarifying paragraph.\n", encoding="utf-8")

    after = build_graph(proj)
    hash_after = _node(after, "PRD-AUTH-E1-S1")["body_hash"]

    assert hash_before != hash_after, "body-only edit must change body_hash"


# ---------- goal node has body_hash None, no crash ----------

def test_goal_node_body_hash_none():
    """Goals are expanded from brd.md and have no standalone body, so their
    body_hash is None (back-compat). Building the graph must not crash."""
    graph = build_graph(VALID)
    goals = [n for n in graph["nodes"] if n["type"] == "goal"]
    assert goals, "valid-spec must contain at least one goal"
    for g in goals:
        assert g["body_hash"] is None


# ---------- old snapshot lacking body_hash → not a body change ----------

def test_old_snapshot_without_body_hash():
    """changed_nodes against a baseline node-dict that has NO body_hash key must
    not mark the node changed on body grounds (unknown baseline) and must not
    raise KeyError."""
    current = {
        "nodes": [
            {"id": "PRD-X", "status": "draft", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "S", "body_hash": "aaaaaaaa"},
        ],
    }
    previous = {
        "nodes": [
            # Pre-upgrade snapshot: identical frontmatter, body_hash key absent.
            {"id": "PRD-X", "status": "draft", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "S"},
        ],
    }
    changed = changed_nodes(current, previous)
    assert changed == [], "absent baseline body_hash must not count as a body change"


# ---------- changed_nodes detects body-only + frontmatter changes ----------

def test_changed_nodes_detects_body_only_change():
    """changed_nodes returns the id whose body_hash differs, and still catches a
    frontmatter-only change on the legacy fields (regression lock)."""
    base = {"id": "PRD-X", "status": "draft", "scope": "in", "moscow": "must",
            "horizon": "now", "size": "S", "body_hash": "11111111"}

    # body-only change
    cur_body = {**base, "body_hash": "22222222"}
    previous = {"nodes": [base]}
    current = {"nodes": [cur_body]}
    assert changed_nodes(current, previous) == ["PRD-X"]

    # frontmatter-only change (body_hash identical) still caught
    cur_status = {**base, "status": "approved"}
    current_fm = {"nodes": [cur_status]}
    assert changed_nodes(current_fm, previous) == ["PRD-X"]

    # no change at all
    current_same = {"nodes": [dict(base)]}
    assert changed_nodes(current_same, previous) == []

    # CHANGED_FIELDS must include body_hash + the 5 legacy fields
    assert "body_hash" in CHANGED_FIELDS
    for f in ("status", "scope", "moscow", "horizon", "size"):
        assert f in CHANGED_FIELDS


# ---------- first post-upgrade validate does not flood ----------

def test_first_validate_no_flood():
    """A baseline snapshot missing body_hash on EVERY node must NOT make
    changed_nodes return the whole spec — only nodes with a real frontmatter
    diff. Avoids an impact-flood on the first post-upgrade validate."""
    # current: every node now carries a body_hash; node B also changed status.
    current = {
        "nodes": [
            {"id": "A", "status": "draft", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "S", "body_hash": "aaaa1111"},
            {"id": "B", "status": "approved", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "M", "body_hash": "bbbb2222"},
            {"id": "C", "status": "draft", "scope": "out", "moscow": "could",
             "horizon": "later", "size": "L", "body_hash": "cccc3333"},
        ],
    }
    # previous (pre-upgrade): no body_hash anywhere; only B's status differs.
    previous = {
        "nodes": [
            {"id": "A", "status": "draft", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "S"},
            {"id": "B", "status": "draft", "scope": "in", "moscow": "must",
             "horizon": "now", "size": "M"},
            {"id": "C", "status": "draft", "scope": "out", "moscow": "could",
             "horizon": "later", "size": "L"},
        ],
    }
    changed = changed_nodes(current, previous)
    assert changed == ["B"], f"only the real frontmatter diff should flag, got {changed}"


# ---------- every node (incl goals) carries a content_hash ----------

def test_every_node_carries_content_hash():
    """content_hash is the AC-aware provenance fingerprint. Unlike body_hash (None for
    goals), EVERY node carries a string content_hash — a BRD goal must not vanish from
    the provenance map (else a goal edit is invisible downstream)."""
    graph = build_graph(VALID)
    for n in graph["nodes"]:
        assert isinstance(n.get("content_hash"), str), f"{n['id']} has no content_hash"
        assert len(n["content_hash"]) == 8
    # goals included specifically (this is the BRD-blind regression)
    goal_ids = {n["id"] for n in graph["nodes"] if n["type"] == "goal"}
    assert {"BRD-G1", "BRD-G2"} <= goal_ids


# ---------- an AC-only edit shifts content_hash but NOT body_hash ----------

def test_ac_only_edit_changes_content_hash_only(tmp_path):
    """Editing ONLY a story's acceptance_criteria (frontmatter; body byte-identical)
    must change content_hash (the provenance signal) while leaving body_hash
    untouched (the memory/drift cache key stays AC-blind by design)."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(VALID, proj)
    story_path = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"

    before = _node(build_graph(proj), "PRD-AUTH-E1-S1")
    text = story_path.read_text(encoding="utf-8")
    text2 = text.replace("they reach the home page.", "they reach the DASHBOARD page.")
    assert text2 != text, "fixture AC line not found — update the test anchor"
    story_path.write_text(text2, encoding="utf-8")

    after = _node(build_graph(proj), "PRD-AUTH-E1-S1")
    assert after["content_hash"] != before["content_hash"], "AC edit must shift content_hash"
    assert after["body_hash"] == before["body_hash"], "AC edit must NOT touch body_hash"


# ---------- a goal content edit shifts that goal's content_hash ----------

def test_goal_content_edit_changes_goal_content_hash(tmp_path):
    """A BRD goal's mutable content (title/metrics) folds into its content_hash, so a
    goal-only edit is detectable even though goals have no body."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(VALID, proj)
    brd = proj / "docs" / "product" / "brd.md"

    before = _node(build_graph(proj), "BRD-G1")
    text = brd.read_text(encoding="utf-8")
    text2 = text.replace("Reach $1M ARR in 12 months", "Reach $2M ARR in 12 months")
    assert text2 != text
    brd.write_text(text2, encoding="utf-8")

    after = _node(build_graph(proj), "BRD-G1")
    assert after["content_hash"] != before["content_hash"]


# ---------- CHANGED_FIELDS includes content_hash ----------

def test_changed_fields_includes_content_hash():
    """The shared changed-field rule must track content_hash so an approved story's
    AC edit registers as a change."""
    assert "content_hash" in CHANGED_FIELDS
    # AC-only style change: body_hash identical, content_hash differs → flagged.
    base = {"id": "S", "status": "approved", "scope": "in", "moscow": "must",
            "horizon": "now", "size": "S", "body_hash": "aaaaaaaa", "content_hash": "11111111"}
    cur = {"nodes": [{**base, "content_hash": "22222222"}]}
    prev = {"nodes": [base]}
    assert changed_nodes(cur, prev) == ["S"]


# ---------- parse_error surfaces, never crashes the build ----------

def test_parse_error_surfaces_in_graph(tmp_path):
    """A malformed artifact lands in graph['parse_errors'] (file + error) and the
    rest of the graph still builds — the memory-gap detector reads this list."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(VALID, proj)
    bad = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    bad.write_text("---\n: : : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")

    graph = build_graph(proj)
    assert graph["parse_errors"], "malformed artifact must surface as a parse_error"
    assert any("PRD-AUTH-E1-S1.md" in pe["file"] for pe in graph["parse_errors"])
    # The other artifacts still made it into the graph.
    assert _node(graph, "PRD-AUTH")["type"] == "prd"


# ---------- foreign-snapshot robustness (twins of the graph_core fix) ----------

def test_changed_nodes_skips_id_less_node():
    # A pre-upgrade / hand-edited snapshot may carry an id-less node; the delta must
    # skip it, never crash with KeyError (matches the hardened graph_core sibling).
    cur = {"nodes": [{"id": "PRD-X", "status": "a"}, {"status": "noid"}]}
    prev = {"nodes": [{"id": "PRD-X", "status": "b"}]}
    assert sg.changed_nodes(cur, prev) == ["PRD-X"]


def test_diff_graphs_skips_id_less_and_non_dict_node():
    cur = {"nodes": [{"id": "PRD-X"}, {"noid": 1}, "bare-str"], "product": {}}
    base = {"nodes": [{"id": "PRD-X"}], "product": {}}
    d = sg.diff_graphs(cur, base)
    assert d["added"] == [] and d["removed"] == []


# ---------- OpSec: private-URL drop is case-insensitive ------------------------

def test_private_url_dropped_case_insensitively():
    # URI schemes are case-insensitive (RFC 3986); the single OpSec chokepoint must
    # drop PRIVATE:/Private: the same as private:, or the secret path leaks into the
    # graph and every render.
    for scheme in ("private:", "PRIVATE:", "Private:"):
        arts = [{"ok": True, "file": "brd.md", "frontmatter": {"type": "brd",
                 "competitors": [{"id": "C", "name": "X",
                                  "url": scheme + "internal/x.pdf", "threat": "high"}]}}]
        assert sg._competitors(arts)[0]["url"] is None, scheme
    # a public URL is still kept
    pub = [{"ok": True, "file": "brd.md", "frontmatter": {"type": "brd",
            "competitors": [{"id": "C", "name": "X", "url": "https://pub.com", "threat": "low"}]}}]
    assert sg._competitors(pub)[0]["url"] == "https://pub.com"


# ---------- title strip is a whole-token match, not a substring ----------------

def test_title_h1_keeps_title_mentioning_other_id():
    # The trailing-id strip must be a whole-token match: a title that merely mentions
    # PRD-Xtra after the em-dash must NOT be truncated for node PRD-X.
    assert (sg._title_from_h1("# Real Title — see PRD-Xtra for context", "PRD-X")
            == "Real Title — see PRD-Xtra for context")
    # the real template suffix `— <TYPE> <id>` IS still stripped:
    assert sg._title_from_h1("# Real Title — PRD PRD-X", "PRD-X") == "Real Title"


# ---------- _stringify_keys / content_hash determinism on a YAML !!set --------

def test_stringify_keys_sorts_a_set_deterministically():
    # A YAML `!!set` frontmatter value yaml-loads to a python `set`; _stringify_keys
    # must turn it into a SORTED list (sort after stringifying) rather than falling
    # through to json.dumps(default=str) -> str(set), whose iteration order is
    # PYTHONHASHSEED-randomized and therefore not run-to-run stable.
    assert skill_spec_graph._stringify_keys({3, 1, 2}) == ["1", "2", "3"]
    assert skill_spec_graph._stringify_keys(frozenset({"b", "a"})) == ["a", "b"]
    # nested under a dict/list, same as a real goal's `metrics: !!set {...}`
    assert skill_spec_graph._stringify_keys({"metrics": {"mrr", "arr"}}) == {"metrics": ["arr", "mrr"]}


def test_set_bearing_goal_content_hash_is_pythonhashseed_stable(tmp_path):
    # Regression: before the fix, a BRD goal's `metrics: !!set {...}` leaked
    # str(set) (hash-seed-order-dependent) into the content_hash fingerprint, so
    # the SAME ledger content produced a DIFFERENT hash across processes (cache
    # thrash). Run the real build in two child processes under different
    # PYTHONHASHSEED values and assert the fingerprint agrees.
    proj = tmp_path / "proj"
    (proj / "docs" / "product").mkdir(parents=True)
    (proj / "docs" / "product" / "brd.md").write_text(
        "---\n"
        "id: BRD\n"
        "type: brd\n"
        "status: approved\n"
        "goals:\n"
        "  - id: BRD-G1\n"
        "    title: Reach ARR\n"
        "    status: approved\n"
        "    metrics: !!set {arr, mrr, churn}\n"
        "---\n\n# BRD\n",
        encoding="utf-8",
    )
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from spec_graph import build_graph\n"
        "from pathlib import Path\n"
        "g = build_graph(Path(%r))\n"
        "goal = next(n for n in g['nodes'] if n['id'] == 'BRD-G1')\n"
        "print(goal['content_hash'])\n"
    ) % (str(_SKILL_SCRIPTS), str(proj))

    hashes = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True, env=env,
        )
        hashes.append(out.stdout.strip())
    assert hashes[0] and hashes[0] == hashes[1], f"not hash-seed-stable: {hashes}"
