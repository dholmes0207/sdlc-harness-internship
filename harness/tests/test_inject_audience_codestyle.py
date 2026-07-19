"""test_inject_audience_codestyle.py — inject wiring for audience (CHAT) + code_style.

Reversed from the prior skill-read-only design: audience now drives the terminal
register too. Verifies:
- audience IS injected (reader-register block) when set, absent when off
- code_style block is CODE-ONLY (no "prose AND code" — audience owns prose)
- humanize directive injected only when humanize is true
- _context_sig includes audience + humanize (a toggle forces a re-inject)
- every terminal/output knob in the sig is no-orphan
- build_context never raises on a corrupt output.yaml (fail-open)

All knob-driven tests drive through scratch files + HARNESS_OUTPUT /
HARNESS_TERMINAL_VOICE and call build_context(output_config.resolve_all()) — the
real production source — so a dead knob cannot pass by hand-injection.
"""

import contextlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HOOKS = Path(__file__).resolve().parents[1] / "hooks"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HOOKS))


def _write_output_yaml(path: Path, *, audience=None, code_style=None,
                       language="vi", humanize=None):
    lines = ["language: %s" % language]
    if humanize is not None:
        lines.append("humanize: %s" % ("true" if humanize else "false"))
    if audience is not None:
        lines.append("audience: %d" % audience)
    if code_style is not None:
        lines.append("code_style: %d" % code_style)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_voice_yaml(path: Path, **kwargs):
    lines = []
    defaults = {
        "voice_level": 5,
        "persona": "none",
        "terminal_voice_level": 3,
        "no_markdown": False,
        "interview_rigor": "standard",
        "action_prompting": "standard",
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        if isinstance(v, bool):
            lines.append("%s: %s" % (k, "true" if v else "false"))
        elif isinstance(v, str):
            lines.append("%s: %s" % (k, v))
        else:
            lines.append("%s: %d" % (k, v))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@contextlib.contextmanager
def _env(**pairs):
    """Temporarily set env vars, restoring prior values on exit."""
    saved = {k: os.environ.get(k) for k in pairs}
    try:
        for k, v in pairs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ctx_from_scratch(d, *, audience=None, code_style=None, humanize=None):
    """Write scratch output+voice yaml, point the env seams at them, and build
    the context off the REAL resolver (resolve_all) — no hand-injected knobs."""
    import output_config as oc
    import voice_inject as vi

    out_yaml = Path(d) / "output.yaml"
    tv_yaml = Path(d) / "terminal-voice.yaml"
    _write_output_yaml(out_yaml, audience=audience, code_style=code_style,
                       humanize=humanize)
    _write_voice_yaml(tv_yaml)
    with _env(HARNESS_OUTPUT=str(out_yaml), HARNESS_TERMINAL_VOICE=str(tv_yaml)):
        return vi.build_context(oc.resolve_all())


def test_audience_injected_when_set():
    """audience set in output.yaml MUST surface a reader-register block, with an
    evidence-invariant note."""
    with tempfile.TemporaryDirectory() as d:
        ctx = _ctx_from_scratch(d, audience=0)
        assert "Reader register" in ctx, (
            "audience=0 set but no reader-register block injected.\nContext: %s" % ctx)
        assert "audience=0" in ctx, "reader-register block missing audience=0 marker"
        low = ctx.lower()
        assert "invariant" in low or "evidence" in low, (
            "reader-register block missing the evidence-invariant note.\nContext: %s" % ctx)


def test_audience_absent_when_off():
    """audience absent -> no reader-register block."""
    with tempfile.TemporaryDirectory() as d:
        ctx = _ctx_from_scratch(d, audience=None, code_style=2)
        assert "Reader register" not in ctx, (
            "audience off but reader-register block still injected.\nContext: %s" % ctx)
        assert "audience=" not in ctx


def test_context_sig_tracks_legacy_output_style_shim():
    """A legacy `output_style` in terminal-voice.yaml is shimmed to code_style by
    resolve_all and DOES change the injected text (a code_style block appears).
    The sig must track that — it is built from resolve_all (not load), so toggling
    output_style on an old install forces a re-inject instead of waiting out the
    turn throttle. Regression for the sig/text divergence on the shim path."""
    import inject_prompt_context as ipc

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        tv_yaml = root / "terminal-voice.yaml"
        out_yaml = root / "output.yaml"
        _write_output_yaml(out_yaml, audience=None, code_style=None)  # no code_style
        with _env(HARNESS_TERMINAL_VOICE=str(tv_yaml), HARNESS_OUTPUT=str(out_yaml)):
            # State A: legacy output_style present -> shimmed -> code_style block in text
            tv_yaml.write_text(
                "voice_level: 5\npersona: none\nterminal_voice_level: 3\n"
                "no_markdown: false\ninterview_rigor: standard\naction_prompting: standard\n"
                "output_style: 2\n", encoding="utf-8")
            sig_with = ipc._context_sig(root)
            # State B: remove output_style -> no block
            _write_voice_yaml(tv_yaml)
            sig_without = ipc._context_sig(root)
        assert sig_with != sig_without, (
            "Toggling legacy output_style did not change the sig — the shim path "
            "diverges from the injected text and the re-inject throttle silently fails.\n"
            "with=%r\nwithout=%r" % (sig_with, sig_without))


def test_context_sig_includes_audience():
    """Changing audience 0->5 MUST change _context_sig (audience is now injected)."""
    import inject_prompt_context as ipc

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        tv_yaml = root / "terminal-voice.yaml"
        out_yaml = root / "output.yaml"
        _write_voice_yaml(tv_yaml)
        with _env(HARNESS_TERMINAL_VOICE=str(tv_yaml), HARNESS_OUTPUT=str(out_yaml)):
            _write_output_yaml(out_yaml, code_style=3, audience=0)
            sig_aud0 = ipc._context_sig(root)
            _write_output_yaml(out_yaml, code_style=3, audience=5)
            sig_aud5 = ipc._context_sig(root)
        assert sig_aud0 != sig_aud5, (
            "Changing audience did not change _context_sig — audience must be in sig.\n"
            "sig_aud0=%r\nsig_aud5=%r" % (sig_aud0, sig_aud5))


def test_code_style_is_code_only():
    """The code_style block must NOT say "prose AND code"; it shapes CODE only."""
    with tempfile.TemporaryDirectory() as d:
        ctx = _ctx_from_scratch(d, code_style=3)
        assert "[Code style" in ctx, "code_style=3 set but no code-style block"
        assert "prose AND code" not in ctx, (
            "code_style block still claims to shape prose — it must be code-only.\n"
            "Context: %s" % ctx)
        low = ctx.lower()
        assert any(tok in low for tok in ("comment", "verbosity", "code")), (
            "code_style block missing a code-shaping keyword.\nContext: %s" % ctx)


def test_humanize_directive_when_true():
    """The humanize directive tracks the RESOLVED bool: explicit true -> directive
    line, explicit false -> none. (The absent-default value is P2's concern —
    default humanize off — so this phase asserts only the explicit booleans.)"""
    with tempfile.TemporaryDirectory() as d:
        ctx_on = _ctx_from_scratch(d, humanize=True)
        assert "humanize=on" in ctx_on.lower(), (
            "humanize=true but no humanize directive injected.\nContext: %s" % ctx_on)
    with tempfile.TemporaryDirectory() as d:
        ctx_off = _ctx_from_scratch(d, humanize=False)
        assert "humanize=on" not in ctx_off.lower(), (
            "humanize=false still injected a humanize directive.\nContext: %s" % ctx_off)


def test_context_sig_includes_humanize():
    """Toggling humanize MUST change _context_sig (it changes the injected text)."""
    import inject_prompt_context as ipc

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        tv_yaml = root / "terminal-voice.yaml"
        out_yaml = root / "output.yaml"
        _write_voice_yaml(tv_yaml)
        with _env(HARNESS_TERMINAL_VOICE=str(tv_yaml), HARNESS_OUTPUT=str(out_yaml)):
            _write_output_yaml(out_yaml, humanize=False)
            sig_off = ipc._context_sig(root)
            _write_output_yaml(out_yaml, humanize=True)
            sig_on = ipc._context_sig(root)
        assert sig_off != sig_on, (
            "Toggling humanize did not change _context_sig.\n"
            "off=%r\non=%r" % (sig_off, sig_on))


def test_context_sig_drops_detail_level_covers_all_terminal():
    """_context_sig must NOT include detail_level. Toggling each inject-domain knob
    (the 6 terminal knobs + code_style + audience + humanize) must change the sig."""
    import inject_prompt_context as ipc

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        tv_yaml = root / "terminal-voice.yaml"
        out_yaml = root / "output.yaml"

        def base():
            _write_voice_yaml(tv_yaml, voice_level=5, persona="none",
                              terminal_voice_level=3, no_markdown=False,
                              interview_rigor="standard", action_prompting="standard")
            _write_output_yaml(out_yaml, code_style=2, audience=2, humanize=False)

        with _env(HARNESS_TERMINAL_VOICE=str(tv_yaml), HARNESS_OUTPUT=str(out_yaml)):
            base()
            base_sig = ipc._context_sig(root)
            assert "detail_level" not in base_sig, (
                "detail_level still appears in _context_sig.\nsig=%r" % base_sig)

            toggles = [
                ("voice_level", lambda: _write_voice_yaml(tv_yaml, voice_level=7, persona="none",
                  terminal_voice_level=3, no_markdown=False, interview_rigor="standard", action_prompting="standard")),
                ("persona", lambda: _write_voice_yaml(tv_yaml, voice_level=5, persona="feynman",
                  terminal_voice_level=3, no_markdown=False, interview_rigor="standard", action_prompting="standard")),
                ("terminal_voice_level", lambda: _write_voice_yaml(tv_yaml, voice_level=5, persona="none",
                  terminal_voice_level=1, no_markdown=False, interview_rigor="standard", action_prompting="standard")),
                ("no_markdown", lambda: _write_voice_yaml(tv_yaml, voice_level=5, persona="none",
                  terminal_voice_level=3, no_markdown=True, interview_rigor="standard", action_prompting="standard")),
                ("interview_rigor", lambda: _write_voice_yaml(tv_yaml, voice_level=5, persona="none",
                  terminal_voice_level=3, no_markdown=False, interview_rigor="deep", action_prompting="standard")),
                ("action_prompting", lambda: _write_voice_yaml(tv_yaml, voice_level=5, persona="none",
                  terminal_voice_level=3, no_markdown=False, interview_rigor="standard", action_prompting="proactive")),
                ("code_style", lambda: _write_output_yaml(out_yaml, code_style=5, audience=2, humanize=False)),
                ("audience", lambda: _write_output_yaml(out_yaml, code_style=2, audience=5, humanize=False)),
                ("humanize", lambda: _write_output_yaml(out_yaml, code_style=2, audience=2, humanize=True)),
            ]
            for knob_name, mutate in toggles:
                base()
                before_sig = ipc._context_sig(root)
                mutate()
                after_sig = ipc._context_sig(root)
                assert before_sig != after_sig, (
                    "Toggling %r did not change _context_sig — orphaned from sig.\n"
                    "before=%r\nafter=%r" % (knob_name, before_sig, after_sig))


def test_voice_inject_never_raises_on_corrupt_output_yaml():
    """A corrupt output.yaml must not cause build_context to raise. The resolver
    (resolve_all) is fail-open; audience/code_style/humanize degrade to safe
    defaults but the context still builds."""
    import output_config as oc
    import voice_inject as vi

    with tempfile.TemporaryDirectory() as d:
        tv_yaml = Path(d) / "terminal-voice.yaml"
        out_yaml = Path(d) / "output.yaml"
        _write_voice_yaml(tv_yaml)
        out_yaml.write_text("not: valid: yaml: :\n!!!!\n", encoding="utf-8")
        with _env(HARNESS_OUTPUT=str(out_yaml), HARNESS_TERMINAL_VOICE=str(tv_yaml)):
            try:
                ctx = vi.build_context(oc.resolve_all())
            except Exception as e:  # noqa: BLE001
                pytest.fail("build_context raised on corrupt output.yaml: %s" % e)
        assert isinstance(ctx, str) and len(ctx) > 0
        # corrupt output.yaml -> no audience/code_style block, but voice still there
        assert "Reader register" not in ctx
        assert "Terminal voice" in ctx
