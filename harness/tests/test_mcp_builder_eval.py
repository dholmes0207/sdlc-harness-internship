"""test_mcp_builder_eval.py — the restored MCP-server evaluation harness runs.

P9 reverses the ledgered "eval-runner dropped" decision. evaluation.py drives an
Anthropic tool-use agent loop against an MCP server and scores the answers; connections.py
selects the transport. The Anthropic SDK + the `mcp` SDK are the skill's own runtime deps
(declared in scripts/requirements.txt), so the client/connection layers are injected here:
the scoring + report logic runs FOR REAL against a stub client, no SDK or API key needed.
Red before the port (the scripts were absent).
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "plugins" / "hs" / "skills" / "mcp-builder" / "scripts"


def _load_evaluation():
    """Import evaluation.py from the skill scripts dir (it has no package __init__)."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("mcp_eval_under_test", _SCRIPTS / "evaluation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- stubs that stand in for the Anthropic client + MCP connection ----

class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]
        self.stop_reason = "end_turn"


class _Messages:
    def __init__(self, reply):
        self._reply = reply

    def create(self, **kwargs):  # sync — agent_loop wraps it in asyncio.to_thread
        return _Resp(self._reply)


class _FakeClient:
    def __init__(self, reply):
        self.messages = _Messages(reply)


class _FakeConnection:
    async def list_tools(self):
        return []

    async def call_tool(self, name, args):  # not reached in the no-tool path
        return {}


def test_assets_present():
    for f in ("evaluation.py", "connections.py", "example_evaluation.xml", "requirements.txt"):
        assert (_SCRIPTS / f).exists(), f"{f} missing"
    reqs = (_SCRIPTS / "requirements.txt").read_text()
    assert "anthropic" in reqs and "mcp" in reqs, "runtime deps not declared"


def test_module_imports_without_sdk():
    """The ADAPT: module imports cleanly even though anthropic/mcp are absent."""
    mod = _load_evaluation()
    assert callable(mod.run_evaluation)
    assert callable(mod.parse_evaluation_file)


def test_parse_and_extract_logic():
    mod = _load_evaluation()
    qa = mod.parse_evaluation_file(_SCRIPTS / "example_evaluation.xml")
    assert len(qa) >= 3 and qa[0]["question"] and qa[0]["answer"]
    assert mod.extract_xml_content("<response>11614.72</response>", "response") == "11614.72"
    assert mod.extract_xml_content("no tags here", "response") is None
    # a live agent_loop can return no text block -> None must not crash (re.findall guard)
    assert mod.extract_xml_content(None, "response") is None


def test_run_evaluation_scores_correct_answer(tmp_path):
    """Inject a stub client that returns the right answer → report shows 100% accuracy."""
    mod = _load_evaluation()
    eval_file = tmp_path / "eval.xml"
    eval_file.write_text(
        "<evaluation><qa_pair><question>What is 6 times 7?</question>"
        "<answer>42</answer></qa_pair></evaluation>",
        encoding="utf-8",
    )
    reply = "<summary>multiplied</summary><feedback>fine</feedback><response>42</response>"
    report = asyncio.run(
        mod.run_evaluation(eval_file, _FakeConnection(), model="stub", client=_FakeClient(reply))
    )
    assert "1/1 (100.0%)" in report, report


def test_run_evaluation_flags_wrong_answer(tmp_path):
    mod = _load_evaluation()
    eval_file = tmp_path / "eval.xml"
    eval_file.write_text(
        "<evaluation><qa_pair><question>What is 6 times 7?</question>"
        "<answer>42</answer></qa_pair></evaluation>",
        encoding="utf-8",
    )
    reply = "<response>41</response>"
    report = asyncio.run(
        mod.run_evaluation(eval_file, _FakeConnection(), model="stub", client=_FakeClient(reply))
    )
    assert "0/1 (0.0%)" in report, report


class _ToolNoBlockResp:
    """stop_reason says tool_use but content carries NO tool_use block (a server-side
    tool block, or an empty/degenerate response). The agent loop must not crash on this."""
    def __init__(self):
        self.content = [_Block("<response>NOT_FOUND</response>")]
        self.stop_reason = "tool_use"


class _ToolNoBlockClient:
    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        return _ToolNoBlockResp()


def test_tool_use_with_no_tool_block_does_not_crash(tmp_path):
    """stop_reason=='tool_use' + no tool_use block must break the loop and score 0,
    not raise StopIteration->RuntimeError that aborts the whole run."""
    mod = _load_evaluation()
    eval_file = tmp_path / "eval.xml"
    eval_file.write_text(
        "<evaluation><qa_pair><question>q</question><answer>42</answer></qa_pair></evaluation>",
        encoding="utf-8",
    )
    report = asyncio.run(
        mod.run_evaluation(eval_file, _FakeConnection(), model="stub", client=_ToolNoBlockClient())
    )
    assert "0/1 (0.0%)" in report, report


def test_connections_transport_selection():
    """Transport routing — runs only where the `mcp` SDK is installed (skill runtime dep)."""
    pytest.importorskip("mcp", reason="mcp SDK is the skill's runtime dep, absent on this host")
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import connections  # noqa: WPS433

    with pytest.raises(ValueError):
        connections.create_connection(transport="bogus")


def test_no_upstream_brand():
    for f in ("evaluation.py", "connections.py"):
        low = (_SCRIPTS / f).read_text(encoding="utf-8").lower()
        assert "claudekit" not in low and ".claude/" not in low
