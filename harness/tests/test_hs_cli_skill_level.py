"""hs-cli skill-level enable/disable (runtime per-skill toggle, single hs plugin).

Disable STASHES the skill dir under harness/plugins/hs/disabled-skills (a tracked
sibling of skills/, so an off skill ships with the bundle and the loader never sees
it) and records it in the omit list. Enable restores the dir and auto-enables deps.
The 13-skill spine core is refused. Stash-based so an installed copy (no source tree)
can round-trip without a reinstall — the mode-A frontmatter hide is unsupported
for plugin skills on this CC (the probe), so omission is the only disable.
"""
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import hs_cli  # noqa: E402


def _mk_target(tmp: Path) -> Path:
    skills = tmp / "harness/plugins/hs/skills"
    for s in ["plan", "cook", "test", "scenario", "afk"]:
        d = skills / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: hs:%s\n---\n# %s\n" % (s, s))
    data = tmp / "harness/data"
    data.mkdir(parents=True)
    (data / "skill-deps.yaml").write_text(
        "core_immutable: [plan, cook, test]\n"
        "skills:\n"
        "  plan: {deps: []}\n"
        "  cook: {deps: [plan, test]}\n"
        "  test: {deps: []}\n"
        "  scenario: {deps: []}\n"
        "  afk: {deps: [scenario]}\n")
    return tmp


def _skills(t): return t / "harness/plugins/hs/skills"
def _stash(t): return t / "harness/plugins/hs/disabled-skills"


def test_disable_moves_dir_and_records(tmp_path):
    t = _mk_target(tmp_path)
    rc = hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    assert rc == 0
    assert not (_skills(t) / "scenario").exists()
    assert (_stash(t) / "scenario" / "SKILL.md").is_file()
    assert "scenario" in json.loads(_omit(t).read_text())["omitted"]


def test_enable_restores_and_clears_record(tmp_path):
    t = _mk_target(tmp_path)
    hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    rc = hs_cli.main(["skills", "--enable", "scenario", "--root", str(t)])
    assert rc == 0
    assert (_skills(t) / "scenario" / "SKILL.md").is_file()
    assert not (_stash(t) / "scenario").exists()
    assert "scenario" not in json.loads(_omit(t).read_text())["omitted"]


def test_enable_accepts_csv_list(tmp_path):
    # Round-22: the onboarding protocol documents `skills --enable <csv-of-cluster-
    # skills>`, but --enable did not comma-split, so a CSV collapsed to ONE bogus
    # skill name ("scenario,afk") and enabled nothing. Both the CSV and the repeated-
    # flag forms must work (mirrors --on/--off, which already split).
    t = _mk_target(tmp_path)
    hs_cli.main(["skills", "--disable", "scenario", "--disable", "afk", "--root", str(t)])
    assert not (_skills(t) / "scenario").exists()
    assert not (_skills(t) / "afk").exists()
    rc = hs_cli.main(["skills", "--enable", "scenario,afk", "--root", str(t)])
    assert rc == 0
    assert (_skills(t) / "scenario" / "SKILL.md").is_file(), "CSV --enable did not restore scenario"
    assert (_skills(t) / "afk" / "SKILL.md").is_file(), "CSV --enable did not restore afk"


def test_disable_accepts_csv_list(tmp_path):
    # Symmetric: a CSV --disable must split too.
    t = _mk_target(tmp_path)
    rc = hs_cli.main(["skills", "--disable", "scenario,afk", "--root", str(t)])
    assert rc == 0
    assert (_stash(t) / "scenario" / "SKILL.md").is_file()
    assert (_stash(t) / "afk" / "SKILL.md").is_file()


def test_disable_core_immutable_refused(tmp_path):
    t = _mk_target(tmp_path)
    rc = hs_cli.main(["skills", "--disable", "plan", "--root", str(t)])
    assert rc != 0
    assert (_skills(t) / "plan" / "SKILL.md").is_file()  # untouched


def test_disable_persists_each_move_before_a_mid_loop_crash(tmp_path, monkeypatch):
    # F1 regression: a crash partway through a multi-skill disable must leave the
    # already-moved skills RECORDED (per-move save), not stranded — else verify
    # --strict reports false integrity drift for a skill the user merely disabled.
    import shutil
    t = _mk_target(tmp_path)
    real_move = shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:                       # crash AFTER the first move lands
            raise OSError("simulated crash mid-disable")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", flaky_move)
    try:
        hs_cli.main(["skills", "--disable", "scenario", "--disable", "afk",
                     "--root", str(t)])
    except OSError:
        pass
    # scenario moved first; it MUST be recorded despite the afk crash
    assert "scenario" in json.loads(_omit(t).read_text())["omitted"]


def test_enable_auto_restores_deps(tmp_path):
    t = _mk_target(tmp_path)
    hs_cli.main(["skills", "--disable", "afk", "--disable", "scenario",
                 "--root", str(t)])
    assert not (_skills(t) / "afk").exists()
    assert not (_skills(t) / "scenario").exists()
    # enabling afk pulls its dep scenario back too
    hs_cli.main(["skills", "--enable", "afk", "--root", str(t)])
    assert (_skills(t) / "afk" / "SKILL.md").is_file()
    assert (_skills(t) / "scenario" / "SKILL.md").is_file()


def test_bare_lists_on_and_off(tmp_path, capsys):
    t = _mk_target(tmp_path)
    hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    capsys.readouterr()
    hs_cli.main(["skills", "--root", str(t)])
    out = capsys.readouterr().out
    assert "scenario" in out and "off" in out
    assert "plan" in out and "on" in out


