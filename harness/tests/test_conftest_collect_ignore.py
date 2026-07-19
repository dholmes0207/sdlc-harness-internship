"""The shipped conftest drops test files coupled to a stashed (install-omitted)
skill from collection, so a default-off install runs the suite green instead of
erroring at import on a `sys.path.insert(plugins/hs/skills/<off>/scripts)`.

The coupling scan is verified on a SYNTHETIC tree so the assertion is identical on
the dev checkout (nothing stashed -> empty ignore -> full coverage) and on an
installed default-off copy (referenced-and-stashed -> file ignored)."""

import importlib.util
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

# Built by concatenation so THIS test file's own source never contains the
# contiguous `plugins/hs/skills/<name>/scripts` pattern — otherwise the live
# collect_ignore scan (which reads harness/tests/*.py) would ignore this very
# file. The synthetic test files written to tmp DO get the contiguous string.
_PFX = "plugins/hs/skills/"


def _scripts_ref(name: str) -> str:
    return f'SCRIPTS = "{_PFX}{name}' + '/scripts"\n'


def _load_conftest():
    # Unique module name so this standalone load never collides with pytest's own
    # conftest import machinery.
    spec = importlib.util.spec_from_file_location("_harness_conftest_probe", _CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skill(root: Path, name: str):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def test_file_coupled_to_stashed_skill_is_ignored(tmp_path):
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_drawio_edit.py").write_text(_scripts_ref("drawio"), encoding="utf-8")
    skills = tmp_path / "skills"; skills.mkdir()
    stash = tmp_path / "disabled-skills"
    _skill(stash, "drawio")  # in stash, absent from skills -> stashed
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert "test_drawio_edit.py" in out


def test_file_coupled_to_on_skill_is_not_ignored(tmp_path):
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_cook.py").write_text(_scripts_ref("cook"), encoding="utf-8")
    skills = tmp_path / "skills"; _skill(skills, "cook")
    stash = tmp_path / "disabled-skills"; stash.mkdir()
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert "test_cook.py" not in out


def test_hyphenated_skill_name_matches(tmp_path):
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_skillcreator_optimize.py").write_text(
        _scripts_ref("skill-creator"), encoding="utf-8")
    skills = tmp_path / "skills"; skills.mkdir()
    stash = tmp_path / "disabled-skills"; _skill(stash, "skill-creator")
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert "test_skillcreator_optimize.py" in out


def test_pathlib_segmented_scripts_path_matches(tmp_path):
    """skill-creator builds its scripts path as separate pathlib segments joined by
    the / operator, so the contiguous slash-path string never appears in source.
    The segmented form must still be detected."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    # concatenated so THIS file's source never holds the contiguous segmented
    # pattern (else the live collect_ignore would drop this very test on a target
    # where skill-creator is stashed — the L2 self-ignore trap).
    seg = 'Path(x) / "skills" / "' + 'skill-creator' + '" / "scripts"'
    (tests / "test_skillcreator_optimize.py").write_text(
        "_P = " + seg + "\n", encoding="utf-8")
    skills = tmp_path / "skills"; skills.mkdir()
    stash = tmp_path / "disabled-skills"; _skill(stash, "skill-creator")
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert "test_skillcreator_optimize.py" in out


def test_skill_md_reference_without_scripts_is_kept(tmp_path):
    """A test that names a stashed skill's SKILL.md/references as DATA (not a
    /scripts import) is NOT coupling — it must still be collected (e.g. the
    omit-record / hs-cli tests that exercise the default-off machinery itself)."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_omit_record.py").write_text(
        'P = "' + _PFX + 'drawio' + '/SKILL.md"\n', encoding="utf-8")
    skills = tmp_path / "skills"; skills.mkdir()
    stash = tmp_path / "disabled-skills"; _skill(stash, "drawio")
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert "test_omit_record.py" not in out


