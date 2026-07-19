"""Contract for disabled_skills — the disabled-skill state library (lib + CLI).

Multi-source is locked from day one (the P3 cache seam): effective_disabled unions
every source, so a later cache Paths simply appends and nothing about the resolver
changes. dep_chain returns the disabled dep-closure in load order; status is the
3-state live|disabled|unknown resolver; describe/skill_list read the human label from
the stashed SKILL.md frontmatter and flag a recorded-but-unstashed (install-omitted)
skill rather than dropping it.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import disabled_skills as ds  # noqa: E402


def _src(base, skills=(), stashed=(), recorded=(), descs=None):
    """Build one Paths source under `base`: live skill dirs, stashed dirs (each with a
    SKILL.md carrying a description), and an omit record listing exactly `recorded`."""
    skills_dir = base / "skills"
    stash_dir = base / "stash"
    record = base / "omit.json"
    for s in skills:
        d = skills_dir / s
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: hs:%s\n---\n# %s\n" % (s, s))
    for s in stashed:
        d = stash_dir / s
        d.mkdir(parents=True, exist_ok=True)
        desc = (descs or {}).get(s, "does %s things" % s)
        (d / "SKILL.md").write_text(
            "---\nname: hs:%s\ndescription: %s\n---\n# %s\n" % (s, desc, s))
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"omitted": sorted(recorded)}))
    return ds.Paths(skills_dir=skills_dir, stash_dir=stash_dir, record_path=record)


def _write_graph(base, body):
    p = base / "skill-deps.yaml"
    p.write_text(body)
    return p


def test_effective_disabled_unions_sources(tmp_path):
    # Two independent sources, each contributing a different disabled skill; the
    # resolver must UNION them (the P3 cache is just another appended source).
    s1 = _src(tmp_path / "a", recorded=["x"], stashed=["x"])
    s2 = _src(tmp_path / "b", recorded=["y"], stashed=["y"])
    assert ds.effective_disabled([s1, s2]) == {"x", "y"}


def test_dep_chain_returns_only_disabled_members_in_order(tmp_path):
    # a -> b -> c; b and c disabled, a live. chain(a) must be the disabled closure in
    # discovery (load) order, excluding a itself.
    deps = _write_graph(tmp_path, (
        "core_immutable: [a]\n"
        "skills:\n"
        "  a: {deps: [b]}\n"
        "  b: {deps: [c]}\n"
        "  c: {deps: []}\n"))
    src = _src(tmp_path / "s", skills=["a"], stashed=["b", "c"], recorded=["b", "c"])
    assert ds.dep_chain("a", [src], deps) == ["b", "c"]


def test_status_live_disabled_unknown(tmp_path):
    src = _src(tmp_path / "s", skills=["live1"], stashed=["dis1"], recorded=["dis1"])
    assert ds.status("live1", [src]) == "live"
    assert ds.status("dis1", [src]) == "disabled"
    assert ds.status("ghost", [src]) == "unknown"


def test_list_reads_description_from_stash(tmp_path):
    src = _src(tmp_path / "s", stashed=["foo"], recorded=["foo"],
               descs={"foo": "foo the widget"})
    items = ds.skill_list([src])
    hit = [i for i in items if i["name"] == "foo"]
    assert hit and "widget" in hit[0]["description"]
    assert hit[0]["stash_missing"] is False


def test_record_says_disabled_but_stash_missing_flags_it(tmp_path):
    # install-omit: recorded disabled, but the dir was never copied (no stash). Still
    # disabled, and flagged stash_missing so the caller reports the install recovery
    # path instead of trying to read a nonexistent SKILL.md.
    src = _src(tmp_path / "s", recorded=["gone"])
    assert ds.status("gone", [src]) == "disabled"
    d = ds.describe("gone", [src])
    assert d["stash_missing"] is True


def test_stash_path_points_at_the_stashed_dir(tmp_path):
    src = _src(tmp_path / "s", stashed=["ask"], recorded=["ask"])
    p = ds.stash_path("ask", [src])
    assert p is not None and Path(p).name == "ask"
    assert (Path(p) / "SKILL.md").is_file()


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "disabled_skills.py"), "--root", str(root), *args],
        capture_output=True, text=True)


def test_cli_status_and_list(tmp_path):
    # Build the real default layout under a root so default_sources resolves it.
    skills = tmp_path / "harness/plugins/hs/skills"
    stash = tmp_path / "harness/plugins/hs/disabled-skills"
    (skills / "cook").mkdir(parents=True)
    (skills / "cook" / "SKILL.md").write_text("---\nname: hs:cook\n---\n# cook\n")
    (stash / "ask").mkdir(parents=True)
    (stash / "ask" / "SKILL.md").write_text(
        "---\nname: hs:ask\ndescription: ask a quick question\n---\n# ask\n")
    rec = tmp_path / "harness/state/install-omitted-skills.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"omitted": ["ask"]}))

    r = _cli(tmp_path, "--status", "ask")
    assert r.returncode == 0 and "disabled" in r.stdout
    r = _cli(tmp_path, "--status", "cook")
    assert "live" in r.stdout
    r = _cli(tmp_path, "--list")
    assert "ask" in r.stdout


def test_no_skill_in_both_skills_and_disabled_dirs():
    # XOR invariant on the REAL tree: a skill lives in skills/ OR the stashed sibling
    # disabled-skills/, never both. A dir in both would double-load / corrupt the
    # re-enable round-trip. Empty stash (nothing disabled in-repo) trivially holds.
    live = _ROOT / "harness/plugins/hs/skills"
    stash = _ROOT / "harness/plugins/hs/disabled-skills"
    live_names = {d.name for d in live.iterdir()
                  if d.is_dir() and (d / "SKILL.md").is_file()} if live.is_dir() else set()
    stash_names = {d.name for d in stash.iterdir()
                   if d.is_dir() and (d / "SKILL.md").is_file()} if stash.is_dir() else set()
    assert live_names.isdisjoint(stash_names), sorted(live_names & stash_names)


# --- dev-farm awareness: an off-listed skill is disabled even though its dir stays
#     in the live skills/ tree (a dev loads a curated symlink farm, not skills/). ---

def _with_off_list(base, src, names):
    off = base / ".harness-dev" / "dev-off-skills.yaml"
    off.parent.mkdir(parents=True, exist_ok=True)
    off.write_text("disabled:\n" + "".join("  - %s\n" % n for n in names))
    return src._replace(off_list_path=off)


def test_dev_farm_offlisted_skill_is_disabled_from_live_dir(tmp_path):
    # threejs present in skills/ (repo is full) + in the dev off-list, NOT stashed.
    src = _src(tmp_path, skills=["plan", "threejs"])
    src = _with_off_list(tmp_path, src, ["threejs"])
    assert ds.status("threejs", [src]) == "disabled"   # not "live", despite the live dir
    assert ds.status("plan", [src]) == "live"
    # read-inline path points at the LIVE dir (it was never moved to a stash)
    assert ds.stash_path("threejs", [src]) == (tmp_path / "skills" / "threejs").resolve()
    # its SKILL.md is readable there -> not flagged stash_missing
    assert ds.describe("threejs", [src])["stash_missing"] is False
    assert "threejs" in {c["name"] for c in ds.skill_list([src])}


def test_off_list_absent_keeps_stock_behavior(tmp_path):
    # no dev off-list -> a present skill is live (ship/install semantics unchanged)
    src = _src(tmp_path, skills=["plan", "threejs"])
    assert ds.status("threejs", [src]) == "live"
