"""test_decision_register.py — the Decision Register (DEC-<n>), ported PS
semantics + harness changes.

Append-only register at docs/decisions.md, written through the fs_guard
"docs" zone. Script owns the deterministic structure: monotonic id alloc
(max+1, never reused, corrupt-but-id-bearing blocks still count), grammar
validation, append-without-overwrite, list. Injection escape covers the
multiline rationale AND the single-line title/affects fields. BOTH append
paths (--append and --append-alloc) run inside the register lock so two
concurrent processes cannot overwrite each other's records.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import decision_register as dr  # noqa: E402
from decision_register import (  # noqa: E402
    DECISION_ID_RE, DecisionError, alloc_id, append_decision,
    list_active, parse_decisions,
)
import fs_guard  # noqa: E402 — exception class looked up live: other test
# files reload fs_guard (new class identity), so an import-time binding here
# would make pytest.raises order-dependent.


def _decisions_path(root: Path) -> Path:
    return root / "docs" / "decisions.md"


def _seed(root: Path, *records: str) -> Path:
    p = _decisions_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = "# Decision Register\n\n"
    p.write_text(header + "\n".join(records) + ("\n" if records else ""),
                 encoding="utf-8")
    return p


def _record(dec_id: str, status: str = "active", supersedes: str = "") -> str:
    sup = "supersedes: %s\n" % supersedes if supersedes else ""
    return (
        "---\n"
        "id: %s\n"
        "status: %s\n"
        "date: 2026-06-01\n"
        "%s"
        "---\n"
        "## %s — sample ruling\n\n"
        "Rationale prose here.\n" % (dec_id, status, sup, dec_id)
    )


# ---------- alloc_id ----------

class TestAllocId:
    def test_first(self, tmp_path):
        assert alloc_id(tmp_path) == "DEC-1"
        _seed(tmp_path)
        assert alloc_id(tmp_path) == "DEC-1"

    def test_increments(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"), _record("DEC-2"))
        assert alloc_id(tmp_path) == "DEC-3"

    def test_superseded_still_counts(self, tmp_path):
        _seed(tmp_path, _record("DEC-1", status="active"),
              _record("DEC-2", status="superseded", supersedes="DEC-1"))
        assert alloc_id(tmp_path) == "DEC-3"

    def test_gap_uses_max_plus_one(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"), _record("DEC-5"))
        assert alloc_id(tmp_path) == "DEC-6"

    def test_corrupt_but_id_bearing_block_reserves_its_number(self, tmp_path):
        corrupt = (
            "---\n"
            "id: DEC-5\n"
            "status: active\n"
            "affects: [unterminated\n"
            "---\n"
            "## DEC-5 — corrupt block\n\nRationale.\n"
        )
        _seed(tmp_path, _record("DEC-1"), corrupt)
        assert sorted(r["id"] for r in parse_decisions(tmp_path)) == ["DEC-1"]
        assert alloc_id(tmp_path) == "DEC-6"

    def test_bad_timestamp_md_frontmatter_skipped_not_crash(self, tmp_path):
        # PyYAML raises a bare ValueError (not YAMLError) on an out-of-range
        # timestamp; the md-mode parser must skip the record, not crash.
        bad = (
            "---\n"
            "id: DEC-2\n"
            "status: active\n"
            "date: 2026-13-45\n"   # month 13 / day 45 → PyYAML ValueError
            "---\n"
            "## DEC-2 — bad date\n\nRationale.\n"
        )
        _seed(tmp_path, _record("DEC-1"), bad)
        # does not raise; DEC-1 still parses, the bad block is skipped
        assert [r["id"] for r in parse_decisions(tmp_path)] == ["DEC-1"]


# ---------- append_decision ----------

class TestAppend:
    def test_validates_grammar(self, tmp_path):
        _seed(tmp_path)
        with pytest.raises(DecisionError):
            append_decision(tmp_path, dec_id="DEC-abc", title="bad", rationale="x")
        with pytest.raises(DecisionError):
            append_decision(tmp_path, dec_id="DECISION-1", title="bad", rationale="x")

    def test_append_only_prior_records_survive(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"))
        before = _decisions_path(tmp_path).read_text(encoding="utf-8")
        out = append_decision(
            tmp_path, dec_id="DEC-2", title="actor format",
            rationale="user:<u>/agent:<a> — attribution, not authentication",
        )
        after = out.read_text(encoding="utf-8")
        assert before.rstrip() in after
        recs = parse_decisions(tmp_path)
        assert [r["id"] for r in recs] == ["DEC-1", "DEC-2"]
        assert recs[1]["status"] == "active"

    def test_rejects_duplicate_id(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"))
        with pytest.raises(DecisionError):
            append_decision(tmp_path, dec_id="DEC-1", title="dup", rationale="x")

    def test_supersede_records_link_and_flips_old(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"))
        append_decision(tmp_path, dec_id="DEC-2", title="switch", rationale="y",
                        supersedes="DEC-1")
        dr._supersede_in_place(tmp_path, "DEC-1")
        by_id = {r["id"]: r for r in parse_decisions(tmp_path)}
        assert by_id["DEC-2"]["supersedes"] == "DEC-1"
        assert by_id["DEC-1"]["status"] == "superseded"

    def test_supersede_preserves_blank_line_before_heading(self, tmp_path):
        append_decision(tmp_path, dec_id="DEC-1", title="first", rationale="z")
        text_before = _decisions_path(tmp_path).read_text(encoding="utf-8")
        assert "---\n\n## DEC-1" in text_before
        assert dr._supersede_in_place(tmp_path, "DEC-1")
        text = _decisions_path(tmp_path).read_text(encoding="utf-8")
        assert "status: superseded" in text
        assert "---\n\n## DEC-1" in text
        assert "---\n## DEC-1" not in text

    def test_supersede_without_status_line_reports_no_flip(self, tmp_path):
        # A hand-edited record missing its `status:` line cannot be flipped;
        # the function must say so (False), not report success while the old
        # ruling silently stays active.
        _seed(tmp_path, (
            "---\n"
            "id: DEC-1\n"
            "date: 2026-06-01\n"
            "---\n"
            "## DEC-1 — sample ruling\n\n"
            "Rationale prose here.\n"
        ))
        assert dr._supersede_in_place(tmp_path, "DEC-1") is False

    def test_invalid_append_with_supersedes_leaves_register_untouched(self, tmp_path, monkeypatch):
        seeded = _seed(tmp_path, _record("DEC-1"))
        before = seeded.read_text(encoding="utf-8")
        argv = ["decision_register.py", "--root", str(tmp_path), "--append",
                "--id", "DEC-2", "--title", "second", "--supersedes", "DEC-1"]
        monkeypatch.setattr(sys, "argv", argv)
        rc = dr.main()
        assert rc == 0  # bad input → JSON finding, not crash
        assert seeded.read_text(encoding="utf-8") == before
        assert sorted(r["id"] for r in list_active(tmp_path)) == ["DEC-1"]

    def test_append_cli_refuses_unflippable_supersede_untouched(self, tmp_path, monkeypatch):
        # The --append CLI path delegates to the SAME locked critical section as
        # --append-alloc, so its supersede-feasibility gate must also refuse a
        # target with no status: line BEFORE writing — never appending a second
        # active ruling. (Companion to test_append_alloc_surfaces_failed_supersede,
        # which exercises the library entry; this pins the CLI/explicit-id path.)
        seeded = _seed(tmp_path, (
            "---\n"
            "id: DEC-1\n"
            "date: 2026-06-01\n"
            "---\n"
            "## DEC-1 — sample ruling\n\nRationale prose here.\n"
        ))
        before = seeded.read_text(encoding="utf-8")
        argv = ["decision_register.py", "--root", str(tmp_path), "--append",
                "--id", "DEC-2", "--title", "switch", "--rationale", "y",
                "--supersedes", "DEC-1"]
        monkeypatch.setattr(sys, "argv", argv)
        rc = dr.main()
        assert rc == 0  # refusal surfaces as a JSON finding, not a crash
        assert seeded.read_text(encoding="utf-8") == before  # byte-untouched
        assert [r["id"] for r in list_active(tmp_path)] == ["DEC-1"]  # no DEC-2

    def test_record_carries_actor_and_ts(self, tmp_path, monkeypatch):
        # Every register record is machine-written state: it must carry an
        # actor (via resolve_actor) and a timestamp, like the other stores.
        monkeypatch.setenv("HARNESS_USER", "decider@local")
        append_decision(tmp_path, dec_id="DEC-1", title="t", rationale="r")
        rec = parse_decisions(tmp_path)[0]
        assert rec["actor"].startswith("user:decider@local")
        assert rec["ts"]  # ts present and non-empty

    def test_append_alloc_surfaces_failed_supersede(self, tmp_path, monkeypatch):
        # If the in-place supersede fails (e.g. the target has no status:
        # line), append_alloc must NOT leave two active rulings silently —
        # it surfaces an error rather than reporting a clean write.
        _seed(tmp_path, (
            "---\n"
            "id: DEC-1\n"
            "date: 2026-06-01\n"
            "---\n"
            "## DEC-1 — sample ruling\n\nRationale prose here.\n"
        ))
        with pytest.raises(DecisionError):
            dr.append_alloc(tmp_path, title="switch", rationale="y",
                            supersedes="DEC-1")
        # No second active ruling left behind by the failed supersede.
        assert sorted(r["id"] for r in list_active(tmp_path)) == ["DEC-1"]

    def test_dangling_supersedes_rejected(self, tmp_path):
        _seed(tmp_path)
        with pytest.raises(DecisionError):
            append_decision(tmp_path, dec_id="DEC-1", title="t", rationale="r",
                            supersedes="DEC-9")

    def test_write_lands_under_docs(self, tmp_path):
        out = append_decision(tmp_path, dec_id="DEC-1", title="first", rationale="z")
        assert out.is_relative_to(tmp_path / "docs")

    def test_fence_blocks_escape(self, tmp_path, monkeypatch):
        escape = tmp_path / "outside" / "decisions.md"
        monkeypatch.setattr(dr, "_decisions_path", lambda root: escape)
        with pytest.raises(fs_guard.FenceError):
            append_decision(tmp_path, dec_id="DEC-1", title="first", rationale="z")
        assert not escape.exists()


# ---------- injection (rationale + title + affects) ----------

class TestInjection:
    def test_rationale_fence_and_heading_escaped(self, tmp_path):
        evil = "para\n---\nid: DEC-99\n---\n## DEC-99 — fake\nmore"
        append_decision(tmp_path, dec_id="DEC-1", title="t", rationale=evil)
        recs = parse_decisions(tmp_path)
        assert [r["id"] for r in recs] == ["DEC-1"]  # no phantom DEC-99
        text = _decisions_path(tmp_path).read_text(encoding="utf-8")
        assert "\\---" in text and "\\## DEC-99" in text

    @pytest.mark.parametrize("sep,label", [
        ("\u2028", "line-separator"),
        ("\u0085", "NEL"),
        ("\x0b", "vertical-tab"),
        ("\x0c", "form-feed"),
    ])
    def test_unicode_separators_cannot_smuggle_records(self, tmp_path, sep, label):
        # These separators survive sanitize_field (it collapses only \r\n)
        # AND never create a line anchor for re MULTILINE (only \n does) —
        # that symmetry is what keeps them harmless. Pin it: a refactor of
        # either side (e.g. switching to a regex that treats them as
        # newlines) must not silently open a smuggling channel.
        evil_affects = "PRD-X%sstatus: superseded%sid: DEC-99" % (sep, sep)
        evil_rationale = "why%s---%s## DEC-88 — fake" % (sep, sep)
        append_decision(tmp_path, dec_id="DEC-1", title="t",
                        rationale=evil_rationale, affects=evil_affects)
        recs = parse_decisions(tmp_path)
        assert [r["id"] for r in recs] == ["DEC-1"]
        assert recs[0]["status"] == "active"  # no smuggled status override

    def test_title_newline_cannot_smuggle_record(self, tmp_path):
        evil_title = "ok\n---\nid: DEC-77\n---\n## DEC-77 — fake"
        append_decision(tmp_path, dec_id="DEC-1", title=evil_title, rationale="r")
        recs = parse_decisions(tmp_path)
        assert [r["id"] for r in recs] == ["DEC-1"]
        # heading stays one line: the whole title is inert on the DEC-1 line
        text = _decisions_path(tmp_path).read_text(encoding="utf-8")
        heading = [l for l in text.splitlines() if l.startswith("## DEC-1")]
        assert len(heading) == 1 and "DEC-77" in heading[0]

    def test_affects_newline_cannot_break_frontmatter(self, tmp_path):
        evil_affects = "PRD-X\nstatus: superseded"
        append_decision(tmp_path, dec_id="DEC-1", title="t", rationale="r",
                        affects=evil_affects)
        recs = parse_decisions(tmp_path)
        assert recs[0]["status"] == "active"  # injected status line did not win
        assert "PRD-X" in recs[0]["affects"]


# ---------- list_active ----------

class TestListActive:
    def test_only_active(self, tmp_path):
        _seed(tmp_path,
              _record("DEC-1", status="active"),
              _record("DEC-2", status="superseded", supersedes="DEC-1"),
              _record("DEC-3", status="active"))
        assert sorted(r["id"] for r in list_active(tmp_path)) == ["DEC-1", "DEC-3"]

    def test_empty_register(self, tmp_path):
        assert list_active(tmp_path) == []
        _seed(tmp_path)
        assert list_active(tmp_path) == []


class _FmSink:
    """Minimal findings collector: frontmatter.validate only calls .error."""
    def __init__(self):
        self.codes = []

    def error(self, code, rel, msg):
        self.codes.append(code)

    def warn(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


def test_render_md_leads_with_valid_doc_frontmatter(tmp_path):
    """decisions.md is a graph doc; its generated view must lead with a document
    frontmatter that the docs-standardize contract accepts — otherwise the
    release preflight docs gate blocks the cut. The file is generated, so the
    frontmatter has to come from render_md, not a hand-edit that regen wipes."""
    md = dr.render_md([
        {"id": "DEC-1", "title": "t", "rationale": "r", "date": "2026-01-01",
         "status": "active"},
    ])
    assert md.startswith("---\n")
    assert md.index("\n---", 4) < md.index("# Decision Register")

    dl = Path(__file__).resolve().parents[1] / "plugins/hs/skills/_docslib"
    sys.path.insert(0, str(dl))
    from docslib import frontmatter as fm  # noqa: E402

    doc_path = tmp_path / "decisions.md"
    doc_path.write_text(md, encoding="utf-8")
    sink = _FmSink()
    fm.validate(fm.parse(doc_path, "docs/decisions.md"), sink)
    assert sink.codes == [], f"frontmatter contract errors: {sink.codes}"


def test_decision_id_regex():
    assert DECISION_ID_RE.match("DEC-1")
    assert DECISION_ID_RE.match("DEC-42")
    for bad in ("DEC-", "DEC-1a", "dec-1", "DECISION-1"):
        assert not DECISION_ID_RE.match(bad)


# ---------- concurrency (both append paths hold the register lock) ----------

def _cli(root, *args):
    return [sys.executable, str(_SCRIPTS / "decision_register.py"),
            "--root", str(root)] + list(args)


class TestConcurrency:
    def test_parallel_append_alloc_yields_distinct_monotonic_ids(self, tmp_path):
        procs = [subprocess.Popen(
            _cli(tmp_path, "--append-alloc", "--title", "t%d" % i,
                 "--rationale", "r%d" % i),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for i in range(6)]
        outs = [p.communicate() for p in procs]
        assert all(p.returncode == 0 for p in procs), outs
        ids = [r["id"] for r in parse_decisions(tmp_path)]
        assert sorted(ids) == ["DEC-%d" % n for n in range(1, 7)]
        assert len(set(ids)) == 6  # no collision, no lost record

    def test_explicit_append_waits_for_register_lock(self, tmp_path):
        # Hold the register lock from the test process; a concurrent --append
        # must WAIT (proving the explicit-id path is inside the critical
        # section too), then complete once released.
        fcntl = pytest.importorskip("fcntl")
        lock_path = dr._lock_path(tmp_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        proc = subprocess.Popen(
            _cli(tmp_path, "--append", "--id", "DEC-1", "--title", "t",
                 "--rationale", "r"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.5)
        assert proc.poll() is None, "append finished while lock was held"
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, err
        assert json.loads(out)["written"] is True
        assert [r["id"] for r in parse_decisions(tmp_path)] == ["DEC-1"]


def test_legacy_append_result_path_is_markdown_source(tmp_path):
    # no decisions.yaml → markdown IS the source; the result names it via `path`
    # and carries no ssot/rendered split (that shape is YAML-mode only).
    res = dr.append_alloc(tmp_path, title="t", rationale="r")
    assert res["path"] == "docs/decisions.md"
    assert "ssot" not in res and "rendered" not in res


def test_rationale_bare_optional_line_preserved(tmp_path):
    # The empty-optional strip must touch only the generated frontmatter, never the
    # caller's rationale body: a rationale line that is literally `affects:` /
    # `supersedes:` (a decision discussing those fields) must survive verbatim.
    append_decision(tmp_path, dec_id="DEC-1", title="meta",
                    rationale="intro line\naffects:\nsupersedes:\nconclusion",
                    affects="", supersedes="")
    body = (tmp_path / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "\naffects:\n" in body
    assert "\nsupersedes:\n" in body



def test_append_decision_rejects_unflippable_supersede(tmp_path):
    # superseding a target that has no flippable `status:` line would strand two
    # active rulings — append_decision must refuse (symmetry with append_alloc).
    status_less = "---\nid: DEC-1\ndate: 2026-06-01\ntitle: x\nrationale: y\n---\n"
    _seed(tmp_path, status_less)
    with pytest.raises(DecisionError):
        append_decision(tmp_path, "DEC-2", "new", "why", supersedes="DEC-1")


# ---------- YAML SSOT mode (decisions.yaml present) ----------

import yaml as _yaml  # noqa: E402


def _yaml_path(root: Path) -> Path:
    return root / "docs" / "decisions.yaml"


def _seed_yaml(root: Path, *records: dict) -> Path:
    p = _yaml_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_yaml.safe_dump(list(records), sort_keys=False), encoding="utf-8")
    return p


def _yrec(dec_id, status="active", supersedes="", rationale="why", title="t"):
    return {"id": dec_id, "status": status, "date": "2026-06-01", "actor": "",
            "ts": "", "affects": "", "supersedes": supersedes,
            "title": title, "rationale": rationale}


class TestYamlMode:
    def test_alloc_id_from_yaml(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1"), _yrec("DEC-2"))
        assert alloc_id(tmp_path) == "DEC-3"

    def test_append_alloc_yaml_crud(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1"))
        res = dr.append_alloc(tmp_path, title="new", rationale="because")
        assert res["id"] == "DEC-2"
        ids = [r["id"] for r in parse_decisions(tmp_path)]
        assert ids == ["DEC-1", "DEC-2"]
        # still YAML mode, decisions.yaml is the SSOT
        assert _yaml_path(tmp_path).is_file()

    def test_append_alloc_result_names_ssot_and_rendered(self, tmp_path):
        # in YAML mode the result must name the real SSOT explicitly and point
        # `path` at it — not at the rendered markdown view (the old misleading shape)
        _seed_yaml(tmp_path, _yrec("DEC-1"))
        res = dr.append_alloc(tmp_path, title="new", rationale="because")
        assert res["ssot"] == "docs/decisions.yaml"
        assert res["rendered"] == "docs/decisions.md"
        assert res["path"] == "docs/decisions.yaml"   # SSOT, not the render

    def test_supersede_field_flip_never_two_active(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1"))
        dr.append_alloc(tmp_path, title="switch", rationale="y", supersedes="DEC-1")
        by_id = {r["id"]: r for r in parse_decisions(tmp_path)}
        assert by_id["DEC-1"]["status"] == "superseded"
        assert by_id["DEC-2"]["status"] == "active"
        assert [r["id"] for r in list_active(tmp_path)] == ["DEC-2"]

    def test_list_exports_rationale(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1", rationale="the WHY prose"))
        active = list_active(tmp_path)
        assert active[0]["rationale"] == "the WHY prose"

    def test_render_md_from_yaml_idempotent(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-2"), _yrec("DEC-1"))
        recs = dr._load_yaml_raw(tmp_path)
        a = dr.render_md(recs)
        b = dr.render_md(recs)
        assert a == b                                  # deterministic
        assert a.index("DEC-1") < a.index("DEC-2")     # sorted by number
        assert dr._GENERATED_MARKER in a

    def test_append_renders_md_view(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1"))
        dr.append_alloc(tmp_path, title="new", rationale="because")
        md = (tmp_path / "docs" / "decisions.md").read_text(encoding="utf-8")
        assert "## DEC-1" in md and "## DEC-2" in md   # rendered view present

    def test_id_scan_resilient_to_corrupt_record(self, tmp_path):
        # a corrupt record in the middle of the YAML must NOT drop id reservation
        # back to DEC-1 (RT-3) — the raw id-scan still sees DEC-1 and DEC-5
        p = _yaml_path(tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "- id: DEC-1\n  status: active\n  title: a\n  rationale: x\n"
            "- id: DEC-5\n  affects: [unterminated\n"  # corrupt block
            "  title: b\n", encoding="utf-8")
        assert alloc_id(tmp_path) == "DEC-6"           # max+1, not DEC-1

    def test_rationale_injection_inert_in_render(self, tmp_path):
        # a rationale carrying a fake record/heading must not smuggle a phantom
        # DEC when stored (YAML scalar) or rendered (escaped) — RT-6/F8
        _seed_yaml(tmp_path, _yrec("DEC-1"))
        evil = "para\n---\nid: DEC-99\n---\n## DEC-99 — fake\nmore"
        dr.append_alloc(tmp_path, title="t", rationale=evil)
        ids = [r["id"] for r in parse_decisions(tmp_path)]
        assert ids == ["DEC-1", "DEC-2"]               # no phantom DEC-99
        md = (tmp_path / "docs" / "decisions.md").read_text(encoding="utf-8")
        assert "\\---" in md and "\\## DEC-99" in md    # escaped in the view

    def test_yaml_supersede_empty_status_unflippable(self, tmp_path):
        # an explicit-empty status is the YAML analogue of a legacy record with
        # no status: line — it must be unflippable (append refuses)
        _seed_yaml(tmp_path, _yrec("DEC-1", status=""))
        with pytest.raises(DecisionError):
            dr.append_alloc(tmp_path, title="x", rationale="y", supersedes="DEC-1")

    def test_yaml_wins_when_both_md_and_yaml_present(self, tmp_path):
        # decisions.yaml is the SSOT: when BOTH a legacy decisions.md and a
        # decisions.yaml exist with contradicting content, parse_decisions must
        # return the YAML form (the gate-artifact analogue of yaml-over-json).
        _seed(tmp_path, _record("DEC-1", status="active"))           # md says active
        _seed_yaml(tmp_path, _yrec("DEC-1", status="superseded"))    # yaml says superseded
        by_id = {r["id"]: r for r in parse_decisions(tmp_path)}
        assert by_id["DEC-1"]["status"] == "superseded"              # yaml wins

    def test_yaml_null_status_behaves_like_empty_not_string_none(self, tmp_path):
        # a bare `status:` key parses to None and must behave EXACTLY like an
        # explicit-empty status: the raw stays falsy so the view defaults to
        # "active" (`"" or "active"`) AND the record is unflippable. The bug was
        # str(None)="None" — a truthy string that defeats BOTH (view shows "None",
        # _can_supersede treats it as a real status → wrongly flippable).
        _seed_yaml(tmp_path, _yrec("DEC-1", status=None))
        by_id = {r["id"]: r for r in parse_decisions(tmp_path)}
        assert by_id["DEC-1"]["status"] == "active"     # view-default fired (raw falsy)
        with pytest.raises(DecisionError):              # empty/null status unflippable
            dr.append_alloc(tmp_path, title="x", rationale="y", supersedes="DEC-1")

    def test_yaml_null_actor_ts_not_stringified_none(self, tmp_path):
        # null actor/ts/date keys must also coerce to "" rather than "None"
        rec = _yrec("DEC-1")
        rec.update(actor=None, ts=None, date=None)
        _seed_yaml(tmp_path, rec)
        r = {x["id"]: x for x in parse_decisions(tmp_path)}["DEC-1"]
        assert r["actor"] == "" and r["ts"] == "" and r["date"] == ""


class TestMigration:
    def test_migration_preserves_all_decs_and_rationale(self, tmp_path):
        _seed(tmp_path, _record("DEC-1"), _record("DEC-2", status="superseded",
                                                   supersedes="DEC-1"))
        before_ids = sorted(r["id"] for r in parse_decisions(tmp_path))
        res = dr.migrate_to_yaml(tmp_path)
        assert res["migrated"] == 2
        assert _yaml_path(tmp_path).is_file()          # now YAML mode
        after = parse_decisions(tmp_path)
        assert sorted(r["id"] for r in after) == before_ids
        # rationale preserved through the migration
        assert all(r["rationale"] for r in after)
        # superseded status survived
        by_id = {r["id"]: r for r in after}
        assert by_id["DEC-2"]["status"] == "superseded"


# ---------- cross-scope confirm gate (P3) ----------

def _run_gate(root, *args, active_plan=None):
    """Run the register CLI as a subprocess with a controlled active-plan env so
    the gate's scope resolution is deterministic (no inherited HARNESS_ACTIVE_PLAN)."""
    env = dict(os.environ)
    env.pop("HARNESS_ACTIVE_PLAN", None)
    if active_plan is not None:
        env["HARNESS_ACTIVE_PLAN"] = active_plan
    return subprocess.run(_cli(root, *args), capture_output=True, text=True, env=env)


