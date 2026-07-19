"""Append-only guard for the YAML-SSOT registers.

Both the decision register (docs/decisions.yaml) and the backlog register
(docs/backlog.yaml) use a read-modify-write on their SSOT: load raw records,
append/flip, dump the whole list. `_load_yaml_raw` returns [] on a whole-file
parse failure, so a single corrupt byte would turn the next write into a full
truncation — the resilient id-scan still reserves the right next id, but every
prior record is silently lost while the call reports success.

These tests pin the guard: a dump that would drop an id the resilient scan can
still see is refused (raise), leaving the SSOT byte-untouched. Also pins the
id-alloc heading-union so a heading-only `## DEC-<n>` record still reserves its
number.
"""
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import decision_register as dr  # noqa: E402
import backlog_register as br  # noqa: E402


def _dec_rec(dec_id, title="t"):
    return {"id": dec_id, "status": "active", "date": "2026-06-01",
            "actor": "x", "ts": "2026-06-01T00:00:00+00:00", "affects": "",
            "supersedes": "", "title": title, "rationale": "r"}


def _seed_dec_yaml(root, recs):
    p = root / "docs" / "decisions.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(recs, sort_keys=False, allow_unicode=True),
                 encoding="utf-8")
    return p


def _bl_rec(bl_id, text="t"):
    return {"id": bl_id, "text": text, "type": "chore", "priority": "P2",
            "status": "open", "created_ts": "2026-06-01T00:00:00+00:00",
            "done_ts": "", "source_ref": "", "actor": "x"}


def _seed_bl_yaml(root, recs):
    p = root / "docs" / "backlog.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(recs, sort_keys=False, allow_unicode=True),
                 encoding="utf-8")
    return p


class TestDecisionClobberGuard:
    def test_append_refuses_to_clobber_unparseable_ssot(self, tmp_path):
        p = _seed_dec_yaml(tmp_path, [_dec_rec("DEC-1"), _dec_rec("DEC-2")])
        # corrupt so yaml.safe_load fails, but `id:` lines stay scannable
        p.write_text(p.read_text(encoding="utf-8") + "\n\t- id: [unterminated\n",
                     encoding="utf-8")
        assert dr._load_yaml_raw(tmp_path) == []
        assert set(dr._scan_all_ids(tmp_path)) >= {"DEC-1", "DEC-2"}
        with pytest.raises(dr.DecisionError):
            dr.append_alloc(tmp_path, title="three", rationale="r3")
        txt = p.read_text(encoding="utf-8")
        assert "DEC-1" in txt and "DEC-2" in txt

    def test_heading_only_record_reserves_its_number(self, tmp_path):
        # markdown mode: a heading-only ## DEC-5 (no id: fence) must still count
        p = tmp_path / "docs" / "decisions.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Decision Register\n\n## DEC-5 — hand-added ruling\n\n"
                     "Some rationale.\n", encoding="utf-8")
        assert dr.alloc_id(tmp_path) == "DEC-6"


class TestBacklogClobberGuard:
    def test_add_refuses_to_clobber_unparseable_ssot(self, tmp_path):
        p = _seed_bl_yaml(tmp_path, [_bl_rec("BL-001"), _bl_rec("BL-002")])
        p.write_text(p.read_text(encoding="utf-8") + "\n\t- id: [unterminated\n",
                     encoding="utf-8")
        assert br._load_yaml_raw(tmp_path) == []
        assert set(br._scan_all_ids(tmp_path)) >= {"BL-001", "BL-002"}
        with pytest.raises(br.BacklogError):
            br.add(tmp_path, text="three", type="chore", priority="P2")
        txt = p.read_text(encoding="utf-8")
        assert "BL-001" in txt and "BL-002" in txt
