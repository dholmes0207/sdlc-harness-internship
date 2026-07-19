"""The curated dev skill farm exposes every repo skill EXCEPT the personal dev
off-list (.harness-dev/dev-off-skills.yaml). Mechanics run on a synthetic tree;
the live drift check (does the farm match the recorded off-list exactly?) is
@dev_repo — it needs the full repo + the dev config, so it skips on an installed
copy and on a dev tree that has no off-list file."""

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "harness" / "scripts"))
import dev_skill_farm as farm  # noqa: E402

_HS_CLI = _ROOT / "harness" / "scripts" / "hs_cli.py"


def _mk_repo(tmp: Path, skills, deps_floor):
    """A minimal repo tree: plugins/.claude-plugin, plugins/hs/{agents,skills}."""
    plug = tmp / "harness" / "plugins"
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
    hs = plug / "hs"
    (hs / "agents").mkdir(parents=True)
    (hs / "agents" / "a.md").write_text("x", encoding="utf-8")
    sk = hs / "skills"
    sk.mkdir()
    for name in skills:
        (sk / name).mkdir()
        (sk / name / "SKILL.md").write_text("# %s\n" % name, encoding="utf-8")
    for res in ("common", "_shared"):  # resource dirs: no SKILL.md
        (sk / res).mkdir()
        (sk / res / "lib.py").write_text("x", encoding="utf-8")
    data = tmp / "harness" / "data"
    data.mkdir(parents=True)
    (data / "skill-deps.yaml").write_text(
        "core_immutable: [%s]\nskills:\n" % ", ".join(deps_floor)
        + "".join("  %s: {deps: []}\n" % s for s in skills), encoding="utf-8")
    return tmp


