"""discover_providers/validate_provider/ccs_available (read-only ccs preflight).

`ccs api list` prints a human ANSI table (probed live) — never parsed.
Discovery instead reads the AUTHORITATIVE profile list from ccs's unified
config (<ccs-home>/config.yaml `profiles:` keys), the same names a
`ccs <name>` call resolves — so every real profile (shared/local included)
is discovered and nothing is excluded by a hardcoded guess. When that config
is absent (older ccs), it falls back to globbing *.settings.json basenames.
Neither the config's other blocks nor a settings file's contents are ever
emitted — only the profile NAMES are read.
"""
import sys
from pathlib import Path

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_preflight as pf  # noqa: E402


def test_missing_ccs_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HARNESS_CCS_CMD", str(tmp_path / "does-not-exist-binary"))
    assert pf.ccs_available() is False

    rc = pf.main(["--check"])
    assert rc != 0
    err = capsys.readouterr().err.lower()
    assert "install" in err


def test_provider_not_in_list_refused(monkeypatch):
    monkeypatch.setattr(pf, "discover_providers", lambda: ["foo", "bar"])
    assert pf.validate_provider("bogus") is False
    assert pf.validate_provider("foo") is True


def test_discover_from_config_yaml_is_authoritative(monkeypatch, tmp_path):
    # The unified config's `profiles:` keys are the source of truth — every
    # listed profile is a real `ccs <name>` backend, shared/local included.
    # It wins over the *.settings.json glob (which may lag or hold stray files).
    import yaml
    ccs_home = tmp_path / ".ccs"
    ccs_home.mkdir()
    (ccs_home / "config.yaml").write_text(
        yaml.safe_dump({"version": 1,
                        "profiles": {"shared": {}, "local": {}, "minimax": {}}}),
        encoding="utf-8")
    # a stray settings file NOT in the config must not leak in
    (ccs_home / "orphan.settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HARNESS_CCS_HOME", str(ccs_home))

    assert pf.discover_providers() == ["local", "minimax", "shared"]


def test_discover_glob_fallback_keeps_shared_local(monkeypatch, tmp_path):
    # No config.yaml (older ccs) → fall back to the settings-file glob, and
    # shared/local are VALID profiles here too — nothing is excluded by a
    # hardcoded meta-profile guess.
    ccs_home = tmp_path / ".ccs"
    ccs_home.mkdir()
    (ccs_home / "foo.settings.json").write_text("{}", encoding="utf-8")
    (ccs_home / "shared.settings.json").write_text("{}", encoding="utf-8")
    (ccs_home / "local.settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HARNESS_CCS_HOME", str(ccs_home))

    assert pf.discover_providers() == ["foo", "local", "shared"]


def test_discover_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_CCS_HOME", str(tmp_path / "does-not-exist-dir"))
    assert pf.discover_providers() == []


def test_discover_filters_flag_like_names(monkeypatch, tmp_path):
    # A basename starting with "-" would be read as a CLI flag by
    # `ccs <name> -p ...`, not a positional provider argument — never
    # surface it as callable.
    ccs_home = tmp_path / ".ccs"
    ccs_home.mkdir()
    (ccs_home / "foo.settings.json").write_text("{}", encoding="utf-8")
    (ccs_home / "-x.settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HARNESS_CCS_HOME", str(ccs_home))

    assert pf.discover_providers() == ["foo"]


def test_discover_fail_open_on_unresolvable_home(monkeypatch):
    # Path.home() raises RuntimeError (not OSError) when HOME cannot be
    # resolved — discovery must still fail-open to [], not crash.
    monkeypatch.delenv("HARNESS_CCS_HOME", raising=False)

    def _raise_runtime_error():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(pf.Path, "home", staticmethod(_raise_runtime_error))
    assert pf.discover_providers() == []