# DEC-1 = flip target; DEC-2 shares rare tokens (rotation/cadence/interval/window)
# so it is a neighbour; DEC-3 is unrelated.
_T1 = ("Token rotation cadence",
       "rotate the refresh credential on a fixed cadence interval window")
_N2 = ("Refresh rotation window",
       "the rotation cadence interval window governs refresh lifetime")
_U3 = ("Database schema choice", "postgres ledger migration partition strategy")


def _seed_gate(root):
    _seed_yaml(root,
               _yrec("DEC-1", title=_T1[0], rationale=_T1[1]),
               _yrec("DEC-2", title=_N2[0], rationale=_N2[1]),
               _yrec("DEC-3", title=_U3[0], rationale=_U3[1]))


def _active_plan_dir(tmp_path, *, mentions="", in_phase=False):
    """A plan dir under tmp/plans with an in_progress plan.md. `mentions` text goes
    into plan.md (or a phase file when in_phase, for the R6 VL-text path)."""
    pdir = tmp_path / "plans" / "260629-1200-fixture"
    pdir.mkdir(parents=True, exist_ok=True)
    plan_body = "---\nstatus: in_progress\n---\n# fixture plan\n"
    if mentions and not in_phase:
        plan_body += mentions + "\n"
    (pdir / "plan.md").write_text(plan_body, encoding="utf-8")
    if in_phase:
        (pdir / "phase-9.md").write_text("## Validation Log\n" + mentions + "\n",
                                         encoding="utf-8")
    return str(pdir)


