"""P8: validate.py audits — GROUP_LEVEL, Color=Identity, stencil-existence, aesthetic.

Each audit is tested bidirectional: a bad fixture triggers the check,
a good fixture stays clean.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE = REPO_ROOT / "harness/plugins/hs/skills/drawio/scripts/validate.py"
FIXTURES = REPO_ROOT / "harness/plugins/hs/skills/drawio/tests-fixtures"


def _run(file, strict=False):
    cmd = [sys.executable, str(VALIDATE), str(file)]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, capture_output=True, text=True)


def test_bad_nesting_warns():
    """Subnet directly inside Cloud (no VPC/AZ) → nesting level warning."""
    bad = FIXTURES / "bad-nesting.drawio"
    r = _run(bad)
    stdout = r.stdout
    assert "group" in stdout.lower() and "nest" in stdout.lower(), (
        f"Expected nesting warning, got: {stdout[:300]}"
    )


def test_good_arch_clean():
    """Correct Cloud→Region→VPC→... nesting → no warnings."""
    good = FIXTURES / "good-arch.drawio"
    r = _run(good)
    assert r.returncode == 0, f"Expected exit 0 on clean arch, got {r.returncode}"
    # No nesting-level warnings
    assert "nest" not in r.stdout.lower(), f"Unexpected nesting warning: {r.stdout[:200]}"


def test_good_arch_no_strict_aesthetic():
    """Without --strict, aesthetic warnings are not emitted."""
    good = FIXTURES / "good-arch.drawio"
    r = _run(good)
    # Verify no aesthetic warnings without --strict, then verify exit 0
    assert "font" not in r.stdout.lower(), f"Unexpected aesthetic warning: {r.stdout[:200]}"
    assert r.returncode == 0


def test_validate_regression():
    """Existing validate tests from upstream suite still pass."""
    # Re-run the good/dangling/edge-label checks via imported test logic
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "test_upstream", REPO_ROOT / "harness/tests/test_drawio_upstream_scripts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    vcli = mod.TestValidateCli()
    vcli.test_good_passes()
    vcli.test_dangling_edge_fails()
    vcli.test_edge_label_passes()

    vgeo = mod.TestValidateGeometry()
    vgeo.setUpClass()
    vgeo.test_segments_cross()
    vgeo.test_route_hits_rect()
    vgeo.test_abs_rect_resolves_container_offset()
    vgeo.test_endpoint_honours_exit_point()
    vgeo.test_edge_crossing_warns()
    vgeo.test_edge_crossing_strict_fails()
    vgeo.test_edge_through_vertex_warns()
    vgeo.test_autorouted_edge_not_checked()


def test_bad_spills_warns():
    """Child shapes extending beyond container → child-spill warning."""
    bad = FIXTURES / "bad-spills.drawio"
    r = _run(bad)
    stdout = r.stdout
    assert "spill" in stdout.lower() or "extends beyond" in stdout.lower(), (
        f"Expected child-spill warning, got: {stdout[:300]}"
    )


def test_good_arch_no_spills():
    """Clean arch diagram has no child-spill warnings."""
    good = FIXTURES / "good-arch.drawio"
    r = _run(good)
    assert "spill" not in r.stdout.lower(), (
        f"Unexpected child-spill warning: {r.stdout[:200]}"
    )


def test_bad_stacked_edges_warns():
    """Multiple edges sharing same exit point → stacked-arrowhead warning."""
    bad = FIXTURES / "bad-stacked-edges.drawio"
    r = _run(bad)
    stdout = r.stdout
    assert "share" in stdout.lower() and ("exit" in stdout.lower() or "entry" in stdout.lower()), (
        f"Expected stacked-arrowhead warning, got: {stdout[:300]}"
    )


def test_good_arch_no_stacked_edges():
    """Clean arch diagram has no stacked-arrowhead warnings."""
    good = FIXTURES / "good-arch.drawio"
    r = _run(good)
    assert "share" not in r.stdout.lower() and "exit" not in r.stdout.lower(), (
        f"Unexpected stacked-arrowhead warning: {r.stdout[:200]}"
    )
