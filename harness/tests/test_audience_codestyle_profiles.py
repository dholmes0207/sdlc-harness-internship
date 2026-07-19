"""test_audience_codestyle_profiles.py — split 6 profiles into audience (prose) + code-style halves.

Tests written BEFORE implementation (TDD red phase).
"""

import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "output-styles"

AUDIENCE_FILES = [DATA_DIR / f"audience-level-{i}.md" for i in range(6)]
CODE_STYLE_FILES = [DATA_DIR / f"code-style-level-{i}.md" for i in range(6)]


def test_audience_profiles_present_0_to_5():
    """6 audience-level-*.md files must exist, no 'junior dev' job-title label in audience role."""
    for p in AUDIENCE_FILES:
        assert p.exists(), f"Missing audience profile: {p.name}"
        content = p.read_text(encoding="utf-8").lower()
        # R4: audience is labeled by jargon-tolerance behaviour, not job title
        # "junior dev" or "junior developer" as an audience label is forbidden
        # (they can mention the concept, but not as the NAME for the audience tier)
        lines = content.splitlines()
        for line in lines:
            # name:/description: frontmatter must not use "junior dev" as the audience identity
            if line.strip().startswith("name:") or line.strip().startswith("description:"):
                assert "junior dev" not in line.lower(), (
                    f"{p.name} name/description labels audience as 'junior dev' — "
                    "R4: use behaviour-based label (jargon tolerance), not job title"
                )


def test_audience_plain_has_sowhat_and_glossary():
    """audience-level-0 and -1 (plain registers) MUST contain:
    1. a 'so what' opener directive
    2. inline-definition directive
    3. glossary directive at report end
    """
    for level in (0, 1):
        p = AUDIENCE_FILES[level]
        assert p.exists(), f"Missing: {p.name}"
        content = p.read_text(encoding="utf-8").lower()
        # Check for "so what" directive
        assert "so what" in content, (
            f"{p.name} missing 'so what' opener directive (design §2)"
        )
        # Check for inline definition directive
        assert any(tok in content for tok in ("inline", "define", "definition")), (
            f"{p.name} missing inline-definition directive (design §8)"
        )
        # Check for glossary directive
        assert "glossary" in content, (
            f"{p.name} missing glossary directive (design §2 + §8)"
        )


def test_resolvers_return_name_and_file():
    """audience_profile(N) and code_style_profile(N) return {name, file} for 0..5;
    None input returns None."""
    import sys
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import voice_prefs

    for level in range(6):
        result = voice_prefs.audience_profile(level)
        assert result is not None, f"audience_profile({level}) returned None"
        assert "name" in result, f"audience_profile({level}) missing 'name'"
        assert "file" in result, f"audience_profile({level}) missing 'file'"
        p = Path(result["file"])
        assert p.exists(), f"audience_profile({level}) file does not exist: {p}"

        cs = voice_prefs.code_style_profile(level)
        assert cs is not None, f"code_style_profile({level}) returned None"
        assert "name" in cs, f"code_style_profile({level}) missing 'name'"
        assert "file" in cs, f"code_style_profile({level}) missing 'file'"
        cp = Path(cs["file"])
        assert cp.exists(), f"code_style_profile({level}) file does not exist: {cp}"

    # None input -> None
    assert voice_prefs.audience_profile(None) is None
    assert voice_prefs.audience_profile(99) is None


def test_no_evidence_directive_in_audience():
    """Audience profiles MUST NOT positively direct the model to alter code or evidence.
    The scope-fence invariant must be present (asserting evidence is unchanged).
    """
    for p in AUDIENCE_FILES:
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8")
        content_lower = content.lower()
        # MUST contain the scope-fence invariant statement
        assert "scope fence" in content_lower or "invariant" in content_lower, (
            f"{p.name} missing scope-fence / invariant section"
        )
        # MUST state that evidence tokens are NOT altered
        assert any(tok in content_lower for tok in ("evidence token", "file:line", "evidence is invariant")), (
            f"{p.name} missing evidence-invariant statement"
        )
        # MUST NOT issue a positive MUST/ADD/REWRITE directive that targets code content
        # (negative uses like "NEVER rewrite code" are fine — they enforce the fence)
        lines = [ln for ln in content.splitlines() if not re.search(r"never|not|don.t|must not|forbidden", ln, re.IGNORECASE)]
        for line in lines:
            assert not re.search(r"\bmust\b.*\brewrite\s+code\b", line, re.IGNORECASE), (
                f"{p.name} positive directive to rewrite code found: {line!r}"
            )
            assert not re.search(r"\bmust\b.*\bchange\s+file:line\b", line, re.IGNORECASE), (
                f"{p.name} positive directive to change file:line found: {line!r}"
            )