class TestCrossScopeGate:
    def test_cross_scope_supersede_blocks_exit2(self, tmp_path):
        _seed_gate(tmp_path)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1")
        assert r.returncode == 2, r.stdout + r.stderr
        finding = json.loads(r.stdout)
        assert finding["error"] == "cross_scope_block"
        assert "DEC-2" in finding["cross_scope"]
        # register byte-untouched: DEC-1 still active, no new record
        ids = {x["id"]: x for x in parse_decisions(tmp_path)}
        assert ids["DEC-1"]["status"] == "active"
        assert len(ids) == 3

    def test_in_scope_supersede_warns_allows(self, tmp_path):
        _seed_gate(tmp_path)
        ap = _active_plan_dir(tmp_path, mentions="references DEC-2 explicitly")
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1", active_plan=ap)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "warn" in r.stderr.lower() or "in-scope" in r.stderr.lower()
        assert {x["id"]: x for x in parse_decisions(tmp_path)}["DEC-1"]["status"] == "superseded"

    def test_cross_scope_with_valid_confirm_allows(self, tmp_path):
        _seed_gate(tmp_path)
        # mint a token for (DEC-1, {DEC-2}) via the confirm CLI
        confirm = [sys.executable, str(_SCRIPTS / "decision_confirm.py"),
                   "--root", str(tmp_path), "--confirm", "--target", "DEC-1",
                   "--neighbors", "DEC-2"]
        assert subprocess.run(confirm, capture_output=True, text=True).returncode == 0
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert {x["id"]: x for x in parse_decisions(tmp_path)}["DEC-1"]["status"] == "superseded"
        # token consumed: a SECOND cross-scope flip re-blocks (need a fresh target)
        _seed_yaml(tmp_path,
                   _yrec("DEC-1", status="superseded", title=_T1[0], rationale=_T1[1]),
                   _yrec("DEC-2", title=_N2[0], rationale=_N2[1]),
                   _yrec("DEC-3", title=_U3[0], rationale=_U3[1]),
                   _yrec("DEC-4", title=_T1[0], rationale=_T1[1]))
        r2 = _run_gate(tmp_path, "--append-alloc", "--title", "again",
                       "--rationale", "rotation cadence interval window refresh",
                       "--supersedes", "DEC-4")
        assert r2.returncode == 2

    def test_confirm_for_wrong_set_still_blocks(self, tmp_path):
        _seed_gate(tmp_path)
        confirm = [sys.executable, str(_SCRIPTS / "decision_confirm.py"),
                   "--root", str(tmp_path), "--confirm", "--target", "DEC-1",
                   "--neighbors", "DEC-99"]  # wrong set
        subprocess.run(confirm, capture_output=True, text=True)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1")
        assert r.returncode == 2

    def test_implicit_flip_warns_never_blocks(self, tmp_path):
        _seed_gate(tmp_path)
        # new active DEC (NO supersedes) strongly overlapping DEC-1 -> WARN only
        r = _run_gate(tmp_path, "--append-alloc", "--title", _T1[0],
                      "--rationale", _T1[1])
        assert r.returncode == 0, r.stdout + r.stderr
        assert "DEC-1" in r.stderr  # warns naming the rival ruling
        assert json.loads(r.stdout)["written"] is True

    def test_scan_flip_exit0_json(self, tmp_path):
        _seed_gate(tmp_path)
        r = _run_gate(tmp_path, "--scan-flip", "DEC-1")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert "DEC-2" in [n["id"] for n in out["neighbors"]]
        assert "cross_scope" in out and "in_scope" in out

    def test_structural_guards_intact(self, tmp_path):
        _seed_gate(tmp_path)
        # dangling supersedes -> still invalid_input exit 0 (NOT cross_scope exit 2)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "x",
                      "--rationale", "y", "--supersedes", "DEC-404")
        assert r.returncode == 0
        assert json.loads(r.stdout)["error"] == "invalid_input"

    def test_no_neighbors_no_block(self, tmp_path):
        _seed_yaml(tmp_path, _yrec("DEC-1", title=_U3[0], rationale=_U3[1]))
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "totally distinct entropy payload zebra",
                      "--supersedes", "DEC-1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "cross_scope" not in r.stderr.lower()

    def test_block_finding_names_active_plan_hint(self, tmp_path):
        _seed_gate(tmp_path)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1")
        finding = json.loads(r.stdout)
        assert "HARNESS_ACTIVE_PLAN" in finding.get("hint", "")
        assert "active_plan" in finding

    def test_block_detectable_from_stdout_only(self, tmp_path):
        _seed_gate(tmp_path)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1")
        # a caller that reads only stdout JSON (not $?) still sees the block
        assert json.loads(r.stdout)["error"] == "cross_scope_block"

    def test_implicit_flip_no_warn_on_weak_overlap(self, tmp_path):
        _seed_gate(tmp_path)
        # shares only 'rotation' with DEC-1/2 — below the high implicit threshold
        r = _run_gate(tmp_path, "--append-alloc", "--title", "Auth flow",
                      "--rationale", "rotation of unrelated session beacon thing")
        assert r.returncode == 0
        assert "có vẻ" not in r.stderr and "implicit" not in r.stderr.lower()

    def test_in_scope_via_validation_log(self, tmp_path):
        _seed_gate(tmp_path)
        ap = _active_plan_dir(tmp_path, mentions="- VL-9 | chốt DEC-2 ngưỡng",
                              in_phase=True)
        r = _run_gate(tmp_path, "--append-alloc", "--title", "new",
                      "--rationale", "rotation cadence interval window refresh",
                      "--supersedes", "DEC-1", active_plan=ap)
        assert r.returncode == 0, r.stdout + r.stderr
