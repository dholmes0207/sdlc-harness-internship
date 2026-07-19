"""test_plugin_manifest_complete.py — the hs plugin manifest carries full metadata.

`claude plugin validate` only warns (never fails, non---strict) when author/license/
etc are missing from plugin.json — silent metadata rot. This test pins the fields
the plan decided on (author, license, keywords) plus the description/marketplace
consistency the batch-3 audit flagged as drifted (Vietnamese plugin.json vs English
marketplace.json, two different texts for one product). Descriptions must be
English (repo convention: instruction files stay English) and must match verbatim
across plugin.json and marketplace.json so the two labels cannot drift again.
"""
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_JSON = _REPO_ROOT / "harness" / "plugins" / "hs" / ".claude-plugin" / "plugin.json"
_MARKETPLACE_JSON = _REPO_ROOT / "harness" / "plugins" / ".claude-plugin" / "marketplace.json"

_EXPECTED_KEYWORDS = [
    "sdlc-harness",
    "claude-code",
    "tdd",
    "orchestration",
    "code-review",
    "agents",
    "hooks",
    "skills",
]


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


# Vietnamese diacritic range (Latin Extended-A/B + combining marks used by the
# Vietnamese alphabet); an em-dash or curly quote is common in English prose too,
# so a bare "non-ASCII" check would false-positive on those. Flag only the
# character range that actually signals Vietnamese text.
_VIETNAMESE_DIACRITICS = re.compile(
    "[À-ỹ̀-ͯ]"
)


def _is_english_ascii(text):
    return not _VIETNAMESE_DIACRITICS.search(text)


def test_plugin_json_author_license_keywords():
    data = _load(_PLUGIN_JSON)
    author = data.get("author")
    assert author == {"name": "Lucas Bui"} or author == "Lucas Bui", (
        "plugin.json author must be Lucas Bui (decided in DECISIONS.md P3)"
    )
    assert data.get("license") == "AGPL-3.0"
    assert data.get("keywords") == _EXPECTED_KEYWORDS


def test_plugin_json_description_is_english_and_nonempty():
    data = _load(_PLUGIN_JSON)
    desc = data.get("description", "")
    assert desc, "plugin.json description must not be empty"
    assert _is_english_ascii(desc), (
        "plugin.json description must be English (repo convention); "
        "got non-ASCII characters, likely leftover Vietnamese text"
    )


def test_plugin_json_description_matches_marketplace_entry():
    plugin_data = _load(_PLUGIN_JSON)
    marketplace_data = _load(_MARKETPLACE_JSON)
    entries = [p for p in marketplace_data.get("plugins", []) if p.get("name") == "hs"]
    assert entries, "marketplace.json must declare an 'hs' plugin entry"
    assert plugin_data.get("description") == entries[0].get("description"), (
        "plugin.json and marketplace.json descriptions for hs must be one "
        "identical source text, not two drifted labels"
    )


def test_marketplace_json_has_own_description():
    data = _load(_MARKETPLACE_JSON)
    assert data.get("description"), (
        "marketplace.json top-level description missing — "
        "claude plugin validate warns on this"
    )


def test_plugin_json_has_displayname():
    data = _load(_PLUGIN_JSON)
    assert data.get("displayName"), "plugin.json should carry a displayName"
