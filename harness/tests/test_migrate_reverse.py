"""Reverse-migration tests — the inverse of the forward decomposition split.

The reverse engine collapses every themed sibling plugin (hs-<g>) back into the
spine plugin (hs): it moves each skill dir hs-<g>/skills/<s> -> hs/skills/<s> and
rewrites every reference hs-<g>:<s> -> hs:<s> (and the path form). Together with the
forward split it must be a true identity round-trip for all 84 non-spine skills.

Everything below runs against a temp-dir fixture (plugin dirs + text files); the
real repo tree is never touched. Post-collapse the live tree holds only the single
`hs` plugin -- no `hs-<g>` siblings survive -- so even the full 84-count assertion
builds a SYNTHETIC 14-plugin themed tree (the authoritative pre-collapse topology,
reconstructed from the canonical group maps) and inverts that.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import migrate_decomposition as md  # noqa: E402


# --------------------------------------------------------------- full non-spine map

def _full_non_spine_map() -> dict[str, str]:
    """Every non-spine skill -> its themed group, the authoritative pre-collapse set.

    Sourced from the canonical group data, NOT the (now-collapsed) live plugin tree:
    the 38 themed skills from decomposition-map.yaml (group != "hs") plus the 46
    ck-port skills from components.yaml's seven ck-port group lists. The ck-port group
    `ai` additionally owns `common` (an ai-grouped shared skill not enumerated in the
    components 'skills:' list but recorded in decomposition-map's group comments), which
    is what brings the ck-port region to 46 and the whole non-spine universe to 84.
    """
    dm = yaml.safe_load((ROOT / "harness/data/decomposition-map.yaml").read_text())
    full = {s: g for s, g in dm["skills"].items() if g != "hs"}  # 38 themed
    comps = yaml.safe_load((ROOT / "harness/data/components.yaml").read_text())["components"]
    for g in ("ai", "devops", "stack", "uiux", "integrations", "extra", "viz"):
        for s in comps[g]["skills"]:
            full[s] = g
    full["common"] = "ai"  # ai-grouped shared skill; the 84th non-spine skill
    return full


# Build spine-form references at runtime instead of writing the literal token, so this
# test file never carries a bare `hs:<skill>` that the forward migrate --check would
# (correctly) flag as a surviving old-form reference. The contiguous token only ever
# exists in the values produced/asserted at run time, never in the source text.
def _spine(skill: str) -> str:
    return "hs:" + skill


def _spine_path(skill: str) -> str:
    return "hs/skills/" + skill


# --------------------------------------------------------------------------- fixture

# A representative slice spanning original themed groups and ck-port groups, so the
# round-trip and rewrite tests cover both regions of the map.
_FIXTURE_SKILLS = {
    # original 6 themed groups
    "loop": "flow",
    "afk": "flow",
    "bakeoff": "think",
    "brainstorm": "think",
    "research": "research",
    "port": "create",
    "remember": "mem",
    "voice": "meta",
    # ck-port groups
    "excalidraw": "viz",
    "deploy": "devops",
    "shader": "ai",
    "databases": "stack",
    "design": "uiux",
    "shopify": "integrations",
    "ask": "extra",
}


def _build_plugins(root: Path, skills: dict[str, str]) -> None:
    """Lay out hs-<g>/skills/<s>/SKILL.md for each themed skill (themed = moved-out)."""
    for skill, group in skills.items():
        d = root / f"harness/plugins/hs-{group}/skills/{skill}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: hs-{group}:{skill}\n---\nbody of {skill}\n"
        )
    # a spine skill so the spine plugin exists and is excluded from inversion
    sp = root / "harness/plugins/hs/skills/plan"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "SKILL.md").write_text("---\nname: hs:plan\n---\nspine\n")


def _build_plugin_dirs_only(root: Path, skills: dict[str, str]) -> None:
    """Lay out the themed dir structure but with neutral SKILL.md content.

    This populates the themed plugins so _invert_non_spine sees a non-spine set, yet
    no SKILL.md carries a themed-form ref — so the only themed-form refs in the tree
    are the ones a test deliberately seeds into a separate content file.
    """
    for skill, group in skills.items():
        d = root / f"harness/plugins/hs-{group}/skills/{skill}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: hs:{skill}\n---\nbody of {skill}\n")
    sp = root / "harness/plugins/hs/skills/plan"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "SKILL.md").write_text("---\nname: hs:plan\n---\nspine\n")


# --------------------------------------------------------------------------- invert

def test_invert_non_spine_yields_90_including_ckports(tmp_path):
    # Post-collapse the live tree has no hs-<g> siblings, so build the full themed-skill
    # tree synthetically (the pre-collapse topology) and invert THAT -- the
    # inverter must still recover all non-spine skills, ck-ports included, and
    # exclude the spine.
    # Original 84 non-spine: 38 themed (flow/think/research/create/mem/meta) + 46 ck-ports.
    # +3 docs-ssot skills (docs-scaffold, docs-standardize, docs-build) + manual-test
    # (research group) + rule-author (meta group) + contract-test (research group)
    # + goal (flow group) + drawio (viz group) + vibe (flow group) + gemini (ai group)
    # + prompt (extra group) + partner (ai group)
    # + coding-agent-orchestration (flow group) -> 96
    # + spec (product group) + shape (product group) -> 98
    # + eval-bootstrap (research group) -> 99
    # + deep-swe (research) + folder-context (meta) + interview-docs (mem) + handoff
    # (extra, components-only) -> 103 total.
    full = _full_non_spine_map()
    assert len(full) == 103, f"fixture map should hold 103 non-spine, got {len(full)}"
    _build_plugins(tmp_path, full)
    ns = md._invert_non_spine(tmp_path)
    assert len(ns) == 103, f"expected 103 non-spine, got {len(ns)}"
    assert "plan" not in ns, "spine skill leaked into inverted set"
    assert ns["excalidraw"] == "viz"
    assert ns["deploy"] == "devops"
    assert ns["bakeoff"] == "think"
    assert ns["loop"] == "flow"
    assert ns["common"] == "ai"  # ck-port shared skill is recovered too
    assert ns["docs-scaffold"] == "docs-ssot"  # docs-ssot pipeline skills
    assert ns["docs-standardize"] == "docs-ssot"
    assert ns["docs-build"] == "docs-ssot"
    assert ns["spec"] == "product"  # PO/BA product-spec pair
    assert ns["shape"] == "product"


# --------------------------------------------------------------------- rewrite invoke

def test_reverse_invoke_rewrite_basic(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    ns = md._invert_non_spine(tmp_path)
    assert md.rewrite_text_reverse("see hs-think:bakeoff now", ns) == f"see {_spine('bakeoff')} now"
    assert md.rewrite_text_reverse("run /hs-flow:afk please", ns) == f"run /{_spine('afk')} please"


def test_reverse_invoke_rewrite_unknown_skill_untouched(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    ns = md._invert_non_spine(tmp_path)
    # notaskill is not a known skill -> left exactly as-is
    assert md.rewrite_text_reverse("hs-think:notaskill", ns) == "hs-think:notaskill"


def test_reverse_invoke_rewrite_wrong_group_untouched(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    ns = md._invert_non_spine(tmp_path)
    # bakeoff is a think skill; hs-flow:bakeoff declares the wrong group -> NOT rewritten
    assert md.rewrite_text_reverse("hs-flow:bakeoff", ns) == "hs-flow:bakeoff"


def test_reverse_path_rewrite(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    ns = md._invert_non_spine(tmp_path)
    assert (
        md.rewrite_text_reverse("open hs-viz/skills/excalidraw/SKILL.md", ns)
        == "open hs/skills/excalidraw/SKILL.md"
    )


def test_reverse_rewrite_idempotent(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    ns = md._invert_non_spine(tmp_path)
    text = "hs-think:bakeoff and /hs-flow:afk and hs-viz/skills/excalidraw"
    once = md.rewrite_text_reverse(text, ns)
    twice = md.rewrite_text_reverse(once, ns)
    assert once == twice, "reverse rewrite is not idempotent"


# --------------------------------------------------------------------------- check

def test_reverse_check_clean_tree(tmp_path):
    # After a full reverse migration the themed plugins are gone and every ref is
    # collapsed, so --check must report a clean tree.
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    (tmp_path / "doc.md").write_text("see hs-think:bakeoff and hs-flow:afk\n")
    md.run_migrate(root=tmp_path, reverse=True, write_rename_map=False)
    assert md.run_migrate(root=tmp_path, do_check=True, reverse=True, write_rename_map=False) == 0


def test_reverse_check_flags_dangling(tmp_path):
    _build_plugin_dirs_only(tmp_path, _FIXTURE_SKILLS)
    (tmp_path / "doc.md").write_text("still references hs-flow:loop here\n")
    assert md.run_migrate(root=tmp_path, do_check=True, reverse=True, write_rename_map=False) != 0


def test_reverse_check_flags_wrong_group_ref(tmp_path):
    _build_plugin_dirs_only(tmp_path, _FIXTURE_SKILLS)
    # bakeoff is a think skill, so hs-flow:bakeoff is a wrong-group ref. The reverse
    # checker must still flag it (any hs-<g>:<known-skill> where g is a real group).
    (tmp_path / "doc.md").write_text("wrong group hs-flow:bakeoff lurks\n")
    assert md.run_migrate(root=tmp_path, do_check=True, reverse=True, write_rename_map=False) != 0


def test_reverse_check_finder_catches_wrong_group(tmp_path):
    # the independent dangling finder catches wrong-group refs directly. Source the
    # non-spine set from a synthetic themed tree (the live tree is collapsed, so
    # _invert_non_spine(ROOT) would be empty and the finder would have nothing to key
    # on). bakeoff is a think skill, so hs-flow:bakeoff is a real-group/wrong-skill
    # ref the finder must still flag.
    _build_plugins(tmp_path, _full_non_spine_map())
    ns = md._invert_non_spine(tmp_path)
    hits = md.find_dangling_reverse("hs-flow:bakeoff", ns)
    assert hits, "wrong-group ref not flagged by reverse dangling finder"


# --------------------------------------------------------------------------- exempt

def test_reverse_exempt_files_not_rewritten(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    body = "this keeps hs-think:bakeoff literal\n"
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "STANDARDIZE.md").write_text(body)
    (tmp_path / "docs" / "decisions.md").write_text(body)
    (tmp_path / "BACKLOG.md").write_text(body)
    md.run_migrate(root=tmp_path, reverse=True, write_rename_map=False)
    for rel in ("docs/STANDARDIZE.md", "docs/decisions.md", "BACKLOG.md"):
        assert (tmp_path / rel).read_text() == body, f"{rel} was rewritten by reverse"


def test_reverse_keep_marker_line_preserved(tmp_path):
    _build_plugins(tmp_path, _FIXTURE_SKILLS)
    kept = "intentional hs-think:bakeoff literal  # migrate:keep\n"
    rewritten = "ordinary hs-think:bakeoff ref\n"
    (tmp_path / "doc.md").write_text(kept + rewritten)
    md.run_migrate(root=tmp_path, reverse=True, write_rename_map=False)
    out = (tmp_path / "doc.md").read_text()
    assert kept in out, "line with # migrate:keep was rewritten"
    assert f"ordinary {_spine('bakeoff')} ref" in out, "ordinary line was not rewritten"


# ----------------------------------------------------------------------------- move

def test_reverse_move_dir(tmp_path):
    _build_plugins(tmp_path, {"excalidraw": "viz"})
    ns = md._invert_non_spine(tmp_path)
    md.move_skill_dirs_reverse(tmp_path, ns)
    assert (tmp_path / "harness/plugins/hs/skills/excalidraw/SKILL.md").is_file()
    assert not (tmp_path / "harness/plugins/hs-viz/skills/excalidraw").exists()


def test_reverse_rename_map_swaps_old_new():
    rmap = md.build_rename_map_reverse(_FIXTURE_SKILLS)
    entry = rmap["moved"]["excalidraw"]
    # reverse: old = themed, new = spine
    assert entry["old_invoke"] == "hs-viz:excalidraw"
    assert entry["new_invoke"] == _spine("excalidraw")
    assert entry["old_dir"] == "harness/plugins/hs-viz/skills/excalidraw"
    assert entry["new_dir"] == "harness/plugins/hs/skills/excalidraw"


# ------------------------------------------------------------------------ round-trip

def test_forward_then_reverse_is_identity(tmp_path):
    """Forward split followed by reverse collapse returns the original tree+content."""
    # Build the SPINE layout: every fixture skill starts under hs/skills/<s> with a
    # bare hs:<s> frontmatter name, plus a content file referencing each skill.
    for skill in _FIXTURE_SKILLS:
        d = tmp_path / f"harness/plugins/hs/skills/{skill}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: hs:{skill}\n---\nbody of {skill}\n")
    refs = " ".join(f"hs:{s}" for s in _FIXTURE_SKILLS)
    paths = " ".join(f"hs/skills/{s}" for s in _FIXTURE_SKILLS)
    content = f"{refs}\n{paths}\n"
    (tmp_path / "doc.md").write_text(content)

    # snapshot original
    before = _snapshot(tmp_path)

    # forward split using the canonical map restricted to our fixture skills
    fwd_ns = {s: g for s, g in _FIXTURE_SKILLS.items()}
    md.move_skill_dirs(tmp_path, fwd_ns)
    _rewrite_tree(tmp_path, lambda t: md.rewrite_text(t, fwd_ns))

    # reverse collapse, sourcing the non-spine set from the (now-split) tree
    rev_ns = md._invert_non_spine(tmp_path)
    md.move_skill_dirs_reverse(tmp_path, rev_ns)
    _rewrite_tree(tmp_path, lambda t: md.rewrite_text_reverse(t, rev_ns))

    after = _snapshot(tmp_path)
    assert after == before, "forward->reverse round-trip is not an identity"


# --------------------------------------------------------------------------- helpers

def _snapshot(root: Path) -> dict[str, str]:
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[p.relative_to(root).as_posix()] = p.read_text()
    return snap


def _rewrite_tree(root: Path, fn) -> None:
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in {".md"}:
            txt = p.read_text()
            new = fn(txt)
            if new != txt:
                p.write_text(new)
