"""P10: pinned upstream version guard — NOTICE must record the current semver.

When the upstream drawio-skill or drawio-ai-kit is updated, bump the
pinned version here and in NOTICE.
"""
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTICE = REPO_ROOT / "harness/plugins/hs/skills/drawio/NOTICE"


@pytest.mark.dev_repo
def test_drawio_skill_version_pinned():
    """NOTICE must contain '1.14.0 @765a95b' for drawio-skill.

    dev_repo marker: this test reads a dev-level doc (NOTICE). In a
    production install the NOTICE file ships, but the upstream version
    only matters at dev time when deciding to pull.
    """
    text = NOTICE.read_text()
    # Check drawio-skill semver + SHA appears
    assert "1.14.0" in text, (
        "NOTICE missing drawio-skill version 1.14.0 — "
        "was upstream updated without bumping the pin?"
    )
    assert "765a95b" in text, (
        "NOTICE missing drawio-skill SHA 765a95b"
    )


@pytest.mark.dev_repo
def test_drawio_aikit_version_pinned():
    """NOTICE must contain 'bda82a2' for drawio-ai-kit."""
    text = NOTICE.read_text()
    assert "bda82a2" in text, (
        "NOTICE missing drawio-ai-kit SHA bda82a2"
    )
