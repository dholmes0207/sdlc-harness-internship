"""Phase B: the decomposition migrate engine (move + 4-form rewrite + dangling-check).

The engine relocates non-spine skills into themed sibling plugins and rewrites every
reference to them: slash invocation (/hs:s), bare invocation (hs:s), frontmatter
`name:`, and path (hs/skills/s). Spine skills and already-prefixed refs are left
untouched; the active plan dir and the generated manifest are out of scope.

Fixtures use SYNTHETIC skill names (zzfoo/zzbar/zzspine) so the test's own literals
are never swallowed by a rewrite of a real skill name.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import migrate_decomposition as md  # noqa: E402

GROUP = "gg"  # synthetic themed group for the fixture


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal synthetic repo: two movable skills, one spine skill, trap refs."""
    root = tmp_path
    plug = root / "harness/plugins"
    # movable skills
    _write(plug / "hs/skills/zzfoo/SKILL.md",
           "---\nname: hs:zzfoo\n---\n# zzfoo\nSee hs:zzbar and /hs:zzfoo.\n")
    _write(plug / "hs/skills/zzbar/SKILL.md",
           "---\nname: hs:zzbar\n---\n# zzbar\n")
    # spine skill — must NOT move or rewrite
    _write(plug / "hs/skills/zzspine/SKILL.md",
           "---\nname: hs:zzspine\n---\n# zzspine\nrefs hs:zzfoo from spine.\n")
    # destination plugin dir pre-exists (substrate already registered it)
    (plug / f"hs-{GROUP}/skills").mkdir(parents=True)
    # a doc with every ref form + boundary traps
    _write(root / "docs/guide.md",
           "invoke /hs:zzfoo or bare hs:zzfoo; path harness/plugins/hs/skills/zzfoo.\n"
           "spine hs:zzspine stays. trap hs:zzfoobar must survive. "
           "already hs-other:zzfoo stays. wordchar xhs:zzfoo stays.\n")
    # data file with bare target form
    _write(root / "harness/data/route-probes.yaml",
           "probes:\n  - target: hs:zzfoo\n  - target: hs:zzspine\n")
    # excluded: manifest + active plan dir both mention the skill, must NOT change
    _write(root / "harness/manifest.json", '{"note": "hs:zzfoo path hs/skills/zzfoo"}\n')
    _write(root / "plans/260619-active/plan.md",
           "---\nstatus: in_progress\n---\nuses hs:zzfoo here.\n")
    # the canonical map for this fixture
    _write(root / "harness/data/decomposition-map.yaml",
           yaml.safe_dump({"skills": {"zzfoo": GROUP, "zzbar": GROUP, "zzspine": "hs"}}))
    return root


def _map(root: Path) -> dict:
    return md.non_spine_skills(md.load_map(root / "harness/data/decomposition-map.yaml"))


# ---- pure rewrite unit (no filesystem) -------------------------------------

def test_rewrite_covers_all_four_forms():
    ns = {"zzfoo": "gg", "zzbar": "gg"}
    out = md.rewrite_text("a /hs:zzfoo b hs:zzbar c hs/skills/zzfoo d name: hs:zzfoo", ns)
    assert "/hs-gg:zzfoo" in out
    assert " hs-gg:zzbar " in out
    assert "hs-gg/skills/zzfoo" in out
    assert "name: hs-gg:zzfoo" in out


def test_rewrite_leaves_spine_and_traps_untouched():
    ns = {"zzfoo": "gg"}  # zzspine is spine → not in ns
    src = "hs:zzspine hs:zzfoobar hs-other:zzfoo xhs:zzfoo -hs:zzfoo"
    out = md.rewrite_text(src, ns)
    assert out == src, f"trap text was mutated: {out!r}"


def test_rewrite_is_idempotent():
    ns = {"zzfoo": "gg"}
    once = md.rewrite_text("hs:zzfoo /hs:zzfoo hs/skills/zzfoo", ns)
    twice = md.rewrite_text(once, ns)
    assert once == twice
    assert "hs-gg-gg" not in twice  # no double-prefix


def test_rewrite_spares_invocation_inside_url():
    # `hs:<skill>` inside a URL (preceded by '//') is not a slash-command/bare
    # invocation — a leading slash before the optional '/' marks a URL, leave it.
    ns = {"zzfoo": "gg"}
    src = "docs at https://hs:zzfoo and http://host/hs:zzfoo end"
    out = md.rewrite_text(src, ns)
    assert out == src, f"URL invocation was rewritten: {out!r}"
    # but a real slash-command / bare form still rewrites
    assert md.rewrite_text("run /hs:zzfoo now", ns) == "run /hs-gg:zzfoo now"


# ---- full tree migration ----------------------------------------------------

