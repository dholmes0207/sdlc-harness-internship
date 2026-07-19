"""test_glossary_invariants.py — guards docs/GLOSSARY.md, the canonical shared-language registry.

The glossary consolidates terms scattered across CLAUDE.md, docs/decisions.md and
old plan notes into one English reference that the planning skills read before they
name things. These invariants keep it from being deleted, gutted, or drifting out of
sync with the wording bans that test_bug_class_invariants enforces over harness/.

Post-migration the SSOT is docs/glossary.yaml and GLOSSARY.md is a rendered VIEW
(glossary_register.py). The invariants below also pin the SSOT and a render-drift
gate so the committed view never falls out of sync with its source.
"""
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GLOSSARY = _REPO / "docs" / "GLOSSARY.md"
_GLOSSARY_YAML = _REPO / "docs" / "glossary.yaml"

_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Terms pinned in CLAUDE.md "Wording" plus the DEC-backed vocabulary. Each must keep
# a row so the consolidation can't be quietly hollowed out back into scattered prose.
_CORE_TERMS = (
    "fs_guard", "actor", "presence gate", "HOOK_CLASS", "fail-closed", "append-only",
)

# The four documented columns of the glossary table.
_COLUMNS = ("Term", "Definition", "Forbidden", "Backing")

# The fs_guard ban, held as a pattern so this source file never carries the
# contiguous banned string — the harness-wide scan in test_bug_class_invariants
# would otherwise flag this file. The bracketed source text does not match its own
# regex, mirroring how that scan stays self-consistent.
_FORBIDDEN_FS_GUARD = re.compile(r"write[- ]fence", re.I)


def _glossary_text():
    return _GLOSSARY.read_text(encoding="utf-8")


class TestGlossaryRegistry:
    @pytest.fixture(autouse=True)
    def _skip_when_not_shipped(self):
        # docs/GLOSSARY.md is a source-repo doc, NOT part of the installed bundle.
        # These tests ship under harness/tests/ and run at deployer sites too;
        # there the file is legitimately absent, so skip rather than false-fail.
        if not _GLOSSARY.is_file():
            pytest.skip("docs/GLOSSARY.md absent (source-only doc, not shipped)")

    def test_glossary_exists(self):
        # hs:plan / hs:discover now point at this path; a missing file turns those
        # read steps into dangling instruction references.
        assert _GLOSSARY.is_file(), (
            "docs/GLOSSARY.md is missing — the planning skills reference it")

    def test_glossary_has_a_term_table(self):
        text = _glossary_text()
        missing = [c for c in _COLUMNS if c not in text]
        assert not missing, (
            "GLOSSARY.md must keep its Term/Definition/Forbidden/Backing table; "
            "missing column header(s): %s" % ", ".join(missing))

    def test_core_terms_each_have_a_row(self):
        text = _glossary_text()
        missing = [t for t in _CORE_TERMS if t not in text]
        assert not missing, (
            "GLOSSARY.md lost canonical term(s): %s" % ", ".join(missing))

    def test_fs_guard_forbidden_wording_is_registered(self):
        # The human-readable registry must name the ban that the harness-wide scan
        # enforces, so the glossary stays the single source of the forbidden wording.
        assert _FORBIDDEN_FS_GUARD.search(_glossary_text()), (
            "GLOSSARY.md must register the forbidden fs_guard framing it bans")


class TestGlossarySSOT:
    """The YAML SSOT (docs/glossary.yaml) is a dev-repo artifact, like the
    rendered view: absent on installed copies, so these are dev_repo-marked."""

    @pytest.fixture(autouse=True)
    def _skip_when_not_shipped(self):
        if not _GLOSSARY_YAML.is_file():
            pytest.skip("docs/glossary.yaml absent (source-only SSOT, not shipped)")

    @pytest.mark.dev_repo
    def test_ssot_parses_and_carries_core_terms(self):
        import glossary_register as gr
        terms = {t["term"] for t in gr.list_terms(_REPO)}
        # core terms are stored with their backtick/qualifier wrapping; assert by
        # substring so `presence gate` matches `` `presence gate` (W1) ``.
        for core in _CORE_TERMS:
            assert any(core in t for t in terms), (
                "glossary.yaml SSOT lost canonical term %r" % core)

    @pytest.mark.dev_repo
    def test_rendered_view_is_in_sync_with_ssot(self):
        # Drift gate: render in-memory from the SSOT and compare to the committed
        # GLOSSARY.md. Any difference means the view was hand-edited or the SSOT
        # changed without a re-render — re-run `glossary_register.py --render`.
        import glossary_register as gr
        assert not gr.check_drift(_REPO), (
            "docs/GLOSSARY.md is out of sync with docs/glossary.yaml — "
            "re-render: python3 harness/scripts/glossary_register.py --render")

    @pytest.mark.dev_repo
    def test_forbidden_fs_guard_wording_survives_render(self):
        # The rendered view must still carry the fs_guard ban after a render
        # (the migration preserves it; test_bug_class_invariants depends on it).
        assert _FORBIDDEN_FS_GUARD.search(_GLOSSARY.read_text(encoding="utf-8"))

    @pytest.mark.dev_repo
    def test_view_carries_generated_marker(self):
        import glossary_register as gr
        assert gr.GENERATED_MARKER in _GLOSSARY.read_text(encoding="utf-8"), (
            "the rendered view must carry the GENERATED_MARKER (no-clobber + "
            "drift signal)")
