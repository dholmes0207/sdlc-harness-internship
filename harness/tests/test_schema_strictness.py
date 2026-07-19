"""test_schema_strictness — three schema-strictness guards.

1. The register refuses to supersede a DEC that is ITSELF already superseded —
   a false supersede chain (retiring an already-retired ruling) is named and
   rejected, never silently appended.
2. harness-release.json constrains `channel` to the enum its own description
   names (dev | beta | stable) — a stray channel value fails the schema.
3. validate_artifacts is a WARN-class checker: it loads a schema + an artifact
   and reports missing-required / wrong-type / bad-enum mismatches as warnings,
   and never raises / never blocks.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import decision_register as dr  # noqa: E402
from decision_register import DecisionError, append_decision  # noqa: E402
import validate_artifacts as va  # noqa: E402

_SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


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


# ---------- 1. no false supersede chain ----------

class TestNoFalseSupersedeChain:
    def test_supersede_of_already_superseded_is_rejected(self, tmp_path):
        # DEC-1 was retired by DEC-2. Trying to supersede DEC-1 AGAIN (with a
        # new DEC-3) is a false chain: the ruling is already dead. Refuse and
        # name the offender.
        _seed(tmp_path,
              _record("DEC-1", status="superseded"),
              _record("DEC-2", status="active", supersedes="DEC-1"))
        before = _decisions_path(tmp_path).read_text(encoding="utf-8")
        with pytest.raises(DecisionError) as exc:
            dr.append_alloc(tmp_path, title="re-retire", rationale="y",
                            supersedes="DEC-1")
        assert "DEC-1" in str(exc.value)  # names the offender
        # register byte-untouched: no DEC-3 appended
        assert _decisions_path(tmp_path).read_text(encoding="utf-8") == before

    def test_supersede_of_active_still_allowed(self, tmp_path):
        # The guard must not over-fire: superseding an ACTIVE ruling is the
        # normal path and stays allowed.
        _seed(tmp_path, _record("DEC-1", status="active"))
        result = dr.append_alloc(tmp_path, title="switch", rationale="y",
                                 supersedes="DEC-1")
        assert result["written"] is True
        by_id = {r["id"]: r for r in dr.parse_decisions(tmp_path)}
        assert by_id["DEC-1"]["status"] == "superseded"
        assert by_id[result["id"]]["supersedes"] == "DEC-1"

    def test_append_decision_rejects_superseded_target_directly(self, tmp_path):
        # The library entry append_decision also refuses a superseded target,
        # so a caller bypassing append_alloc cannot smuggle a false chain.
        _seed(tmp_path,
              _record("DEC-1", status="superseded"),
              _record("DEC-2", status="active", supersedes="DEC-1"))
        with pytest.raises(DecisionError) as exc:
            append_decision(tmp_path, dec_id="DEC-3", title="re", rationale="r",
                            supersedes="DEC-1")
        assert "DEC-1" in str(exc.value)


# ---------- 2. release-channel enum ----------

class TestReleaseChannelEnum:
    def _schema(self):
        return json.loads((_SCHEMAS / "harness-release.json").read_text(
            encoding="utf-8"))


    def test_bad_channel_value_flagged(self, tmp_path):
        schema = self._schema()
        artifact = {"schema_version": "1.0", "harness_version": "1.2.3",
                    "channel": "nightly"}
        findings = va.validate(schema, artifact)
        assert any("channel" in f for f in findings)

    def test_good_channel_value_passes(self, tmp_path):
        schema = self._schema()
        artifact = {"schema_version": "1.0", "harness_version": "1.2.3",
                    "channel": "stable"}
        assert va.validate(schema, artifact) == []


# ---------- 3. validate_artifacts (WARN-class) ----------

class TestValidateArtifacts:
    def _schema(self):
        return json.loads((_SCHEMAS / "artifact-review-decision.json").read_text(
            encoding="utf-8"))

    def test_missing_required_field_flagged(self):
        schema = self._schema()
        artifact = {"verdict": "PASS", "reviewer": "user:x", "role": "lead"}
        findings = va.validate(schema, artifact)  # rationale missing
        assert any("rationale" in f for f in findings)

    def test_wrong_type_flagged(self):
        schema = self._schema()
        artifact = {"verdict": "PASS", "reviewer": 42, "role": "lead",
                    "rationale": "why"}
        findings = va.validate(schema, artifact)
        assert any("reviewer" in f for f in findings)

    def test_bad_enum_flagged(self):
        schema = self._schema()
        artifact = {"verdict": "MAYBE", "reviewer": "user:x", "role": "lead",
                    "rationale": "why"}
        findings = va.validate(schema, artifact)
        assert any("verdict" in f for f in findings)

    def test_clean_artifact_no_findings(self):
        schema = self._schema()
        artifact = {"verdict": "PASS", "reviewer": "user:x", "role": "lead",
                    "rationale": "why"}
        assert va.validate(schema, artifact) == []

    def test_const_mismatch_flagged(self):
        schema = json.loads((_SCHEMAS / "artifact-plan-approval.json").read_text(
            encoding="utf-8"))
        artifact = {"schema": "plan-approval/v2", "plan": "p",
                    "plan_hash": "0" * 12, "author": "a", "reviewer": "r",
                    "verdict": "APPROVED", "rationale": "ok", "ts": "t"}
        findings = va.validate(schema, artifact)
        assert any("schema" in f for f in findings)

    def test_union_type_accepts_null(self):
        # manifest_files_count is ["integer", "null"] — null must pass.
        schema = json.loads((_SCHEMAS / "harness-release.json").read_text(
            encoding="utf-8"))
        artifact = {"schema_version": "1.0", "harness_version": "1.2.3",
                    "channel": "dev", "manifest_files_count": None}
        assert va.validate(schema, artifact) == []

    def test_validate_never_raises_on_garbage(self):
        # WARN-class contract: a non-dict artifact, missing schema keys, or
        # odd values produce findings (or none) but NEVER an exception.
        schema = self._schema()
        for junk in (None, [], "string", 42, {"verdict": ["nested"]}):
            out = va.validate(schema, junk)
            assert isinstance(out, list)  # always a list, never a raise

    def test_cli_reports_findings_and_exits_zero(self, tmp_path, monkeypatch,
                                                  capsys):
        # The CLI is WARN-class: it prints findings as JSON and exits 0
        # (never blocks).
        art = tmp_path / "bad.json"
        art.write_text(json.dumps(
            {"verdict": "MAYBE", "reviewer": "user:x", "role": "lead"}),
            encoding="utf-8")
        schema = _SCHEMAS / "artifact-review-decision.json"
        argv = ["validate_artifacts.py", "--schema", str(schema),
                "--artifact", str(art)]
        monkeypatch.setattr(sys, "argv", argv)
        rc = va.main()
        assert rc == 0  # WARN-class never blocks
        out = json.loads(capsys.readouterr().out)
        assert out["findings"]  # mismatches surfaced