def test_migrate_moves_dirs_and_keeps_spine(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    plug = repo / "harness/plugins"
    assert (plug / "hs-gg/skills/zzfoo/SKILL.md").is_file()
    assert (plug / "hs-gg/skills/zzbar/SKILL.md").is_file()
    assert not (plug / "hs/skills/zzfoo").exists()
    assert not (plug / "hs/skills/zzbar").exists()
    # spine stays put, name unchanged
    assert (plug / "hs/skills/zzspine/SKILL.md").is_file()
    assert "name: hs:zzspine" in (plug / "hs/skills/zzspine/SKILL.md").read_text()


def test_migrate_rewrites_frontmatter_name(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    txt = (repo / "harness/plugins/hs-gg/skills/zzfoo/SKILL.md").read_text()
    assert "name: hs-gg:zzfoo" in txt


def test_migrate_rewrites_docs_all_forms_and_spares_traps(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    g = (repo / "docs/guide.md").read_text()
    assert "/hs-gg:zzfoo" in g
    assert "bare hs-gg:zzfoo" in g
    assert "hs-gg/skills/zzfoo" in g
    # traps survive
    assert "hs:zzspine stays" in g
    assert "hs:zzfoobar must survive" in g
    assert "hs-other:zzfoo stays" in g
    assert "xhs:zzfoo stays" in g


def test_migrate_rewrites_data_file(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    rp = (repo / "harness/data/route-probes.yaml").read_text()
    assert "target: hs-gg:zzfoo" in rp
    assert "target: hs:zzspine" in rp  # spine untouched


def test_migrate_excludes_manifest_and_active_plan(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    assert "hs:zzfoo" in (repo / "harness/manifest.json").read_text()
    assert "hs:zzfoo" in (repo / "plans/260619-active/plan.md").read_text()


def test_check_clean_after_migrate(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    # dangling scan excludes manifest + plans, so a clean migrate → exit 0
    assert md.run_migrate(root=repo, dry_run=False, do_check=True) == 0


def test_check_detects_dangling_before_migrate(repo: Path):
    # before any move, the live refs are dangling w.r.t. the target topology
    assert md.run_migrate(root=repo, dry_run=False, do_check=True) != 0


def test_migrate_is_idempotent_on_tree(repo: Path):
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    g1 = (repo / "docs/guide.md").read_text()
    md.run_migrate(root=repo, dry_run=False, do_check=False)  # second run
    g2 = (repo / "docs/guide.md").read_text()
    assert g1 == g2
    assert "hs-gg-gg" not in g2


def test_dry_run_changes_nothing(repo: Path):
    before = (repo / "docs/guide.md").read_text()
    md.run_migrate(root=repo, dry_run=True, do_check=False)
    assert (repo / "docs/guide.md").read_text() == before
    assert (repo / "harness/plugins/hs/skills/zzfoo").exists()  # not moved


def test_rename_map_emitted(repo: Path):
    # The map is regenerated INTO the tmp repo by the engine and read back from
    # there — never from a committed copy. Deleting the tracked map at repo root
    # does not affect this test.
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    rm = json.loads((repo / "harness/data/decomposition-rename-map.json").read_text())
    assert rm["moved"]["zzfoo"]["new_invoke"] == "hs-gg:zzfoo"
    assert rm["moved"]["zzfoo"]["old_invoke"] == "hs:zzfoo"
    assert "zzspine" not in rm["moved"]


def test_rename_map_not_required_at_runtime(repo: Path):
    """The forward engine reads its INPUT from decomposition-map.yaml and emits the
    rename-map as OUTPUT. No committed rename-map.json is needed for it to run —
    proving the tracked file has zero live readers and is safe to delete."""
    assert not (repo / "harness/data/decomposition-rename-map.json").exists()
    md.run_migrate(root=repo, dry_run=False, do_check=False)
    # engine produced correct rewrites without any pre-existing committed map
    guide = (repo / "docs/guide.md").read_text()
    assert "hs-gg:zzfoo" in guide
    assert (repo / "harness/data/decomposition-rename-map.json").exists()


def test_no_dangling_ref_after_delete():
    """After deleting the committed rename-map, the only places its filename may
    still appear in shipped harness code are the engine's own OUTPUT path and this
    test file. Any other live reference would be a dangling ref."""
    needle = "decomposition-rename-map.json"
    allowed = {"migrate_decomposition.py", "test_migrate_decomposition.py"}
    offenders = []
    for sub in ("scripts", "data", "hooks", "plugins", "rules", "schemas"):
        base = ROOT / "harness" / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.name in allowed:
                continue
            if p.suffix in (".pyc",) or "__pycache__" in p.parts:
                continue
            try:
                if needle in p.read_text(encoding="utf-8"):
                    offenders.append(str(p.relative_to(ROOT)))
            except (OSError, UnicodeDecodeError):
                continue
    assert offenders == [], f"dangling refs to deleted rename-map: {offenders}"


def test_reverse_patterns_are_cached():
    """The reverse rewrite is applied line-by-line; recompiling the 84-skill
    alternation per line made a full-tree collapse minutes-slow. The compiled
    pattern pair must be memoized so equal inputs reuse the same objects."""
    ns = {"loop": "flow", "brainstorm": "think", "excalidraw": "viz"}
    first = md._reverse_res(ns)
    second = md._reverse_res(dict(ns))  # equal content, distinct dict object
    assert first is second  # cached: no recompile across calls in one run


def test_reverse_rewrite_still_correct_after_caching():
    """Memoization must not change the rewrite result — wrong-group and unknown
    refs stay verbatim; correct-group refs collapse to the bare hs: form."""
    ns = {"loop": "flow", "bakeoff": "think"}
    assert md.rewrite_text_reverse("see /hs:loop now", ns) == "see /hs:loop now"
    assert md.rewrite_text_reverse("hs-flow:bakeoff", ns) == "hs-flow:bakeoff"  # wrong group
    assert md.rewrite_text_reverse("hs-think:notaskill", ns) == "hs-think:notaskill"
