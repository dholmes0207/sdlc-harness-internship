"""Verb CLI + append-only job registry contract for the ccs partner lane
(phase 4). The registry lives under harness/state/partner/ (gitignored,
subdir isolates it from the gemini lane's registry); HARNESS_STATE_DIR
redirects it to a tmp dir for the suite. `--provider` is mandatory — the
lane never calls ccs blind.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_companion as pc  # noqa: E402
import partner_preflight as pf  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_ccs.py"

# The shipped harness/data/partner.yaml ships master=off (factory-safe) — the
# CLI tests need master=on to actually spawn, so every call below passes
# --config pointed at this tmp file rather than the shipped default.
_BASE_CFG = {
    "master": "on", "write": "read_only", "allow_live": "off", "secret_scrub": "warn",
    "purposes": {"review": "review", "adversarial-review": "redteam",
                 "research": "research", "critique": "critique"},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "cost_warn_usd": 0.50,
}


def _cfg_path(tmp_path, **over):
    p = tmp_path / "partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE_CFG, **over}, sort_keys=False), encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_CCS_CMD", "%s %s" % (sys.executable, _FAKE))
    monkeypatch.delenv("FAKE_CCS_MODE", raising=False)
    monkeypatch.setattr(pf, "discover_providers", lambda: ["minimax"])
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def _jobs():
    return pc.JobRegistry(subdir="partner").read_all()


def test_cli_review_envelope(tmp_path, capsys):
    rc = pc.main(["review", "--provider", "minimax", "-p", "x",
                 "--config", _cfg_path(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] is not None
    assert out["cost"] == pytest.approx(0.0042)
    assert out["provenance"]["reviewer_engine"] == "ccs:minimax"

    recs = _jobs()
    assert [r for r in recs if r["status"] == "running"]
    assert [r for r in recs if r["status"] == "done"]


def test_provider_required():
    with pytest.raises(SystemExit):
        pc.main(["review", "-p", "x"])


def test_status_and_result_lookup(tmp_path, capsys):
    pc.main(["review", "--provider", "minimax", "-p", "hello",
             "--config", _cfg_path(tmp_path)])
    job_id = _jobs()[-1]["job_id"]
    capsys.readouterr()

    rc = pc.main(["status", job_id])
    assert rc == 0
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["status"] == "done"

    rc2 = pc.main(["result", job_id])
    assert rc2 == 0
    result_out = json.loads(capsys.readouterr().out)
    assert "hello" in result_out["result"]["text"]
    assert result_out["provenance"]["reviewer_engine"] == "ccs:minimax"