def test_build_exposes_on_skills_and_resources_drops_off(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe", "drawio"], ["plan", "use"])
    farm_dir = tmp_path / "farm"
    res = farm.build_farm(tmp_path, farm_dir, off={"vibe", "drawio"})
    exposed = farm.exposed_skills(farm_dir)
    assert exposed == {"plan", "use"}          # off skills dropped
    assert res == {"exposed": 2, "off": 2}
    # resource dirs always exposed (symlinked), so an ON skill importing them works
    assert (farm_dir / "hs" / "skills" / "common").is_dir()
    assert (farm_dir / "hs" / "skills" / "_shared").is_dir()
    # non-skills plugin content is symlinked through
    assert (farm_dir / "hs" / "agents" / "a.md").is_file()
    assert (farm_dir / ".claude-plugin" / "marketplace.json").is_file()


def test_build_refuses_a_floor_skill(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    with pytest.raises(ValueError):
        farm.build_farm(tmp_path, tmp_path / "farm", off={"use"})  # use is floor


class TestBinRootPointer:
    def test_global_mode_exposes_repo_as_bin(self, tmp_path):
        # the pointer another project sets to consume THIS repo as its bin is the
        # repo's own resolved root.
        assert farm.bin_root_pointer(tmp_path) == str(tmp_path.resolve())

    def test_cli_bin_root_prints_env_line(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(_ROOT / "harness" / "scripts" / "dev_skill_farm.py"),
             "--root", str(tmp_path), "--bin-root"],
            capture_output=True, text=True)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "HARNESS_BIN_ROOT=%s" % tmp_path.resolve()
        # the note steers the value to the OTHER project, not this repo
        assert "other project" in proc.stderr.lower()

    def test_floor_skills_still_protected(self, tmp_path):
        # the bin-root mode is additive — the off-list floor guard is unchanged.
        _mk_repo(tmp_path, ["plan", "use", "find-skills"], ["plan", "use", "find-skills"])
        assert "use" in farm._floor(tmp_path)


def test_rebuild_is_idempotent(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    farm_dir = tmp_path / "farm"
    farm.build_farm(tmp_path, farm_dir, off={"vibe"})
    farm.build_farm(tmp_path, farm_dir, off={"vibe"})  # second run must not raise
    assert farm.exposed_skills(farm_dir) == {"plan", "use"}


def test_off_list_missing_returns_none(tmp_path):
    _mk_repo(tmp_path, ["plan"], ["plan"])
    assert farm.load_off_list(tmp_path) is None   # no .harness-dev/dev-off-skills.yaml


def _write_record(root, names, header="# personal off-list\n"):
    p = root / ".harness-dev" / "dev-off-skills.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + "disabled:\n" + "".join("  - %s\n" % n for n in names),
                 encoding="utf-8")
    return p


def test_toggle_record_adds_and_preserves_header_comment(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe", "drawio"], ["plan", "use"])
    p = _write_record(tmp_path, ["vibe"], header="# KEEP ME\n")
    new = farm.toggle_record(tmp_path, add=["drawio"])
    assert new == {"vibe", "drawio"}
    text = p.read_text()
    assert "# KEEP ME" in text            # header comment survives
    assert "- drawio" in text and "- vibe" in text


def test_toggle_record_remove_drops_the_line(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe", "drawio"], ["plan", "use"])
    p = _write_record(tmp_path, ["vibe", "drawio"])
    new = farm.toggle_record(tmp_path, remove=["vibe"])
    assert new == {"drawio"}
    assert "- vibe" not in p.read_text()


def test_toggle_record_add_is_idempotent(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    p = _write_record(tmp_path, ["vibe"])
    farm.toggle_record(tmp_path, add=["vibe"])   # already present
    assert p.read_text().count("- vibe") == 1


def test_validate_off_flags_floor_and_unknown(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    bad = farm.validate_off(tmp_path, ["vibe", "use", "ghost"])
    assert any("use" in b and "floor" in b for b in bad)
    assert any("ghost" in b for b in bad)
    assert not any("vibe" in b for b in bad)      # vibe is a legit off candidate


# --- live drift check: the recorded off-list is real + the farm matches it ------

def _live_off():
    p = _ROOT / ".harness-dev" / "dev-off-skills.yaml"
    if not p.is_file():
        pytest.skip("no personal dev off-list on this checkout")
    return farm.load_off_list(_ROOT)


@pytest.mark.dev_repo
def test_recorded_off_list_names_are_real_skills():
    off = _live_off()
    unknown = off - farm.all_skills(_ROOT)
    assert not unknown, "dev-off-skills.yaml lists non-existent skills: %s" % sorted(unknown)


@pytest.mark.dev_repo
def test_recorded_off_list_excludes_the_floor():
    off = _live_off()
    floor = set(farm._floor(_ROOT))
    assert not (off & floor), "dev-off-skills.yaml disables floor skills: %s" % sorted(off & floor)


@pytest.mark.dev_repo
def test_built_farm_exposes_exactly_all_minus_off(tmp_path):
    off = _live_off()
    farm_dir = tmp_path / "farm"
    farm.build_farm(_ROOT, farm_dir, off)
    assert farm.exposed_skills(farm_dir) == farm.all_skills(_ROOT) - off


# --- hs_cli skills --off/--on integration (both modes) --------------------------

def _cli(root, *flags):
    return subprocess.run([sys.executable, str(_HS_CLI), "skills", *flags,
                           "--root", str(root)], capture_output=True, text=True)


def test_cli_off_on_dev_farm_mode(tmp_path):
    """With a dev off-list present, --off/--on edit the record + rebuild the farm
    (repo skills/ untouched) and print the restart reminder."""
    _mk_repo(tmp_path, ["plan", "use", "vibe", "drawio", "shopify"], ["plan", "use"])
    _write_record(tmp_path, ["vibe"])
    r = _cli(tmp_path, "--off", "drawio,shopify")
    assert r.returncode == 0, r.stderr
    assert "restart" in r.stdout.lower()
    assert farm.load_off_list(tmp_path) == {"vibe", "drawio", "shopify"}
    exposed = farm.exposed_skills(tmp_path / ".harness-dev" / "hs-plugins")
    assert exposed == {"plan", "use"}
    # skills/ in the repo is untouched — the off skills still live there
    assert (tmp_path / "harness/plugins/hs/skills/drawio/SKILL.md").is_file()
    r2 = _cli(tmp_path, "--on", "vibe")
    assert r2.returncode == 0
    assert farm.load_off_list(tmp_path) == {"drawio", "shopify"}


def test_cli_off_refuses_floor_in_farm_mode(tmp_path):
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    _write_record(tmp_path, [])
    r = _cli(tmp_path, "--off", "use")
    assert r.returncode == 2
    assert "floor" in r.stderr


def test_cli_off_install_mode_moves_in_tree(tmp_path):
    """No dev off-list -> installed-copy semantics: --off dir-omits into the stash
    (skills/ loses the dir) and reminds to restart."""
    _mk_repo(tmp_path, ["plan", "use", "vibe"], ["plan", "use"])
    # no .harness-dev/dev-off-skills.yaml -> install mode
    r = _cli(tmp_path, "--off", "vibe")
    assert r.returncode == 0, r.stderr
    assert "restart" in r.stdout.lower()
    assert not (tmp_path / "harness/plugins/hs/skills/vibe").exists()   # moved out
    assert (tmp_path / "harness/plugins/hs/disabled-skills/vibe/SKILL.md").is_file()
