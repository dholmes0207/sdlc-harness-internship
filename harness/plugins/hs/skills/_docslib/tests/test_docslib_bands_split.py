"""C1 — tách design taxonomy (bands.yaml) ⟂ presentation (_present/) khỏi merged showcase.yaml.

load_model đọc `band` từ `_index/bands.yaml` và `order`/`text_fix` từ `_present/*`; parity với
file `showcase.yaml` gộp cũ; shim back-compat khi CHỈ có legacy `showcase.yaml` (chưa migrate).
"""
from pathlib import Path

import yaml

from docslib.index import load_model
from docslib import graph


def _write(p: Path, payload) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def _module_readme(docs: Path, rel_dir: str, mid: str) -> None:
    _write(docs / rel_dir / "README.md",
           f"---\nid: {mid}\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n# {mid}\n")


_BANDS = [
    {"id": "ingest", "vi": "Thu", "en": "Ingest", "cluster_vi": "Cụm thu", "cluster_en": "Ingest cluster"},
    {"id": "extract", "vi": "Bóc", "en": "Extract", "cluster_vi": "Cụm bóc", "cluster_en": "Extract cluster"},
]


def _build_split_tree(docs: Path) -> None:
    _module_readme(docs, "modules/core/mod-01-intake", "mod-01")
    _module_readme(docs, "modules/core/mod-02-extract", "mod-02")
    _write(docs / "_index" / "bands.yaml", {
        "bands": _BANDS,
        "modules": [{"id": "mod-01", "band": "ingest"}, {"id": "mod-02", "band": "extract"}],
    })
    _write(docs / "_present" / "present.yaml", {
        "modules": [{"id": "mod-01", "order": 1}, {"id": "mod-02", "order": 2}],
        "text_fix": {"foo": "bar"},
    })


def _build_merged_tree(docs: Path) -> None:
    _module_readme(docs, "modules/core/mod-01-intake", "mod-01")
    _module_readme(docs, "modules/core/mod-02-extract", "mod-02")
    _write(docs / "_index" / "showcase.yaml", {
        "bands": _BANDS,
        "modules": [
            {"id": "mod-01", "order": 1, "band": "ingest"},
            {"id": "mod-02", "order": 2, "band": "extract"},
        ],
        "text_fix": {"foo": "bar"},
    })


def test_band_read_from_bands_yaml(tmp_path):
    _build_split_tree(tmp_path)
    m = load_model(tmp_path)
    assert m.module("mod-01").band == "ingest"
    assert m.module("mod-02").band == "extract"


def test_order_read_from_present(tmp_path):
    _build_split_tree(tmp_path)
    m = load_model(tmp_path)
    assert m.module("mod-01").order == 1
    assert m.module("mod-02").order == 2


def test_bands_order_and_clusters_from_bands_yaml(tmp_path):
    _build_split_tree(tmp_path)
    m = load_model(tmp_path)
    bo = graph._bands_order(m)
    assert ("ingest", "Thu", "Ingest") in bo
    cn = graph._cluster_names(m)
    assert cn["ingest"] == ("Cụm thu", "Ingest cluster")


def test_text_fix_from_present(tmp_path):
    _build_split_tree(tmp_path)
    m = load_model(tmp_path)
    assert graph._text_fix(m) == {"foo": "bar"}


def test_split_parity_with_merged(tmp_path):
    split = tmp_path / "split"
    merged = tmp_path / "merged"
    _build_split_tree(split)
    _build_merged_tree(merged)
    ms = load_model(split)
    mm = load_model(merged)
    assert {x.id: (x.band, x.order) for x in ms.modules} == {x.id: (x.band, x.order) for x in mm.modules}
    assert graph._bands_order(ms) == graph._bands_order(mm)
    assert graph._cluster_names(ms) == graph._cluster_names(mm)
    assert graph._text_fix(ms) == graph._text_fix(mm)


