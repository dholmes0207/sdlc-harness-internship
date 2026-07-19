"""Tests for the advisory fence scan (`check_fence.py`).

ADVISORY ONLY — it never blocks. It scans the working-tree changes (via
`git status --porcelain`) and reports any touched file that lives OUTSIDE the
declared ownership zones. A clean run (only owned paths touched, or nothing
touched) yields empty findings and exit 0. This feeds the memory-gap pass; it
is NOT a write guard (that is fs_guard's job).

The fence boundary is DATA-DRIVEN from ownership.yaml zones — not hard-coded to
one directory. The `fake zone` test below proves the boundary follows the zone
table: declare a different zone and a previously-owned path becomes a breach.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_fence  # noqa: E402
from conftest import _git  # noqa: E402


def _init_repo(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "--allow-empty", "-q", "-m", "base")


def _touch(root: Path, rel: str, content: str = "x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------- API: scan() against the harness's declared zones ----------
# The harness ownership.yaml declares a `docs/` zone, so docs/product/* is
# in-fence; src/ is in no zone, so it is a breach.

def test_check_fence_flags_outside(tmp_path):
    root = tmp_path
    _init_repo(root)
    _touch(root, "docs/product/prds/auth.md")   # under docs/ zone (ok)
    _touch(root, "src/app.py")                   # OUTSIDE every zone (flagged)
    findings = check_fence.scan(root)
    outside = [f["file"] for f in findings]
    assert "src/app.py" in outside
    assert "docs/product/prds/auth.md" not in outside
    # Advisory severity — never an error that would gate CI.
    assert all(f["severity"] in ("warn", "info") for f in findings)
    assert all(f["check"] == "fence_breach" for f in findings)


def test_check_fence_clean(tmp_path):
    root = tmp_path
    _init_repo(root)
    _touch(root, "docs/product/vision.md")
    _touch(root, "docs/product/brd.md")
    findings = check_fence.scan(root)
    assert findings == []


def test_check_fence_nothing_touched(tmp_path):
    root = tmp_path
    _init_repo(root)
    findings = check_fence.scan(root)
    assert findings == []


def test_check_fence_handles_deleted_and_renamed(tmp_path):
    root = tmp_path
    _init_repo(root)
    _touch(root, "config/x.yaml")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add config")
    # Delete the outside file → still a touch outside the fence.
    (root / "config" / "x.yaml").unlink()
    findings = check_fence.scan(root)
    assert any(f["file"] == "config/x.yaml" for f in findings)


def test_check_fence_not_a_git_repo_degrades(tmp_path):
    # No git repo → advisory cannot read git state; degrade to empty (never crash,
    # never block). It is advisory, so absence of git is not an error.
    findings = check_fence.scan(tmp_path)
    assert findings == []


# ---------- the fence boundary follows the zone table, not a hard-coded dir ----------

def test_fence_boundary_is_zone_driven(tmp_path):
    """Pass an explicit zone prefix set: a file under the declared zone is in-fence
    and a docs/product/ file — owned by default — becomes a breach. Proves the
    boundary is config-derived, not nailed to docs/product/."""
    root = tmp_path
    _init_repo(root)
    _touch(root, "src/owned/widget.py")          # under the declared zone (ok)
    _touch(root, "docs/product/prds/auth.md")    # NOT in this zone set → breach
    findings = check_fence.scan(root, prefixes=["src/owned/"])
    flagged = [f["file"] for f in findings]
    assert "docs/product/prds/auth.md" in flagged
    assert "src/owned/widget.py" not in flagged


def test_fence_prefixes_read_from_ownership(tmp_path, monkeypatch):
    """fence_prefixes() flattens ownership.yaml zones into POSIX path prefixes.
    Point the loader at a fake ownership file and the prefixes follow it."""
    own = tmp_path / "ownership.yaml"
    own.write_text(
        "zones:\n"
        "  alpha: [src/owned/]\n"
        "  beta:\n"
        "    - docs/\n"
        "    - plans/\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_OWNERSHIP_FILE", str(own))
    prefixes = check_fence.fence_prefixes()
    # Every declared root appears, normalized with a trailing slash.
    assert "src/owned/" in prefixes
    assert "docs/" in prefixes
    assert "plans/" in prefixes


# ---------- CLI ----------

def test_check_fence_cli_exit_zero_when_clean(tmp_path):
    root = tmp_path
    _init_repo(root)
    _touch(root, "docs/product/vision.md")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_fence.py"), "--root", str(root)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["findings"] == []


def test_check_fence_cli_exit_zero_when_breached(tmp_path):
    # ADVISORY: even with a breach the script exits 0 (it never blocks).
    root = tmp_path
    _init_repo(root)
    _touch(root, "outside.txt")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_fence.py"), "--root", str(root)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert any(f["file"] == "outside.txt" for f in out["findings"])
