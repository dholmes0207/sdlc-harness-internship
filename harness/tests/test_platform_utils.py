"""test_platform_utils.py — Windows-support platform utilities (B3).

These utilities are imported by *ported* skill scripts that compute the plugin
root and then ``sys.path.insert(0, <plugin_root> / "scripts")``. After the
plugin collapse there is ONE plugin ``hs``: every skill lives under
``harness/plugins/hs/skills/<skill>/`` and the plugin-shared scripts are
consolidated into ``harness/plugins/hs/scripts/``. So the util must live at
``hs/scripts/<name>.py`` — that is the contract every consumer (now all under
the single ``hs`` plugin) resolves to. These tests prove the *wiring* (a
consumer can import it), not just that a file exists.

- resolve_env.py  -> consumed by ai-multimodal (ai group, 4 scripts)
- win_compat.py   -> consumed by devops (devops group) + databases (stack group)
- encoding_utils.py -> ALREADY canonical in harness/scripts (must NOT be re-added)
"""
import importlib
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PLUGINS = _REPO / "harness" / "plugins"
# Post-collapse: all plugin-shared scripts consolidated under the single hs plugin.
# Every consumer group (ai / devops / stack) now resolves to this one scripts dir.
_HS_SCRIPTS = _PLUGINS / "hs" / "scripts"
_HS_AI_SCRIPTS = _HS_SCRIPTS
_HS_DEVOPS_SCRIPTS = _HS_SCRIPTS
_HS_STACK_SCRIPTS = _HS_SCRIPTS


def _import_from(dirpath, modname):
    """Import a module from a specific dir, isolating it from sys.modules cache."""
    sys.modules.pop(modname, None)
    sys.path.insert(0, str(dirpath))
    try:
        mod = importlib.import_module(modname)
        return importlib.reload(mod)
    finally:
        try:
            sys.path.remove(str(dirpath))
        except ValueError:
            pass


# ---- resolve_env (ai group, ai-multimodal) ----------------------------------

def test_resolve_env_file_at_consumer_path():
    # the 4 ai-multimodal scripts insert the hs plugin's scripts dir onto sys.path
    assert (_HS_AI_SCRIPTS / "resolve_env.py").is_file(), \
        "resolve_env.py must live where ai-multimodal consumers look: hs/scripts/"


def test_resolve_env_process_env_wins(monkeypatch):
    monkeypatch.setenv("HS_PLATFORM_TEST_KEY", "from-process-env")
    mod = _import_from(_HS_AI_SCRIPTS, "resolve_env")
    assert mod.resolve_env("HS_PLATFORM_TEST_KEY") == "from-process-env"


def test_resolve_env_default_when_absent(monkeypatch):
    monkeypatch.delenv("HS_DEFINITELY_MISSING_XYZ", raising=False)
    mod = _import_from(_HS_AI_SCRIPTS, "resolve_env")
    assert mod.resolve_env("HS_DEFINITELY_MISSING_XYZ", default="fallback") == "fallback"


# ---- win_compat (devops + stack groups) -------------------------------------


def test_win_compat_no_claudekit_brand():
    text = (_HS_DEVOPS_SCRIPTS / "win_compat.py").read_text(encoding="utf-8")
    assert "ClaudeKit" not in text, "ck-port must drop the ClaudeKit brand"


# ---- encoding_utils (already canonical — guard against re-add) --------------

def test_encoding_utils_stays_canonical_in_harness_scripts():
    canon = _REPO / "harness" / "scripts" / "encoding_utils.py"
    assert canon.is_file()
    body = canon.read_text(encoding="utf-8")
    # the richer HS version (configure_utf8_console + emit_json) must remain
    assert "def configure_utf8_console" in body
    assert "def emit_json" in body, "must not be clobbered by the leaner CK copy"
