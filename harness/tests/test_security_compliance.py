"""test_security_compliance.py — enterprise compliance enrichment of security-scan.

The harness targets enterprise SDLC, so the security scan gains (1) a compliance
reference mapping SOC2 / GDPR / PCI-DSS / HIPAA to the STRIDE/OWASP controls that
evidence them, surfaced as a scan trigger in the SKILL, and (2) the finer STRIDE
checklist items the threat model previously omitted (MFA, request signing, log
retention, connection-pool hygiene, dead-letter queues, escalation re-auth). All
reference content stays brand-clean and within the load-on-demand size budget.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _ROOT / "harness" / "plugins" / "hs" / "skills" / "security-scan"
_SKILL = _SKILL_DIR / "SKILL.md"
_COMPLIANCE = _SKILL_DIR / "references" / "compliance-frameworks.md"
_THREAT = _SKILL_DIR / "references" / "threat-model.md"

# assembled so this test file itself never trips the ownership-boundary invariant
_BANNED = re.compile(r"/ck:|" + r"\.claude/" + r"(?:skills|hooks)/"
                     + r"|ClaudeKit|claudekit", re.IGNORECASE)


def _read(p):
    return p.read_text(encoding="utf-8")


class TestComplianceReference:
    def test_reference_exists_and_is_brand_clean(self):
        assert _COMPLIANCE.is_file(), "missing compliance reference: %s" % _COMPLIANCE
        assert not _BANNED.search(_read(_COMPLIANCE)), "brand leak in compliance ref"

    def test_covers_the_four_frameworks(self):
        body = _read(_COMPLIANCE)
        for fw in ("SOC2", "GDPR", "PCI-DSS", "HIPAA"):
            assert fw in body, "compliance ref missing %s" % fw


    def test_within_size_budget(self):
        assert len(_read(_COMPLIANCE).splitlines()) <= 300


class TestSkillSurfacesCompliance:
    def test_skill_lists_compliance_as_a_trigger(self):
        body = _read(_SKILL).lower()
        assert "compliance" in body
        # and routes to the new drawer
        assert "compliance-frameworks.md" in _read(_SKILL)


class TestThreatModelGranularity:
    def test_added_finer_stride_items(self):
        body = _read(_THREAT).lower()
        # the 7 items CK had and HS lacked — stable keyword anchors
        for needle in ("mfa", "hmac", "dead-letter", "re-auth",
                       "connection pool", "90 days"):
            assert needle in body, "threat model missing finer STRIDE item: %s" % needle

    def test_threat_model_within_size_budget(self):
        assert len(_read(_THREAT).splitlines()) <= 300
