"""Catalog token-cost measurement: sum of SKILL.md `description:` bytes across
skills the host loads (live) vs omitted (off). bytes/4 ≈ tokens — the diet proxy.

Rescoped P3 (DECISIONS.md D-01): the cache-flip machinery was dropped (plugin loads
live from directory-source, no cache), so P3's surviving deliverable is this
measurement. Floor-disjoint invariants for both off-lists already live in
test_skill_defaults.py (ship) and test_dev_skill_farm.py (dev)."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import measure_catalog_tokens as mct  # noqa: E402
import skill_frontmatter as sf  # noqa: E402


def _skill(root: pathlib.Path, name: str, desc: str, *, folded: bool = False) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if folded:
        fm = f"---\nname: hs:{name}\ndescription: >\n  {desc}\n---\nbody\n"
    else:
        fm = f'---\nname: hs:{name}\ndescription: "{desc}"\n---\nbody\n'
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    _skill(root, "alpha", "AAAA")          # 4 bytes
    _skill(root, "beta", "BBBBBB")         # 6 bytes
    _skill(root, "gamma", "CCCCCCCC")      # 8 bytes
    return root


def test_no_off_list_all_live(tree):
    r = mct.measure(tree)
    assert r["live_count"] == 3 and r["off_count"] == 0
    assert r["live_desc_bytes"] == 4 + 6 + 8
    assert r["off_desc_bytes"] == 0


def test_off_split_moves_bytes_to_off_bucket(tree):
    r = mct.measure(tree, off_names=["beta", "gamma"])
    assert r["live_count"] == 1 and r["off_count"] == 2
    assert r["live_desc_bytes"] == 4
    assert r["off_desc_bytes"] == 6 + 8
    assert r["total_desc_bytes"] == 4 + 6 + 8


def test_est_tokens_are_bytes_over_four(tree):
    r = mct.measure(tree, off_names=["gamma"])
    assert r["est_tokens_saved"] == round(8 / 4)
    assert r["est_tokens_live"] == round((4 + 6) / 4)


def test_folded_description_parsed(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    _skill(root, "fold", "FOLDED-EIGHT", folded=True)  # 12 bytes
    r = mct.measure(root)
    assert r["live_desc_bytes"] == len(b"FOLDED-EIGHT")


def test_fallback_regex_when_yaml_missing(tree, monkeypatch):
    # Force the yaml-less path: single-line descriptions still parse via regex.
    monkeypatch.setattr(sf, "_YAML", False)
    r = mct.measure(tree)
    assert r["live_desc_bytes"] == 4 + 6 + 8


def test_fallback_undercounts_folded_description(tmp_path, monkeypatch):
    # Pin the known degraded shape: without yaml, a folded value collapses to the
    # `>` indicator (1 byte), not the folded body. Guards against silent drift.
    root = tmp_path / "skills"
    root.mkdir()
    _skill(root, "fold", "FOLDED-BODY", folded=True)
    monkeypatch.setattr(sf, "_YAML", False)
    r = mct.measure(root)
    assert r["live_desc_bytes"] == len(b">")


def test_dir_without_skill_md_skipped(tree):
    (tree / "empty").mkdir()
    r = mct.measure(tree)
    assert r["live_count"] == 3  # empty/ ignored


def test_off_list_loader_reads_disabled_key(tmp_path):
    p = tmp_path / "off.yaml"
    p.write_text("disabled:\n  - beta\n  - gamma\n", encoding="utf-8")
    assert set(mct._load_off_list(str(p))) == {"beta", "gamma"}


def test_off_list_loader_none_is_empty():
    assert mct._load_off_list(None) == []


def test_cli_emits_json(tree, capsys):
    rc = mct.main(["--skills-root", str(tree), "--off", "beta"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"off_count": 1' in out
    assert '"live_desc_bytes": 12' in out  # alpha(4)+gamma(8)
