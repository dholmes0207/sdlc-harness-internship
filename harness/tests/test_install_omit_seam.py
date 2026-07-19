"""verify_install omit-seam: a deliberately-omitted skill dir is not drift.

Dir-omission is the install-time disable for the collapsed single-hs plugin. The
omitted skill's files are in the manifest (the source ships them) but absent from
the target. verify_install must read the install-recorded omit list and exclude
exactly those skill dirs from its hash/presence loop — and NOT over-exclude: a
file missing for any OTHER reason must still be reported.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import verify_install as vi  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_target(tmp: Path, omitted_recorded):
    """A minimal target tree: 3 skills in the manifest; 'kept' on disk, 'gone' and
    'dropped' absent. `omitted_recorded` is the install-recorded omit list."""
    skills = ["kept", "gone", "dropped"]
    files = {}
    for s in skills:
        rel = "harness/plugins/hs/skills/%s/SKILL.md" % s
        body = "---\nname: hs:%s\n---\n# %s\n" % (s, s)
        if s == "kept":
            _write(tmp / rel, body)
        files[rel] = vi.sha256_bytes(body.encode("utf-8")) \
            if hasattr(vi, "sha256_bytes") else __import__("hashlib").sha256(
                body.encode("utf-8")).hexdigest()
    _write(tmp / "harness/manifest.json", json.dumps({"files": files}))
    if omitted_recorded is not None:
        _write(tmp / "harness/state/install-omitted-skills.json",
               json.dumps({"omitted": omitted_recorded}))
    return tmp


def test_recorded_omit_is_not_drift(tmp_path):
    _make_target(tmp_path, omitted_recorded=["gone", "dropped"])
    problems = vi.verify(tmp_path)
    # both omitted skills are excluded — clean despite being absent on disk
    assert problems == [], "recorded-omitted skills must not read as drift: %s" % problems


def test_unrecorded_missing_is_still_drift(tmp_path):
    # only 'gone' is recorded as omitted; 'dropped' is genuinely missing
    _make_target(tmp_path, omitted_recorded=["gone"])
    problems = vi.verify(tmp_path)
    rels = [r for r, _ in problems]
    assert "harness/plugins/hs/skills/dropped/SKILL.md" in rels, \
        "an unrecorded missing file must still be reported"
    assert "harness/plugins/hs/skills/gone/SKILL.md" not in rels, \
        "the recorded-omitted skill must be excluded"


def test_no_omit_file_means_strict_as_before(tmp_path):
    # no install-omitted-skills.json -> the seam is inert, every absent file drifts
    _make_target(tmp_path, omitted_recorded=None)
    problems = vi.verify(tmp_path)
    rels = sorted(r for r, _ in problems)
    assert rels == [
        "harness/plugins/hs/skills/dropped/SKILL.md",
        "harness/plugins/hs/skills/gone/SKILL.md",
    ]