def test_bare_list_dev_farm_offlisted_reads_off(tmp_path, capsys):
    # a skill still physically in skills/ but named in the dev off-list must read as
    # off, not on (the bare list is dev-farm-aware, not just stash-aware)
    t = _mk_target(tmp_path)
    offlist = t / ".harness-dev" / "dev-off-skills.yaml"
    offlist.parent.mkdir(parents=True)
    offlist.write_text("disabled:\n  - scenario\n")
    hs_cli.main(["skills", "--root", str(t)])
    out = capsys.readouterr().out
    assert (_skills(t) / "scenario" / "SKILL.md").is_file()  # still on disk, not stashed
    lines = out.splitlines()
    assert any(l.startswith("off ") and "scenario" in l for l in lines), out
    assert any(l.startswith("on ") and "plan" in l for l in lines), out


def test_disable_stash_collision_refused(tmp_path):
    # #1: a stale stash must NOT be nested into (data corruption). Disable refuses.
    t = _mk_target(tmp_path)
    (_stash(t) / "scenario").mkdir(parents=True)            # pre-existing stash
    (_stash(t) / "scenario" / "SKILL.md").write_text("stale")
    rc = hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    # the live skill dir is left intact; no nested dir created
    assert (_skills(t) / "scenario" / "SKILL.md").is_file()
    assert not (_stash(t) / "scenario" / "scenario").exists()
    assert rc != 0


def test_disable_unknown_skill_is_nonzero(tmp_path):
    # #5: disabling a non-existent skill must not report success
    t = _mk_target(tmp_path)
    rc = hs_cli.main(["skills", "--disable", "totallybogus", "--root", str(t)])
    assert rc != 0
    assert "totallybogus" not in json.loads(_omit(t).read_text())["omitted"]


def test_enable_unstashed_omitted_skill_is_nonzero(tmp_path):
    # #4: the documented recovery path for an install-omitted skill (no stash) must
    # NOT claim success while restoring nothing.
    t = _mk_target(tmp_path)
    # scenario omitted at install: dir absent, recorded, NO stash
    import shutil
    shutil.rmtree(_skills(t) / "scenario")
    _omit(t)  # ensure state dir
    (t / "harness/state/install-omitted-skills.json").write_text(
        json.dumps({"omitted": ["scenario"]}))
    rc = hs_cli.main(["skills", "--enable", "scenario", "--root", str(t)])
    assert rc != 0
    assert not (_skills(t) / "scenario").exists()


def _omit(t):
    (t / "harness/state").mkdir(parents=True, exist_ok=True)
    return t / "harness/state/install-omitted-skills.json"


def _git_commit_target(t):
    import subprocess
    def g(*a):
        subprocess.run(["git", "-C", str(t)] + list(a), capture_output=True, check=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", "-A")
    g("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")


def test_disable_stashes_into_plugin_tree_not_state(tmp_path):
    # The stash is a TRACKED sibling of skills/, not gitignored state/: an off skill
    # ships with the bundle. Disable must land the dir under plugins/hs/disabled-skills.
    t = _mk_target(tmp_path)
    rc = hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    assert rc == 0
    assert (t / "harness/plugins/hs/disabled-skills/scenario/SKILL.md").is_file()
    assert not (t / "harness/state/disabled-skills/scenario").exists()


def test_disable_in_tracked_repo_now_succeeds(tmp_path):
    # A git-tracked skill tree is no longer a footgun: the move is a git RENAME into a
    # tracked sibling, not a deletion into gitignored state/. Disable proceeds cleanly.
    t = _mk_target(tmp_path)
    _git_commit_target(t)
    rc = hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    assert rc == 0
    assert not (_skills(t) / "scenario").exists()
    assert (_stash(t) / "scenario" / "SKILL.md").is_file()


def _mk_floor_target(tmp: Path) -> Path:
    """A target whose core_immutable carries the off-skill proxy floor (use,
    find-skills, cleanup) beside the spine, to prove they are refused like any spine."""
    skills = tmp / "harness/plugins/hs/skills"
    for s in ["plan", "use", "find-skills", "cleanup", "journal"]:
        d = skills / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: hs:%s\n---\n# %s\n" % (s, s))
    data = tmp / "harness/data"
    data.mkdir(parents=True)
    (data / "skill-deps.yaml").write_text(
        "core_immutable: [plan, use, find-skills, cleanup]\n"
        "skills:\n"
        "  plan: {deps: []}\n"
        "  use: {deps: [find-skills]}\n"
        "  find-skills: {deps: [use]}\n"
        "  cleanup: {deps: []}\n"
        "  journal: {deps: []}\n")
    return tmp


def test_disable_refuses_use_findskills_cleanup(tmp_path):
    # the off-skill proxy floor is immutable: disabling it would strand the machinery
    # that reaches every OTHER off skill. Refused (exit 2, "spine core"), dir untouched.
    t = _mk_floor_target(tmp_path)
    for s in ["use", "find-skills", "cleanup"]:
        rc = hs_cli.main(["skills", "--disable", s, "--root", str(t)])
        assert rc != 0, s
        assert (_skills(t) / s / "SKILL.md").is_file()


def test_disable_dep_of_live_skill_warns_but_proceeds(tmp_path, capsys):
    # locking VL-8 into a contract: disabling a skill that a still-enabled skill lists
    # as a dep WARNS but does not refuse — an off skill being a dep of a live one is a
    # legitimate state (interview #23), not an error.
    t = _mk_target(tmp_path)  # afk (live) declares dep [scenario]
    rc = hs_cli.main(["skills", "--disable", "scenario", "--root", str(t)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dep of still-enabled" in err and "afk" in err
    assert not (_skills(t) / "scenario").exists()
