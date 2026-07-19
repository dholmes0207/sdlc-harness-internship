"""Tests for the standards template generator.

generate_standards_templates instantiates a template skeleton, substitutes
{{tokens}} (unfilled → TBD), allocates the next parent-scoped id using the SAME
ID_PATTERN_BY_TYPE the builder owns (imported, never re-encoded), and writes the
output through the standards fs_guard zone. Charter goal skeletons carry a
required metrics: field. The generated id validates against the builder's grammar.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402

import generate_standards_templates as gen  # noqa: E402
import standards_graph  # noqa: E402


def _empty_root(tmp_path) -> Path:
    (tmp_path / "harness" / "standards" / "areas").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_allocates_next_parent_scoped_id(tmp_path):
    # with an existing graph that has ARCH-G1, allocate the next arch_goal id
    graph = {"nodes": [{"id": "ARCH-G1", "type": "arch_goal"}], "edges": []}
    assert gen.allocate_id(graph, "arch_goal", slug=None, parent=None) == "ARCH-G2"
    # a rule under STD-AUTH-RG1 allocates STD-AUTH-RG1-R1 when none exist
    g2 = {"nodes": [{"id": "STD-AUTH-RG1", "type": "rule_group"}], "edges": []}
    assert gen.allocate_id(g2, "rule", slug=None, parent="STD-AUTH-RG1") == "STD-AUTH-RG1-R1"
    # a rule_group under STD-AUTH
    g3 = {"nodes": [{"id": "STD-AUTH", "type": "std_area"}], "edges": []}
    assert gen.allocate_id(g3, "rule_group", slug=None, parent="STD-AUTH") == "STD-AUTH-RG1"
    # an std_area from a slug
    assert gen.allocate_id({"nodes": [], "edges": []}, "std_area",
                           slug="auth", parent=None) == "STD-AUTH"


def test_token_substitution(tmp_path):
    text = gen.render("id: {{id}}\ntitle: {{title}}\n", {"id": "STD-X"})
    assert "STD-X" in text
    assert "TBD" in text  # unfilled title → TBD


def test_charter_goal_requires_metric_field(tmp_path):
    skeleton = gen.load_template("charter")
    assert "metrics:" in skeleton, "charter skeleton must carry a metrics: field"


def test_generated_artifact_passes_id_grammar(tmp_path):
    for ttype, idv in (("std_area", "STD-AUTH"), ("rule_group", "STD-AUTH-RG1"),
                       ("rule", "STD-AUTH-RG1-R1"), ("arch_goal", "ARCH-G1")):
        pattern = standards_graph.ID_PATTERN_BY_TYPE[ttype]
        assert pattern.match(idv), f"{idv} must match the {ttype} grammar"


def test_writes_through_fs_guard_zone(tmp_path, monkeypatch):
    from fs_guard import FenceError
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _empty_root(tmp_path)
    # a write target outside harness/standards/ must raise FenceError
    with pytest.raises(FenceError):
        gen.write_artifact(tmp_path / "docs" / "evil.md", "x", root=tmp_path)
    # a write inside the zone succeeds
    target = tmp_path / "harness" / "standards" / "areas" / "STD-AUTH.md"
    gen.write_artifact(target, "id: STD-AUTH\n", root=tmp_path)
    assert target.read_text(encoding="utf-8").startswith("id: STD-AUTH")


def test_main_bad_values_json_is_clean_error_not_traceback(tmp_path, capsys):
    # a malformed --values must exit 2 with a stderr message, never a raw traceback
    rc = gen.main(["--type", "std_area", "--slug", "x", "--root", str(tmp_path),
                   "--values", "{not valid json"])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "values" in err or "json" in err


def test_main_bad_slug_is_clean_error_not_traceback(tmp_path, capsys):
    # finding 1: input errors BEYOND --values (an invalid slug the allocator
    # rejects) must also exit 2 with a message, not crash with a raw traceback /
    # exit 1 — the docstring promises this and the harness block convention is
    # exit 2, never 1.
    rc = gen.main(["--type", "std_area", "--slug", "bad slug spaces",
                   "--root", str(tmp_path)])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_user_supplied_id_must_pass_grammar(tmp_path, capsys):
    # finding 2: an id passed via --values skips allocate_id, so the generator
    # must still validate it against the type grammar — else it scaffolds a tree
    # that fails its own gate, breaking the stated generate-time guarantee.
    rc = gen.main(["--type", "std_area", "--root", str(tmp_path),
                   "--values", '{"id": "STD-bad lower spaces"}'])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "id" in err or "grammar" in err


def test_main_write_refuses_to_overwrite_existing(tmp_path, capsys):
    # finding 3: re-running std_area --write for the same slug must not silently
    # clobber a hand-authored area file (data loss); refuse unless --force.
    root = _empty_root(tmp_path)
    argv = ["--type", "std_area", "--slug", "auth", "--root", str(root), "--write"]
    assert gen.main(argv) == 0
    capsys.readouterr()
    rc = gen.main(argv)                 # second run, same target
    assert rc == 2
    assert "exist" in capsys.readouterr().err.lower()


def test_main_force_allows_overwrite(tmp_path):
    # --force is the explicit escape hatch for finding 3's refusal.
    root = _empty_root(tmp_path)
    argv = ["--type", "std_area", "--slug", "auth", "--root", str(root), "--write"]
    assert gen.main(argv) == 0
    assert gen.main(argv + ["--force"]) == 0


# ── pure-YAML SSOT emission (new default) ───────────────────────────────────

def test_default_format_is_yaml_ssot(tmp_path, capsys):
    # the generator now defaults to the pure-YAML SSOT form: a std_area writes a
    # `.std.yaml` file (no `.md`, no `---` fence) that the loader builds cleanly.
    root = _empty_root(tmp_path)
    rc = gen.main(["--type", "std_area", "--slug", "auth", "--root", str(root),
                   "--write"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith(".std.yaml"), out
    written = root / out
    assert written.exists()
    assert not written.read_text(encoding="utf-8").lstrip().startswith("---")
    # and it builds through the SSOT loader with no parse error
    graph = standards_graph.build_graph(root)
    assert graph["parse_errors"] == []
    assert any(n["id"] == "STD-AUTH" for n in graph["nodes"])


def test_format_md_still_available(tmp_path, capsys):
    # legacy `.md`-frontmatter emission stays reachable via --format md.
    root = _empty_root(tmp_path)
    rc = gen.main(["--type", "std_area", "--slug", "auth", "--root", str(root),
                   "--format", "md", "--write"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith(".md"), out
    assert (root / out).read_text(encoding="utf-8").lstrip().startswith("---")
