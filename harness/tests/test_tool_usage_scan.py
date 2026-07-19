"""test_tool_usage_scan.py — objective verifier for authored allowed-tools.

When a skill DECLARES allowed-tools, it must not omit a tool the skill clearly needs,
or the skill silently breaks at runtime. This verifier uses only HIGH-CONFIDENCE signals
(precision over recall) so it never false-alarms: a scripts/ dir implies Bash, a `Task(`
call implies Task, an explicit WebFetch/WebSearch mention implies that tool. It checks
the declared allowed-tools is a superset of those — under-declaration is the failure mode.
A skill with no allowed-tools yet is not flagged (nothing authored to check).
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import tool_usage_scan as tus  # noqa: E402


def _skill(tmp_path, frontmatter, body="# b\n", scripts=None):
    d = tmp_path / "s"
    d.mkdir(exist_ok=True)
    (d / "SKILL.md").write_text("---\n%s\n---\n\n%s" % (frontmatter, body), encoding="utf-8")
    if scripts:
        sd = d / "scripts"
        sd.mkdir(exist_ok=True)
        for n in scripts:
            (sd / n).write_text("print('x')\n", encoding="utf-8")
    return d


def test_detects_bash_from_scripts_dir(tmp_path):
    d = _skill(tmp_path, "name: hs:x\ndescription: d", scripts=["run.py"])
    assert "Bash" in tus.detect_required_tools(str(d))


def test_detects_task_from_body_call(tmp_path):
    d = _skill(tmp_path, "name: hs:x\ndescription: d", body="Spawn Task(hs:researcher) to scout.\n")
    assert "Task" in tus.detect_required_tools(str(d))


def test_detects_websearch_mention(tmp_path):
    d = _skill(tmp_path, "name: hs:x\ndescription: d", body="Use WebSearch for current docs.\n")
    assert "WebSearch" in tus.detect_required_tools(str(d))


def test_flags_declared_allowed_tools_missing_bash(tmp_path):
    d = _skill(tmp_path, "name: hs:x\ndescription: d\nallowed-tools: Read, Grep", scripts=["run.py"])
    findings = tus.check_allowed_tools(str(d))
    assert any("Bash" in f for f in findings)


def test_no_flag_when_superset(tmp_path):
    d = _skill(tmp_path, "name: hs:x\ndescription: d\nallowed-tools: [Bash, Read, Task]",
               body="Task(hs:dev)\n", scripts=["run.py"])
    assert tus.check_allowed_tools(str(d)) == []


def test_no_flag_when_allowed_tools_absent(tmp_path):
    # not yet authored -> nothing to verify, do not flag
    d = _skill(tmp_path, "name: hs:x\ndescription: d", scripts=["run.py"])
    assert tus.check_allowed_tools(str(d)) == []