def test_no_stash_dir_returns_empty(tmp_path):
    """Dev tree has no disabled-skills/ dir -> empty ignore -> every test runs."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_drawio_edit.py").write_text(_scripts_ref("drawio"), encoding="utf-8")
    skills = tmp_path / "skills"; _skill(skills, "drawio")
    out = conf.stashed_skill_coupled_files(tests, skills, tmp_path / "absent")
    assert out == []


def test_skill_present_in_both_is_not_stashed(tmp_path):
    """A re-enabled skill lives in BOTH trees; it is ON, so its tests must run."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_repomix_batch.py").write_text(_scripts_ref("repomix"), encoding="utf-8")
    skills = tmp_path / "skills"; _skill(skills, "repomix")
    stash = tmp_path / "disabled-skills"; _skill(stash, "repomix")
    out = conf.stashed_skill_coupled_files(tests, skills, stash)
    assert out == []


# --- release-toolkit coupling: drop tests that import release/pack.py ----------
# Concatenated so THIS file's source never holds the contiguous `import pack`
# line — otherwise the live release_toolkit scan (which reads harness/tests/*.py)
# would drop this very test on an installed copy (the self-ignore trap).
_IMP = "import" + " pack"


def test_bare_release_import_is_detected(tmp_path):
    """A test doing a top-level `import pack` (the release build toolkit, which is
    NEVER shipped) errors at COLLECTION on an installed copy; it must be ignored."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_courier_probe.py").write_text(
        "import sys\n" + _IMP + "  # noqa\n", encoding="utf-8")
    out = conf.release_toolkit_coupled_files(tests)
    assert "test_courier_probe.py" in out


def test_from_pack_import_is_detected(tmp_path):
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_pack_from.py").write_text(
        "from" + " pack import build_tarball\n", encoding="utf-8")
    out = conf.release_toolkit_coupled_files(tests)
    assert "test_pack_from.py" in out


def test_non_release_import_is_kept(tmp_path):
    """A test that imports only shipped modules is NOT release-coupled -> kept."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_plain.py").write_text(
        "import os\nimport build_manifest\n", encoding="utf-8")
    out = conf.release_toolkit_coupled_files(tests)
    assert out == []


def test_substring_package_is_not_a_false_match(tmp_path):
    """`import package_utils` must NOT match the release-toolkit `pack` module."""
    conf = _load_conftest()
    tests = tmp_path / "tests"; tests.mkdir()
    (tests / "test_pkg.py").write_text(
        "import" + " package_utils\n", encoding="utf-8")
    out = conf.release_toolkit_coupled_files(tests)
    assert out == []


def test_real_release_coupled_tests_are_detected():
    """The two shipped dev-only tests that import release/pack.py must be caught by
    the scanner, so an installed copy drops them instead of aborting collection."""
    conf = _load_conftest()
    tests_dir = Path(__file__).resolve().parent
    out = conf.release_toolkit_coupled_files(tests_dir)
    assert "test_courier_tree.py" in out
    assert "test_harness_lifecycle.py" in out


class _RecordingConfig:
    """Minimal stand-in for pytest's config: records addinivalue_line calls so we
    can assert which markers the shipped conftest declares without a live run."""
    def __init__(self):
        self.lines = []

    def addinivalue_line(self, name, line):
        self.lines.append((name, line))


def test_shipped_conftest_registers_live_lane_markers():
    """pyproject.toml (marker registry) is NOT shipped, so the conftest must
    declare the live-lane marks itself or an installed copy warns PytestUnknownMark
    on every real_* test. dev_repo travels too."""
    conf = _load_conftest()
    cfg = _RecordingConfig()
    conf.pytest_configure(cfg)
    declared = {name.split(":")[0] for kind, name in cfg.lines if kind == "markers"}
    for mark in ("dev_repo", "real_gemini", "real_agy", "real_sdk", "real_host"):
        assert mark in declared, f"{mark} not registered by shipped conftest"


