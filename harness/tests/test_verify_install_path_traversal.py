"""test_verify_install_path_traversal.py — a marketplace `source` must stay
inside harness/plugins.

verify_install.plugin_presence_problems() joins each marketplace `source` under
harness/plugins/ and probes it with is_file(). A crafted source that climbs out
via `..` or an absolute path would resolve OUTSIDE the plugins dir — the probe
then reads attacker-chosen filesystem locations. The fix matches the install.py
containment precedent: reject absolute sources and any `..` climb, flagged as
install drift (low severity — only is_file checks, no copy/exec).
"""
import json
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import verify_install as vi  # noqa: E402


def _mk_with_source(tmp_path, source):
    root = tmp_path / "r"
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    (mpdir / "marketplace.json").write_text(json.dumps(
        {"name": "hs-local",
         "plugins": [{"name": "evil", "source": source}]}), encoding="utf-8")
    return root


def test_dotdot_climb_source_flagged(tmp_path):
    root = _mk_with_source(tmp_path, "../../etc/x")
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    rel, prob = probs[0]
    assert "evil" in prob
    # named as a containment refusal, not a "missing plugin.json" message
    assert "escape" in prob.lower() or "outside" in prob.lower()


def test_absolute_source_flagged(tmp_path):
    root = _mk_with_source(tmp_path, "/etc/passwd")
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    _rel, prob = probs[0]
    assert "escape" in prob.lower() or "outside" in prob.lower()


def test_dotdot_climb_does_not_probe_outside(tmp_path):
    # Even if the escaped target genuinely exists on disk, the escaping source
    # must still be flagged (containment is independent of what is out there).
    outside = tmp_path / "outside"
    pdir = outside / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text("{}", encoding="utf-8")
    (outside / "skills").mkdir()
    root = _mk_with_source(tmp_path, "../../outside")
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    assert "escape" in probs[0][1].lower() or "outside" in probs[0][1].lower()


def test_normal_in_tree_source_not_flagged_as_escape(tmp_path):
    root = tmp_path / "r"
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    (mpdir / "marketplace.json").write_text(json.dumps(
        {"name": "hs-local",
         "plugins": [{"name": "hs", "source": "./hs"}]}), encoding="utf-8")
    pdir = root / "harness" / "plugins" / "hs" / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "hs"}),
                                      encoding="utf-8")
    (root / "harness" / "plugins" / "hs" / "skills").mkdir()
    assert vi.plugin_presence_problems(root) == []