def test_legacy_showcase_shim(tmp_path):
    """Chỉ có legacy showcase.yaml → split-read back-compat + cờ deprecation."""
    _build_merged_tree(tmp_path)
    m = load_model(tmp_path)
    assert m.module("mod-01").band == "ingest"
    assert m.module("mod-01").order == 1
    assert graph._text_fix(m) == {"foo": "bar"}
    assert m.legacy_showcase is True


def test_new_split_not_flagged_legacy(tmp_path):
    _build_split_tree(tmp_path)
    m = load_model(tmp_path)
    assert m.legacy_showcase is False


# ---- R2 hardening ---------------------------------------------------------

def test_malformed_index_yaml_does_not_crash(tmp_path):
    """_index/_present yaml hỏng → load_model KHÔNG crash (gate báo gap, không traceback)."""
    _module_readme(tmp_path, "modules/core/mod-01-intake", "mod-01")
    (tmp_path / "_index").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_index" / "bands.yaml").write_text(": : broken [\n", encoding="utf-8")
    (tmp_path / "_present").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_present" / "x.yaml").write_text(":\n  - broken: [\n", encoding="utf-8")
    m = load_model(tmp_path)             # không raise
    assert m.module("mod-01") is not None


def test_present_multi_file_merge(tmp_path):
    """nhiều file _present/*.yaml → scalar last-by-sort thắng; modules[] nối."""
    _module_readme(tmp_path, "modules/core/mod-01-intake", "mod-01")
    _module_readme(tmp_path, "modules/core/mod-02-extract", "mod-02")
    _write(tmp_path / "_index" / "bands.yaml", {"bands": _BANDS,
           "modules": [{"id": "mod-01", "band": "ingest"}, {"id": "mod-02", "band": "extract"}]})
    _write(tmp_path / "_present" / "a.yaml", {"modules": [{"id": "mod-01", "order": 1}], "text_fix": {"k": "from-a"}})
    _write(tmp_path / "_present" / "b.yaml", {"modules": [{"id": "mod-02", "order": 2}], "text_fix": {"k": "from-b"}})
    (tmp_path / "_present" / "junk.yaml").write_text("- not-a-dict\n", encoding="utf-8")  # bỏ qua
    m = load_model(tmp_path)
    assert m.module("mod-01").order == 1 and m.module("mod-02").order == 2   # modules[] nối
    assert m.present["text_fix"]["k"] == "from-b"                             # b > a (sort)


def test_shim_order_defaults_to_zero(tmp_path):
    """legacy module thiếu order → shim mặc định 0."""
    _module_readme(tmp_path, "modules/core/mod-09-x", "mod-09")
    _write(tmp_path / "_index" / "showcase.yaml",
           {"modules": [{"id": "mod-09", "band": "ingest"}], "bands": _BANDS})  # không order
    m = load_model(tmp_path)
    assert m.module("mod-09").order == 0


def test_split_parity_carries_presentation_keys(tmp_path):
    """shim + split đều mang nguyên presentation key (sections/pages) — parity lossless."""
    split = tmp_path / "split"
    merged = tmp_path / "merged"
    _build_split_tree(split)
    _write(split / "_present" / "extra.yaml", {"sections": [{"id": "ov", "order": 1}], "pages": [{"key": "hub"}]})
    _build_merged_tree(merged)
    _write(merged / "_index" / "showcase.yaml", {
        "bands": _BANDS,
        "modules": [{"id": "mod-01", "order": 1, "band": "ingest"}, {"id": "mod-02", "order": 2, "band": "extract"}],
        "text_fix": {"foo": "bar"}, "sections": [{"id": "ov", "order": 1}], "pages": [{"key": "hub"}],
    })
    ms = load_model(split)
    mm = load_model(merged)
    assert ms.present.get("sections") == [{"id": "ov", "order": 1}]      # split: _present mang sections
    assert mm.present.get("sections") == [{"id": "ov", "order": 1}]      # shim: showcase→present mang sections
    assert ms.present.get("pages") == mm.present.get("pages") == [{"key": "hub"}]
