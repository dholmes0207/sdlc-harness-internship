"""test_nudge_channels.py — the configurable nudge-visibility trin (INV-3 F-2).

A nudge advisory reaches nobody unless routed. This suite pins the three-sink
router: a `nudge-channels.yaml` (human-edited, ship-global; dev override via
HARNESS_NUDGE_CHANNELS) maps each nudge name -> relay | systemMessage | stderr |
off, and hook_runtime.emit_nudge() dispatches accordingly.

Precedence (load-bearing): per-name file entry > file-global `default:` (only when
the file sets one) > the caller's code-level default_channel > "stderr". Ship omits
`default:` so unlisted security nudges keep their code default (systemMessage);
dev sets `default: systemMessage` so everything is visible.

Hermetic: HARNESS_NUDGE_CHANNELS pins the config; HARNESS_HOOK_LOG_DIR the crash
log; HARNESS_STATE_DIR the trace store. PYTEST_CURRENT_TEST cleared where a write
must actually land.
"""
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS))


@pytest.fixture(autouse=True)
def _reset_hook_runtime_caches():
    """Some tests here set HARNESS_HOOK_CONFIG / HARNESS_NUDGE_CHANNELS and populate
    hook_runtime's MODULE-level caches. monkeypatch restores the env but not those
    caches, so without this teardown a stale config would leak into later test FILES
    (e.g. a *_default_enabled test reading a tmp config that disables the nudge)."""
    yield
    import hook_runtime
    hook_runtime._reset_config_cache()
    hook_runtime._reset_nudge_channels_cache()
    hook_runtime._reset_pending_system_messages()


def _fresh(monkeypatch, tmp_path, *, channels=None, state_dir=None):
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    if state_dir is not None:
        monkeypatch.setenv("HARNESS_STATE_DIR", str(state_dir))
    if channels is not None:
        cfg = tmp_path / "nudge-channels.yaml"
        cfg.write_text(yaml.safe_dump(channels), encoding="utf-8")
        monkeypatch.setenv("HARNESS_NUDGE_CHANNELS", str(cfg))
    else:
        monkeypatch.delenv("HARNESS_NUDGE_CHANNELS", raising=False)
    # No sys.modules reload: the loaders read env at CALL time, so resetting the
    # caches is enough. Reloading would leave a fresh hook_runtime object in
    # sys.modules and pollute later test files that call hook_runtime.hook_enabled.
    import hook_runtime
    hook_runtime._reset_config_cache()
    hook_runtime._reset_nudge_channels_cache()
    hook_runtime._reset_pending_system_messages()
    return hook_runtime


# ---------------------------------------------------------------------------
# nudge_channel() — resolution + precedence
# ---------------------------------------------------------------------------

