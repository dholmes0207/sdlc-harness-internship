"""Tests for the standards_drift detector — the pure judgment behind the
standards-drift nudge.

`assess(paths)` answers: did this session edit architecture/standards-bearing
CODE under a watched tree without also touching one of the prose standards that
hs:plan / hs:cook auto-read? If so the loaded context risks drifting from reality
→ signal. Pure + deterministic: same path set → same signal (subjects sorted +
de-duped), tolerant of absolute or relative paths, silent on routine non-arch edits.

The watched trees + context docs are now config-driven (precedence:
$HARNESS_STANDARDS_CONFIG → shipped harness/data/standards.yaml → module constants).
The shipped default is GENERIC cross-language; THIS repo dogfoods its own harness/
trees via a .harness-dev override. To keep these unit tests hermetic (independent of
the ambient repo standards.yaml), the autouse fixture pins the watched config to the
harness dogfood trees; tests that exercise resolution override it explicitly.
"""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import standards_drift as sd  # noqa: E402

_HARNESS_TREES_CFG = (
    "drift:\n"
    "  watch_trees:\n"
    "    - harness/hooks/\n"
    "    - harness/scripts/\n"
    "    - harness/plugins/\n"
    "    - harness/data/\n"
    "    - harness/schemas/\n"
    "  context_docs:\n"
    "    - docs/system-architecture.md\n"
    "    - docs/harness/system-architecture.md\n"
    "    - docs/code-standards.md\n"
)


@pytest.fixture(autouse=True)
def _pin_harness_trees(tmp_path, monkeypatch):
    """Pin assess()'s watched config to the harness dogfood trees so the behavioral
    tests judge against a known set, not the ambient repo standards.yaml (whose
    shipped default is generic). Tests that assert resolution override this."""
    cfg = tmp_path / "pin-standards.yaml"
    cfg.write_text(_HARNESS_TREES_CFG, encoding="utf-8")
    monkeypatch.setenv("HARNESS_STANDARDS_CONFIG", str(cfg))


# --- core assess() behavior (against the pinned harness trees) ----------------

def test_code_edit_without_docs_signals():
    sig = sd.assess(["harness/hooks/foo.py"])
    assert sig is not None
    assert sig["type"] == "standards_drift"
    assert "harness/hooks/foo.py" in sig["subjects"]
    assert sig["total"] == 1


def test_arch_doc_touch_silences():
    assert sd.assess(["harness/hooks/foo.py", "docs/system-architecture.md"]) is None


def test_code_standards_touch_silences():
    assert sd.assess(["harness/scripts/bar.py", "docs/code-standards.md"]) is None


def test_full_doc_touch_silences():
    assert sd.assess(["harness/data/x.yaml", "docs/harness/system-architecture.md"]) is None


def test_docs_only_is_silent():
    assert sd.assess(["docs/system-architecture.md"]) is None


def test_non_arch_paths_silent():
    # tests/, plans/, root docs are not architecture/standards-bearing code
    assert sd.assess(["README.md", "plans/p/plan.md", "harness/tests/test_x.py"]) is None


def test_pycache_ignored():
    assert sd.assess(["harness/scripts/__pycache__/x.pyc"]) is None


def test_absolute_paths_handled():
    sig = sd.assess(["/home/u/repo/harness/data/stage-policy.yaml"])
    assert sig is not None
    assert sig["subjects"] == ["harness/data/stage-policy.yaml"]
    assert sig["total"] == 1


def test_subjects_deduped_and_sorted():
    sig = sd.assess(["harness/hooks/b.py", "harness/hooks/a.py", "harness/hooks/a.py"])
    assert sig["subjects"] == ["harness/hooks/a.py", "harness/hooks/b.py"]
    assert sig["total"] == 2


def test_empty_is_silent():
    assert sd.assess([]) is None


def test_subjects_capped():
    paths = ["harness/scripts/m%02d.py" % i for i in range(20)]
    sig = sd.assess(paths)
    assert sig["total"] == 20
    assert len(sig["subjects"]) == sd._MAX_SUBJECTS


# --- config-driven resolution ------------------------------------------------

def test_assess_uses_config_trees():
    # explicit injection wins over any config: only the injected tree counts
    assert sd.assess(["src/core/x.py"], trees=["src/core/"]) is not None
    assert sd.assess(["harness/hooks/x.py"], trees=["src/core/"]) is None


def test_assess_config_context_docs():
    # a non-default context doc silences when injected + touched
    sig = sd.assess(
        ["src/x.py", "ARCHITECTURE.md"],
        trees=["src/"],
        context_docs=["ARCHITECTURE.md"],
    )
    assert sig is None
    # ...and without touching it, the same edit signals
    assert sd.assess(["src/x.py"], trees=["src/"], context_docs=["ARCHITECTURE.md"]) is not None


def test_assess_env_override_wins(tmp_path, monkeypatch):
    cfg = tmp_path / "override-standards.yaml"
    cfg.write_text("drift:\n  watch_trees:\n    - custom/\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STANDARDS_CONFIG", str(cfg))
    trees, _docs = sd._load_config()
    assert "custom/" in trees
    assert "harness/hooks/" not in trees
    # assess honors the override: custom/ signals, the harness default does not
    assert sd.assess(["custom/x.py"]) is not None
    assert sd.assess(["harness/hooks/x.py"]) is None


def test_assess_fallback_when_no_config(tmp_path, monkeypatch):
    # a root whose standards.yaml has no drift: section, and no env override,
    # falls back to the module constants (regression lock)
    monkeypatch.delenv("HARNESS_STANDARDS_CONFIG", raising=False)
    data_dir = tmp_path / "harness" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "standards.yaml").write_text(
        "user_rules_dir: docs/standards/\n", encoding="utf-8")
    trees, docs = sd._load_config(root=tmp_path)
    assert trees == sd._ARCH_CODE_TREES
    assert docs == sd._CONTEXT_DOCS


def test_shipped_default_is_generic():
    # leak guard: the SHIPPED standards.yaml watches generic cross-language trees,
    # NEVER this repo's harness/ layout (which would leak onto every installer)
    import yaml
    shipped = Path(__file__).resolve().parent.parent / "data" / "standards.yaml"
    drift = yaml.safe_load(shipped.read_text(encoding="utf-8")).get("drift", {})
    trees = drift.get("watch_trees", [])
    assert "src/" in trees
    assert not any(t.startswith("harness/") for t in trees)
