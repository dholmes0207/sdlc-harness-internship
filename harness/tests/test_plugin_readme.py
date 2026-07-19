"""test_plugin_readme.py — generated per-plugin index READMEs.

Each plugin dir gets a README that indexes its skills (invoke name + one-line purpose),
its default on/off state, and how to enable the group. This is an INDEX (new navigation
info), deliberately NOT a per-skill README that would merely restate SKILL.md frontmatter
(DRY). The generator is idempotent and never clobbers a hand-written README (one lacking
the generated marker).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import generate_plugin_readme as gpr  # noqa: E402

_PLUGINS = _ROOT / "harness" / "plugins"


def test_every_plugin_has_a_readme_with_marker():
    # the generator runs as part of the build; assert the committed tree is covered
    for plug in sorted(_PLUGINS.iterdir()):
        if not (plug / ".claude-plugin" / "plugin.json").is_file():
            continue
        readme = plug / "README.md"
        assert readme.is_file(), "plugin %s missing README" % plug.name
        assert gpr.MARKER in readme.read_text(encoding="utf-8"), \
            "plugin %s README missing generated marker" % plug.name


@pytest.mark.dev_repo
def test_committed_readme_is_fresh():
    """The committed generated README must equal a fresh render — so a version bump
    (or a skill add/rename) that forgets to regenerate is caught, not shipped stale
    (this is exactly how the README froze at an old version). @dev_repo: on a
    default-off install the catalog is reduced, so the render differs by design."""
    for plug in sorted(_PLUGINS.iterdir()):
        if not (plug / ".claude-plugin" / "plugin.json").is_file():
            continue
        readme = plug / "README.md"
        if gpr.MARKER not in readme.read_text(encoding="utf-8"):
            continue  # hand-written, never regenerated
        assert readme.read_text(encoding="utf-8") == gpr.render_plugin_readme(plug), (
            "plugin %s README is STALE — run "
            "`python3 harness/scripts/generate_plugin_readme.py`" % plug.name)


def test_generation_is_idempotent(tmp_path):
    plug = _make_fake_plugin(tmp_path, "hs-demo", [("hs-demo:alpha", "Do alpha. Use for X.")])
    first = gpr.render_plugin_readme(plug)
    # write + regenerate -> identical
    (plug / "README.md").write_text(first, encoding="utf-8")
    second = gpr.render_plugin_readme(plug)
    assert first == second


def test_no_clobber_handwritten(tmp_path):
    plug = _make_fake_plugin(tmp_path, "hs-demo", [("hs-demo:alpha", "Do alpha.")])
    hand = "# Hand written, keep me\n"
    (plug / "README.md").write_text(hand, encoding="utf-8")
    wrote = gpr.write_plugin_readme(plug)
    assert wrote is False  # skipped (no marker)
    assert (plug / "README.md").read_text(encoding="utf-8") == hand


# ---- helpers ---------------------------------------------------------------

def _make_fake_plugin(tmp_path, name, skills):
    import json
    plug = tmp_path / name
    (plug / ".claude-plugin").mkdir(parents=True)
    (plug / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "description": "Demo plugin.", "version": "1.0.0"}),
        encoding="utf-8")
    for inv, desc in skills:
        sd = plug / "skills" / inv.split(":")[-1]
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(
            "---\nname: %s\ndescription: %s\n---\n\n# body\n" % (inv, desc), encoding="utf-8")
    return plug
