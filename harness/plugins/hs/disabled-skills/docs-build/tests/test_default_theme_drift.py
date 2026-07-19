"""FIX-25 — Drift-guard: default-theme/assets shell files phải byte-identical với
docs/showcase/assets tương ứng.

Chống drift im lặng khi 2 bản cùng tồn tại và bị sửa riêng.
01-core-shell.js KHÔNG so vì đây là bản strip — khác 01-core.js trong overlay.

Drift only has meaning between TWO COPIES OF THE SAME THEME. The guard therefore
runs ONLY when docs/showcase is a verbatim clone of the default-theme — detected
by 01-base.css being byte-identical. A showcase that ships its OWN theme (its own
01-base.css, e.g. the harness split with harness-specific components) is not a
clone, has nothing to drift against, and skips. Keying on byte-identity (not on a
filename's mere presence) is self-correcting: it stays skipped even after a custom
theme is split into the same numbered files.
"""
import hashlib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_DEFAULT_THEME = _REPO / "harness" / "plugins" / "hs" / "skills" / "docs-build" / "default-theme" / "assets"
_SHOWCASE = _REPO / "docs" / "showcase" / "assets"


def _is_default_theme_clone() -> bool:
    """True only if the showcase 01-base.css is a verbatim copy of the default-theme."""
    base, ref = _SHOWCASE / "css" / "01-base.css", _DEFAULT_THEME / "css" / "01-base.css"
    if not (base.exists() and ref.exists()):
        return False
    return base.read_bytes() == ref.read_bytes()


pytestmark = pytest.mark.skipif(
    not _is_default_theme_clone(),
    reason="docs/showcase ships its own theme, not a verbatim default-theme clone — drift N/A",
)

# (default-theme relative path, showcase relative path)
_PAIRS = [
    ("css/01-base.css",       "css/01-base.css"),
    ("css/02-components.css", "css/02-components.css"),
    ("css/05-layout.css",     "css/05-layout.css"),
    ("css/06-print-a11y.css", "css/06-print-a11y.css"),
    ("lib/three.min.js",      "lib/three.min.js"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("dt_rel,sc_rel", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_shell_file_byte_identical(dt_rel, sc_rel):
    """default-theme shell file byte-identical với showcase overlay tương ứng."""
    dt_file = _DEFAULT_THEME / dt_rel
    sc_file = _SHOWCASE / sc_rel

    assert dt_file.exists(), f"default-theme file không tồn tại: {dt_file}"
    assert sc_file.exists(), f"showcase file không tồn tại: {sc_file}"

    dt_hash = _sha256(dt_file)
    sc_hash = _sha256(sc_file)

    assert dt_hash == sc_hash, (
        f"DRIFT DETECTED: {dt_rel}\n"
        f"  default-theme: {dt_hash}\n"
        f"  showcase:      {sc_hash}\n"
        f"  Sync lại 2 bản để tránh drift im lặng."
    )
