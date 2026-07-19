"""Phase 6 glue: setup surface + install default-off + registration + docs/DEC/glossary.

The lane is a 5-axis config that must be onboardable via hs:setup and ship default-off
via install; the relayer agent + loop reference must ship; the new terms must be in
the glossary SSOT; and the DEC must be recorded EXTENDS DEC-219/220.
"""
import re
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_ROOT = _HARNESS.parent
_SETUP = _HARNESS / "plugins" / "hs" / "skills" / "setup" / "SKILL.md"
_CONFIG_REF = _ROOT / "docs" / "harness" / "config-reference.md"
_RELAYER = _HARNESS / "plugins" / "hs" / "agents" / "gemini-relayer.md"
_SHIPPED_CFG = _HARNESS / "data" / "gemini-partner.yaml"
_GLOSSARY = _ROOT / "docs" / "glossary.yaml"
_DECISIONS = _ROOT / "docs" / "decisions.yaml"


def test_setup_surfaces_gemini_lane():
    text = _SETUP.read_text(encoding="utf-8").lower()
    assert "gemini" in text and "partner lane" in text
    # the 5 axes + loop + injectable bootstrap are surfaced
    for token in ("master", "route_all_injection", "loop", "injectable"):
        assert token in text, "setup does not surface %r" % token


def test_install_lane_default_off():
    import sys
    sys.path.insert(0, str(_HARNESS / "scripts"))
    import gemini_partner_config as gpc
    cfg = gpc.resolve(_SHIPPED_CFG)   # coerces YAML bool-fold back to tokens
    assert cfg["master"] == "off"          # ships inert
    assert cfg["route_all_injection"] == "off"


def test_agent_ships_present():
    assert _RELAYER.is_file()
    assert "model: haiku" in _RELAYER.read_text(encoding="utf-8")


def test_config_reference_indexes_route_all_injection():
    text = _CONFIG_REF.read_text(encoding="utf-8")
    assert "route_all_injection" in text
    assert "gemini-partner" in text
    # env-bound → restart is noted somewhere in the lane's config-reference entry
    assert "restart" in text.lower()


def test_glossary_terms_registered():
    text = _GLOSSARY.read_text(encoding="utf-8")
    for term in ("injectable", "gemini-relayer", "route_all_injection",
                 "skill-injection-passthrough"):
        assert term in text, "glossary missing term %r" % term


def test_dec_extends_recorded():
    text = _DECISIONS.read_text(encoding="utf-8")
    # DEC-221 records the mirror lane and expresses EXTENDS DEC-219/220
    assert "DEC-221" in text
    m = re.search(r"DEC-221.*?(?=\n- id:|\Z)", text, re.DOTALL)
    assert m and "219" in m.group(0) and "220" in m.group(0)
