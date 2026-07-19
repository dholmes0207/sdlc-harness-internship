"""Version-parity guard: the plugin manifests must not drift from the release identity.

The release version of record lives in harness/release.json (harness_version). The
two plugin-marketplace manifests carry their own version string, and a release cut
that forgets to update them lets the published plugin advertise a stale version.
This test makes that drift fail loudly (locally + CI + pre-push) instead of shipping.

Ride-along: release.py syncs all three on a cut; this asserts the invariant holds.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_JSON = ROOT / "harness" / "release.json"
PLUGIN_JSON = ROOT / "harness" / "plugins" / "hs" / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / "harness" / "plugins" / ".claude-plugin" / "marketplace.json"


def _release_version() -> str:
    return json.loads(RELEASE_JSON.read_text(encoding="utf-8"))["harness_version"]


def _plugin_version() -> str:
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]


def _marketplace_hs_version() -> str:
    data = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    hs = [p for p in data["plugins"] if p["name"] == "hs"]
    assert hs, "no 'hs' plugin entry in marketplace.json"
    return hs[0]["version"]


def test_plugin_json_matches_release_version():
    assert _plugin_version() == _release_version(), (
        "plugin.json version %s != release.json harness_version %s "
        "(release.py cut must sync plugin.json)"
        % (_plugin_version(), _release_version())
    )


def test_marketplace_json_matches_release_version():
    assert _marketplace_hs_version() == _release_version(), (
        "marketplace.json hs version %s != release.json harness_version %s "
        "(release.py cut must sync marketplace.json)"
        % (_marketplace_hs_version(), _release_version())
    )