class TestChannelResolution:
    def test_per_name_entry_wins(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}})
        assert hr.nudge_channel("foo", "stderr") == "systemMessage"

    def test_unlisted_falls_to_caller_default(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "relay"}})
        # bar not listed, no file-global default -> caller's default_channel
        assert hr.nudge_channel("bar", "systemMessage") == "systemMessage"

    def test_file_global_default_beats_caller_default(self, tmp_path, monkeypatch):
        # dev posture: default: systemMessage makes everything visible, even a
        # hook whose code default is stderr.
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"default": "systemMessage", "channels": {}})
        assert hr.nudge_channel("anything", "stderr") == "systemMessage"

    def test_per_name_beats_file_global_default(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"default": "systemMessage",
                              "channels": {"quiet": "off"}})
        assert hr.nudge_channel("quiet", "stderr") == "off"
        assert hr.nudge_channel("other", "stderr") == "systemMessage"

    def test_no_file_falls_to_caller_default(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, channels=None)
        assert hr.nudge_channel("foo", "relay") == "relay"

    def test_invalid_channel_value_ignored(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "megaphone"}})
        # bogus value dropped -> caller default (fail-open, never crash)
        assert hr.nudge_channel("foo", "stderr") == "stderr"

    def test_malformed_file_falls_open(self, tmp_path, monkeypatch):
        cfg = tmp_path / "nudge-channels.yaml"
        cfg.write_text(": : not yaml : :", encoding="utf-8")
        monkeypatch.setenv("HARNESS_NUDGE_CHANNELS", str(cfg))
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        import hook_runtime
        hook_runtime._reset_nudge_channels_cache()
        assert hook_runtime.nudge_channel("foo", "systemMessage") == "systemMessage"

    def test_bare_off_yaml_boolean_coerced(self, tmp_path, monkeypatch):
        # YAML 1.1 parses a bare `off` as boolean False; the loader must coerce it
        # back to the "off" channel (a raw yaml file, NOT yaml.safe_dump which would
        # quote it — this is the real on-disk shape a human writes).
        cfg = tmp_path / "nudge-channels.yaml"
        cfg.write_text("channels:\n  foo: off\n", encoding="utf-8")
        monkeypatch.setenv("HARNESS_NUDGE_CHANNELS", str(cfg))
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        import hook_runtime
        hook_runtime._reset_nudge_channels_cache()
        assert hook_runtime.nudge_channel("foo", "stderr") == "off"

    def test_missing_env_file_falls_open(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_NUDGE_CHANNELS", str(tmp_path / "nope.yaml"))
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        import hook_runtime
        hook_runtime._reset_nudge_channels_cache()
        assert hook_runtime.nudge_channel("foo", "stderr") == "stderr"


# ---------------------------------------------------------------------------
# emit_nudge() — routing to the right sink
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_stderr_channel_writes_stderr_only(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "stderr"}})
        hr.emit_nudge("foo", "hello there", session="s1")
        err = capsys.readouterr().err
        assert "hello there" in err
        assert hr._drain_system_messages() == ""  # nothing queued

    def test_systemmessage_channel_queues(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}})
        hr.emit_nudge("foo", "warn user", session="s1")
        assert capsys.readouterr().err == ""       # not on stderr
        assert hr._drain_system_messages() == "\nwarn user"  # leading \n under the label

    def test_off_channel_is_silent(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "off"}})
        hr.emit_nudge("foo", "nobody hears", session="s1")
        assert capsys.readouterr().err == ""
        assert hr._drain_system_messages() == ""

    def test_systemmessage_ALSO_records_observation(self, tmp_path, monkeypatch, capsys):
        # INVARIANT (hard): systemMessage is an ADDITIVE human layer — a systemMessage
        # never enters the model context, so the model bus (the observation that a
        # resurface turns into additionalContext) MUST still fire. Otherwise a
        # systemMessage-only nudge is invisible to the model in an autonomous /goal.
        state = tmp_path / "state"
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}}, state_dir=state)
        hr.emit_nudge("foo", "warn user AND model", session="sess-9", kind="foo")
        # human layer still queues
        assert hr._drain_system_messages() == "\nwarn user AND model"
        # model bus: observation STILL recorded (fails today — systemMessage skips it)
        trace = state / "trace"
        recs = []
        for f in trace.glob("trace-*.jsonl"):
            recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        obs = [r for r in recs if r.get("event") == "foo_observation"]
        assert len(obs) == 1
        assert obs[0]["session"] == "sess-9"

    def test_route_relay_systemmessage_ALSO_calls_record_obs(self, tmp_path, monkeypatch, capsys):
        # Same invariant for the relay-default router: systemMessage must not drop
        # the model bus (record_obs).
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}})
        called = {"n": 0}
        hr.route_relay_nudge("foo", "both channels",
                             lambda: called.__setitem__("n", called["n"] + 1))
        assert hr._drain_system_messages() == "\nboth channels"  # human layer
        assert called["n"] == 1                                 # model bus (fails today)

    def test_relay_channel_records_observation(self, tmp_path, monkeypatch, capsys):
        state = tmp_path / "state"
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "relay"}}, state_dir=state)
        hr.emit_nudge("foo", "model should act", session="sess-42", kind="foo")
        assert capsys.readouterr().err == ""       # relay is not stderr
        # an observation event foo_observation lands in the trace store
        trace = state / "trace"
        recs = []
        for f in trace.glob("trace-*.jsonl"):
            recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        obs = [r for r in recs if r.get("event") == "foo_observation"]
        assert len(obs) == 1
        assert obs[0]["session"] == "sess-42"
        assert obs[0]["note"] == "model should act"

    def test_relay_without_session_degrades_to_stderr(self, tmp_path, monkeypatch, capsys):
        # can't relay an anonymous observation (no session to filter on) -> stderr
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "relay"}})
        hr.emit_nudge("foo", "orphan advisory", session=None)
        assert "orphan advisory" in capsys.readouterr().err

    def test_empty_text_is_noop(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}})
        hr.emit_nudge("foo", "", session="s1")
        assert capsys.readouterr().err == ""
        assert hr._drain_system_messages() == ""


# ---------------------------------------------------------------------------
# run_nudge_hook honours the configured channel end-to-end
# ---------------------------------------------------------------------------

