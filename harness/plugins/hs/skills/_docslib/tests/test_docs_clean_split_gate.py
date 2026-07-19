"""C6 — gate ép convention content⟂UI: 4 invariant, mỗi vi phạm planted phải fail.

Chỉ ép khi tree ĐÃ adopt convention mới (`_index/bands.yaml` | `_present/` | `playbook.yaml`);
tree legacy (chỉ `showcase.yaml`) KHÔNG bị ép (back-compat tới khi migrate).
"""
from pathlib import Path

import yaml

from docslib.findings import Findings
from docslib.index import load_model
from docslib import graph


def _w(p: Path, payload) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload if isinstance(payload, str) else yaml.safe_dump(payload, allow_unicode=True),
                 encoding="utf-8")


def _adopted_tree(docs: Path) -> None:
    """Cây tối thiểu đã adopt convention (có bands.yaml + _present) — clean."""
    _w(docs / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n# mod-01\n")
    _w(docs / "_index" / "bands.yaml", {"bands": [{"id": "ingest", "vi": "T", "en": "I"}],
                                        "modules": [{"id": "mod-01", "band": "ingest"}]})
    _w(docs / "_present" / "present.yaml", {"modules": [{"id": "mod-01", "order": 1}]})


def _errors(docs: Path, code_prefix: str):
    model = load_model(docs)
    f = Findings()
    graph.validate_clean_split(model, f)
    return [i for i in f.by_severity("error") if i.code.startswith(code_prefix)]


def test_clean_adopted_tree_passes(tmp_path):
    _adopted_tree(tmp_path)
    model = load_model(tmp_path)
    f = Findings()
    graph.validate_clean_split(model, f)
    assert not f.has_errors(), [i.msg for i in f.by_severity("error")]


def test_legacy_tree_not_enforced(tmp_path):
    # chỉ showcase.yaml + derived committed → KHÔNG ép (back-compat)
    _w(tmp_path / "_index" / "showcase.yaml", {"modules": [{"id": "mod-01", "order": 1, "band": "ingest"}],
                                               "sections": [{"id": "overview", "order": 1}]})
    _w(tmp_path / "showcase" / "assets" / "js" / "module-m4-data.js", "var X=1;")
    model = load_model(tmp_path)
    f = Findings()
    graph.validate_clean_split(model, f)
    assert not f.has_errors()


def test_derived_committed_flagged(tmp_path):
    _adopted_tree(tmp_path)
    _w(tmp_path / "showcase" / "assets" / "js" / "module-m4-data.js", "var X=1;")
    _w(tmp_path / "public" / "index.html", "<html></html>")
    errs = _errors(tmp_path, "derived-committed")
    assert errs
    wheres = {e.where for e in errs}
    assert any("module-m4-data.js" in w for w in wheres)
    assert any("public/index.html" in w for w in wheres)


def test_presentation_key_in_index_flagged(tmp_path):
    _adopted_tree(tmp_path)
    # nhét sections (presentation) vào _index → vi phạm
    _w(tmp_path / "_index" / "modules.yaml", {"sections": [{"id": "ov", "order": 1}], "parts": {}})
    assert _errors(tmp_path, "presentation-in-index")


def test_band_in_present_flagged(tmp_path):
    _adopted_tree(tmp_path)
    # nhét bands (design taxonomy) vào _present → vi phạm
    _w(tmp_path / "_present" / "present.yaml",
       {"modules": [{"id": "mod-01", "order": 1}], "bands": [{"id": "ingest"}]})
    assert _errors(tmp_path, "band-in-present")


def test_split_adopted_via_playbook_only(tmp_path):
    """tree adopt CHỈ qua playbook.yaml (không bands/_present) → vẫn ép gate."""
    _w(tmp_path / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n# mod-01\n")
    _w(tmp_path / "playbook.yaml", {"content": ["modules/"], "output": "build/"})
    _w(tmp_path / "public" / "index.html", "<html></html>")
    assert _errors(tmp_path, "derived-committed")


def test_multiple_violations_all_flagged(tmp_path):
    """nhiều vi phạm cùng lúc → gate KHÔNG short-circuit, báo đủ 4 loại."""
    _adopted_tree(tmp_path)
    _w(tmp_path / "public" / "index.html", "<html></html>")                    # derived-committed
    _w(tmp_path / "_index" / "modules.yaml", {"sections": [{"id": "ov"}], "parts": {}})  # presentation-in-index
    _w(tmp_path / "_present" / "present.yaml",
       {"modules": [{"id": "mod-01", "order": 1}], "bands": [{"id": "x"}]})    # band-in-present
    _w(tmp_path / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n"
       "# mod-01\n\n| Part | Nhà | Reuse |\n|---|---|---|\n| `x` | mod-04 | y |\n")               # graph-redeclared
    model = load_model(tmp_path)
    f = Findings()
    graph.validate_clean_split(model, f)
    codes = {i.code for i in f.by_severity("error")}
    assert {"derived-committed", "presentation-in-index", "band-in-present",
            "graph-redeclared-in-readme"} <= codes


def test_readme_vn_reuse_table_flagged(tmp_path):
    """F3: bảng reuse tiếng Việt (Thành phần / Tái dùng) cũng bị bắt (không chỉ EN)."""
    _adopted_tree(tmp_path)
    _w(tmp_path / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n"
       "# mod-01\n\n## Tái dùng\n\n| Thành phần | Tái dùng bởi |\n|---|---|\n| `x` | mod-04 |\n")
    assert _errors(tmp_path, "graph-redeclared-in-readme")


def test_readme_fenced_example_not_flagged(tmp_path):
    """F3: bảng reuse trong code-fence (ví dụ minh hoạ) KHÔNG bị bắt (false-pos)."""
    _adopted_tree(tmp_path)
    _w(tmp_path / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n"
       "# mod-01\n\nVí dụ format bảng:\n\n```\n| Part | Uses | Nhà |\n|---|---|---|\n| a | b | c |\n```\n")
    model = load_model(tmp_path)
    f = Findings()
    graph.validate_clean_split(model, f)
    assert not [i for i in f.by_severity("error") if i.code == "graph-redeclared-in-readme"]


def test_graph_redeclared_in_readme_flagged(tmp_path):
    _adopted_tree(tmp_path)
    # README re-declare graph bằng bảng reuse → vi phạm
    _w(tmp_path / "modules/core/mod-01-x/README.md",
       "---\nid: mod-01\ntype: module-readme\nstatus: stable\nowner: PTNT\nversion: 1.0.0\n---\n"
       "# mod-01\n\n## Reuses\n\n| Part | Nhà | Vì sao |\n|---|---|---|\n| `audit-spine` | mod-04 | x |\n")
    assert _errors(tmp_path, "graph-redeclared-in-readme")
