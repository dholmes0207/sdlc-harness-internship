"""test_output_config.py — loader for harness/data/output.yaml.

The output config governs the language of harness-GENERATED prose (reports,
docs) and whether the humanizer rule is applied. Instruction files are English;
this setting picks the OUTPUT language (default Vietnamese for this harness).

One shared loader: no env override (the setting is
tracked config, a change is a git-visible diff), loud on missing/malformed,
language constrained to the en/vi enum.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from output_config import (  # noqa: E402
    OutputConfigError,
    language,
    load,
    load_output,
    main,
    resolve_all,
    save_output,
)


def _write(tmp_path, text):
    p = tmp_path / "output.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestLoadOutput:
    def test_valid_file_parses(self, tmp_path):
        p = _write(tmp_path, "language: vi\nhumanize: true\n")
        cfg = load_output(path=str(p))
        # audience/code_style absent → None; thinking_language absent → default en
        assert cfg == {
            "language": "vi", "humanize": True,
            "audience": None, "code_style": None,
            "thinking_language": "en",
        }

    def test_thinking_language_default_en_and_override(self, tmp_path):
        # absent → default en
        p = _write(tmp_path, "language: vi\n")
        assert load_output(path=str(p))["thinking_language"] == "en"
        # explicit override is carried through
        p2 = _write(tmp_path, "language: vi\nthinking_language: vi\n")
        assert load_output(path=str(p2))["thinking_language"] == "vi"

    def test_english_is_valid(self, tmp_path):
        p = _write(tmp_path, "language: en\nhumanize: false\n")
        cfg = load_output(path=str(p))
        assert cfg["language"] == "en"
        assert cfg["humanize"] is False

    def test_humanize_defaults_false_when_absent(self, tmp_path):
        # Default flipped OFF: humanize is token-spend, only on when a human asks
        # (publish-externally). An absent key means off, on every read path.
        p = _write(tmp_path, "language: vi\n")
        assert load_output(path=str(p))["humanize"] is False

    def test_invalid_language_rejected(self, tmp_path):
        p = _write(tmp_path, "language: fr\n")
        with pytest.raises(OutputConfigError, match="language"):
            load_output(path=str(p))

    def test_non_bool_humanize_rejected(self, tmp_path):
        p = _write(tmp_path, "language: vi\nhumanize: maybe\n")
        with pytest.raises(OutputConfigError, match="humanize"):
            load_output(path=str(p))

    def test_missing_file_is_actionable(self, tmp_path):
        with pytest.raises(OutputConfigError, match="output.yaml|output config"):
            load_output(path=str(tmp_path / "nope.yaml"))

    def test_non_mapping_rejected(self, tmp_path):
        p = _write(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(OutputConfigError, match="mapping"):
            load_output(path=str(p))


class TestAudienceCodeStyleFields:
    """audience (prose-only) + code_style (code-only) ride alongside language /
    humanize. The writer must serialize EVERY set field, not a hardcoded pair —
    otherwise a later `language` write silently drops them (the drop-on-write corruption)."""

    def test_roundtrip_preserves_new_fields_on_language_write(self, tmp_path):
        p = _write(tmp_path, "language: vi\nhumanize: true\n")
        save_output({"audience": 0, "code_style": 5}, path=str(p))
        # a later write that only touches language must NOT erase audience/code_style
        save_output({"language": "en"}, path=str(p))
        cfg = load_output(path=str(p))
        assert cfg["audience"] == 0
        assert cfg["code_style"] == 5
        assert cfg["language"] == "en"

    def test_audience_enum(self, tmp_path):
        p = _write(tmp_path, "language: vi\naudience: 6\n")
        with pytest.raises(OutputConfigError, match="audience"):
            load_output(path=str(p))
        # absent audience is fine (no prose shaping)
        p2 = _write(tmp_path, "language: vi\n")
        assert load_output(path=str(p2))["audience"] is None

    def test_code_style_off_and_range(self, tmp_path):
        p_off = _write(tmp_path, "language: vi\ncode_style: off\n")
        assert load_output(path=str(p_off))["code_style"] is None
        p_ok = _write(tmp_path, "language: vi\ncode_style: 5\n")
        assert load_output(path=str(p_ok))["code_style"] == 5
        p_bad = _write(tmp_path, "language: vi\ncode_style: 6\n")
        with pytest.raises(OutputConfigError, match="code_style"):
            load_output(path=str(p_bad))

    def test_unknown_key_still_rejected(self, tmp_path):
        p = _write(tmp_path, "language: vi\nhumanize: true\n")
        with pytest.raises(OutputConfigError, match="foo|unknown"):
            save_output({"foo": 1}, path=str(p))

    def test_cli_set_audience(self, tmp_path):
        p = _write(tmp_path, "language: vi\nhumanize: true\n")
        rc = main(["--file", str(p), "--set", "audience=0"])
        assert rc == 0
        assert load_output(path=str(p))["audience"] == 0


class TestNonRaisingLoadAndResolve:
    """`load()` is the hook/skill path — fail-OPEN (degrade to default, never
    raise) so a corrupt output.yaml cannot kill a fail-open hook. `load_output`
    stays the gate path — fail-CLOSED (raises). The two must not be merged."""

    def test_load_nonraising_on_missing(self, tmp_path):
        missing = str(tmp_path / "nope.yaml")
        cfg = load(path=missing)  # must NOT raise
        assert cfg["language"] == "vi"
        assert cfg.get("_diag")
        with pytest.raises(OutputConfigError):
            load_output(path=missing)

    def test_load_nonraising_on_malformed(self, tmp_path):
        p = _write(tmp_path, "language: vi\naudience: 99\n")
        cfg = load(path=str(p))  # must NOT raise
        assert cfg["audience"] is None  # bad field degrades to default
        assert cfg.get("_diag")
        with pytest.raises(OutputConfigError, match="audience"):
            load_output(path=str(p))

    def test_resolve_all_merges_both(self, tmp_path):
        op = _write(tmp_path, "language: en\naudience: 3\ncode_style: 2\n")
        merged = resolve_all(output_path=str(op))
        assert merged["audience"] == 3
        assert merged["code_style"] == 2
        assert "voice_level" in merged  # voice axis present too

    def test_resolve_all_never_raises_on_corrupt(self, tmp_path):
        bad = _write(tmp_path, "audience: 99\ncode_style: nonsense\n")
        merged = resolve_all(output_path=str(bad))  # must NOT raise
        assert isinstance(merged, dict)
        assert "voice_level" in merged

    def test_resolve_all_seeds_voice_keys_when_voice_load_raises(self, monkeypatch, tmp_path):
        """The 'never raises' contract must hold even when voice_prefs.load itself
        raises BEFORE its own try (e.g. PyYAML missing -> ImportError on the lazy
        import). The terminal-voice keys stay present (seeded from DEFAULTS) so a
        hard-indexing consumer (voice_inject.build_context) never KeyErrors."""
        import voice_prefs
        op = _write(tmp_path, "language: vi\n")

        def _boom(*a, **k):
            raise ImportError("simulated: PyYAML missing")

        monkeypatch.setattr(voice_prefs, "load", _boom)
        merged = resolve_all(output_path=str(op))
        # every terminal-voice key is present despite the raise
        for key in voice_prefs.DEFAULTS:
            assert key in merged, "voice key %r dropped when voice_prefs.load raised" % key
        assert merged.get("_voice_diag")  # the failure is recorded, not swallowed silently


class TestHumanizeDefaultOff:
    """humanize default flipped True->False across EVERY read/write path. A field
    is only honest when all its paths carry the same default (LESSONS)."""

    def test_humanize_defaults_false_failopen(self, tmp_path):
        import output_config as oc
        p = _write(tmp_path, "language: vi\n")
        assert load(path=str(p))["humanize"] is False
        assert oc._DEFAULTS["humanize"] is False

    def test_humanize_roundtrip_both_values(self, tmp_path):
        p = _write(tmp_path, "language: vi\n")
        save_output({"humanize": True}, path=str(p))
        assert load_output(path=str(p))["humanize"] is True
        save_output({"humanize": False}, path=str(p))
        assert load_output(path=str(p))["humanize"] is False

    def test_shipped_default_humanize_off(self):
        # the tracked harness/data/output.yaml ships humanize OFF
        assert load_output()["humanize"] is False


class TestResolverCLI:
    """`--resolved` prints resolve_all() (honors HARNESS_OUTPUT + HARNESS_TERMINAL_VOICE):
    the ONE source skill/agent reads, and a single-command full-config view."""

    def test_cli_resolved_honors_override(self, tmp_path, capsys, monkeypatch):
        import json as _json
        scratch = _write(tmp_path, "language: vi\naudience: 0\n")
        monkeypatch.setenv("HARNESS_OUTPUT", str(scratch))
        rc = main(["--resolved"])
        assert rc == 0
        out = _json.loads(capsys.readouterr().out)
        assert out["audience"] == 0

    def test_cli_resolved_includes_voice_axes(self, tmp_path, capsys, monkeypatch):
        import json as _json
        scratch = _write(tmp_path, "language: vi\n")
        monkeypatch.setenv("HARNESS_OUTPUT", str(scratch))
        rc = main(["--resolved"])
        assert rc == 0
        out = _json.loads(capsys.readouterr().out)
        assert "voice_level" in out  # resolve_all merges the terminal-voice axes

    def test_cli_default_no_args_ignores_env(self, tmp_path, capsys, monkeypatch):
        # bare CLI (no --resolved) keeps reading the tracked file via load_output,
        # which does NOT honor HARNESS_OUTPUT — the gate-ish path stays env-blind.
        import json as _json
        scratch = _write(tmp_path, "language: en\naudience: 5\n")
        monkeypatch.setenv("HARNESS_OUTPUT", str(scratch))
        rc = main([])
        assert rc == 0
        out = _json.loads(capsys.readouterr().out)
        # reads the shipped tracked file (vi), not the env scratch (en)
        assert out["language"] == "vi"


class TestLanguageConvenience:
    def test_language_returns_string(self, tmp_path):
        p = _write(tmp_path, "language: en\n")
        assert language(path=str(p)) == "en"

    def test_shipped_default_is_vietnamese(self):
        # The tracked harness/data/output.yaml defaults the harness to Vietnamese
        # output — instructions are English, generated prose is Vietnamese.
        assert language() == "vi"