class TestNudgeWrapperRouting:
    def _run(self, hr, name, msg, session="s1"):
        raw = json.dumps({"session_id": session})
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            hr.run_nudge_hook(name, lambda data: msg, raw=raw)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def _enabled(self, name):
        return {"hooks": {name: {"enabled": True}}}

    def test_stderr_default_no_systemmessage_in_stdout(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "stderr"}})
        # nudge must be enabled to fire; pin via hook config
        monkeypatch.setenv("HARNESS_HOOK_CONFIG",
                           str(self._write_cfg(tmp_path, "foo")))
        hr._reset_config_cache()
        out = self._run(hr, "foo", "advice text")
        blob = json.loads(out)
        assert blob.get("continue") is True
        assert "systemMessage" not in blob            # stderr path
        assert "advice text" in capsys.readouterr().err

    def test_systemmessage_channel_surfaces_in_stdout(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": "systemMessage"}})
        monkeypatch.setenv("HARNESS_HOOK_CONFIG",
                           str(self._write_cfg(tmp_path, "foo")))
        hr._reset_config_cache()
        out = self._run(hr, "foo", "warn the human")
        blob = json.loads(out)
        assert blob["systemMessage"] == "\nwarn the human"

    @staticmethod
    def _write_cfg(tmp_path, name):
        p = tmp_path / "harness-hooks.yaml"
        p.write_text(yaml.safe_dump({"hooks": {name: {"enabled": True}}}),
                     encoding="utf-8")
        return p


# ---------------------------------------------------------------------------
# generic relay: a config-relay hook surfaces via nudge_context_inject next turn
# ---------------------------------------------------------------------------

class TestGenericRelay:
    def _reload_inject(self):
        sys.modules.pop("nudge_context_inject", None)
        import nudge_context_inject
        importlib.reload(nudge_context_inject)
        return nudge_context_inject

    def test_relay_hook_surfaces_and_is_one_shot(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"cook_isolation": "relay"}},
                    state_dir=state)
        # a group-3 hook the user routed to relay records its observation ...
        hr.emit_nudge("cook_isolation", "[nudge] cook_isolation: /clear then cook",
                      session="sID")
        inj = self._reload_inject()
        out = inj.core({"session_id": "sID"})
        assert out is not None
        assert "cook_isolation" in out
        assert "/clear" in out
        # one-shot: a second prompt with no newer observation stays silent
        assert inj.core({"session_id": "sID"}) is None

    def test_bespoke_relay_name_not_double_injected(self, tmp_path, monkeypatch):
        # decision_capture is served by a bespoke builder; even if a user lists it
        # as relay, the generic pass must skip it (no duplicate line).
        state = tmp_path / "state"
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"decision_capture": "relay"}},
                    state_dir=state)
        # record ONLY a generic decision_capture_observation (no bespoke event)
        hr._record_nudge_observation("decision_capture", "x", session="sID2")
        inj = self._reload_inject()
        out = inj.core({"session_id": "sID2"})
        # generic pass skipped it; the bespoke family looks for decision_capture_
        # observation too, but here it's fine either way — assert no generic tag
        assert out is None or "[nudge:decision_capture]" not in out

    def test_unlisted_hook_not_relayed(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"other": "systemMessage"}},
                    state_dir=state)
        hr._record_nudge_observation("cook_isolation", "note", session="sID3")
        inj = self._reload_inject()
        # cook_isolation is not relay in config -> generic pass ignores it
        assert inj.core({"session_id": "sID3"}) is None


# ---------------------------------------------------------------------------
# NEW 2-axis form {model, user}: the two axes are independent, and the enum-era
# combination that was IMPOSSIBLE to express — user-only (human sees it, model
# blind) — is now first-class.
# ---------------------------------------------------------------------------

class TestTwoAxisForm:
    def test_both_axes_record_and_queue(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": {"model": True, "user": True}}})
        assert hr.nudge_axes("foo", "stderr") == {"model": True, "user": True, "stderr": False}
        assert hr.nudge_channel("foo", "stderr") == "systemMessage"  # shim
        hr.emit_nudge("foo", "both", session="s1")
        assert hr._drain_system_messages() == "\nboth"    # user axis queued (leading \n)
        assert capsys.readouterr().err == ""              # not degraded to stderr

    def test_model_only_records_no_systemmessage(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": {"model": True, "user": False}}})
        assert hr.nudge_channel("foo", "stderr") == "relay"
        hr.emit_nudge("foo", "model only", session="s1")
        assert hr._drain_system_messages() == ""          # no user layer queued
        assert capsys.readouterr().err == ""              # session present -> recorded, no stderr
        assert "foo" in hr.nudge_observation_names()      # on the model bus

    def test_user_only_queues_but_never_records(self, tmp_path, monkeypatch, capsys):
        # The enum could NOT express this: human sees it, model stays blind.
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": {"model": False, "user": True}}})
        assert hr.nudge_axes("foo", "stderr") == {"model": False, "user": True, "stderr": False}
        assert hr.nudge_channel("foo", "stderr") == "off"  # no legacy name fits -> closest
        hr.emit_nudge("foo", "human only", session="s1")
        assert hr._drain_system_messages() == "\nhuman only"
        assert "foo" not in hr.nudge_observation_names()   # NOT on the model bus

    def test_neither_axis_is_off(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": {"model": False, "user": False}}})
        hr.emit_nudge("foo", "silent", session="s1")
        assert hr._drain_system_messages() == ""
        assert capsys.readouterr().err == ""

    def test_route_relay_nudge_two_axis_user_only(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"channels": {"foo": {"model": False, "user": True}}})
        calls = []
        hr.route_relay_nudge("foo", "hi", lambda: calls.append(1))
        assert calls == []                                 # model off -> record_obs NOT called
        assert hr._drain_system_messages() == "\nhi"       # user on -> queued (leading \n)

    def test_file_global_default_two_axis(self, tmp_path, monkeypatch):
        # dev posture expressed the clean way: default both axes on.
        hr = _fresh(monkeypatch, tmp_path,
                    channels={"default": {"model": True, "user": True},
                              "channels": {}})
        assert hr.nudge_axes("anything", "stderr") == {"model": True, "user": True, "stderr": False}
