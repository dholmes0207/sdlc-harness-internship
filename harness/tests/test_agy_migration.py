"""test_agy_migration.py — dual LLM-CLI contract: gemini (API-key) primary, agy fallback.

Correction of an earlier over-broad migration. Google did NOT retire the `gemini`
binary on 2026-06-18 — it discontinued the *consumer OAuth tiers* (free / AI Pro /
Ultra). `gemini` CLI authenticated with `GEMINI_API_KEY` (AI Studio / Code Assist)
is *completely unaffected* and runs headless. `agy` (Antigravity) is OAuth-primary
and its headless API-key path is not usable, so it can only be a secondary fallback.

The harness therefore runs a DUAL contract on its LLM-driven CLI surface:

  1. gemini + `GEMINI_API_KEY` is the PRIMARY headless CLI (present in each surface);
  2. agy survives as a SECONDARY fallback (still present, never the sole path);
  3. a deterministic tertiary fallback stays documented (cli.ts / internal / genai);
  4. the false "Google retired the gemini binary" framing is gone from the surface;
  5. the prepended MCP proxy contract reference exists (CLI-agnostic);
  6. over-reach guard: a live genai-API consumer (gemini_batch_process.py) STILL
     imports the API — a blind swap of API usage fails here.
"""
import re
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_SKILLS = _REPO / "harness" / "plugins" / "hs" / "skills"

# The three LLM-driven CLI surfaces that must carry the dual contract.
_USE_MCP = _SKILLS / "use-mcp" / "SKILL.md"
_SCOUT_EXT = _SKILLS / "scout" / "references" / "external-scouting.md"
_AI_MM = _SKILLS / "ai-multimodal" / "SKILL.md"
_CLI_DOC = _SKILLS / "use-mcp" / "references" / "llm-cli-integration.md"

_SURFACES = [_USE_MCP, _SCOUT_EXT, _AI_MM]

# A gemini-CLI *invocation* form (not a model id / env / path).
_GEMINI_INVOCATION = re.compile(r"gemini\s+-y\b")
# An agy invocation form.
_AGY_INVOCATION = re.compile(r"agy\s+--dangerously-skip-permissions\b")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_gemini_cli_primary_present_on_every_surface():
    missing = [str(f.relative_to(_REPO)) for f in _SURFACES
               if not _GEMINI_INVOCATION.search(_read(f))]
    assert not missing, "gemini CLI (primary headless path) missing on: " + ", ".join(missing)


def test_api_key_auth_documented():
    # The headless auth path is GEMINI_API_KEY — it must be named where gemini is primary.
    for f in (_USE_MCP, _AI_MM, _CLI_DOC):
        assert "GEMINI_API_KEY" in _read(f), f"GEMINI_API_KEY auth note missing in {f.name}"


def test_agy_survives_as_fallback():
    # agy stays as a secondary fallback on the CLI surfaces — never removed outright.
    missing = [str(f.relative_to(_REPO)) for f in (_USE_MCP, _SCOUT_EXT)
               if not _AGY_INVOCATION.search(_read(f))]
    assert not missing, "agy fallback dropped from: " + ", ".join(missing)


def test_false_retirement_framing_removed():
    # The inaccurate "Google retired the gemini binary" claim must not survive.
    bad = re.compile(r"(retired|killed)\s+the\s+`?gemini`?\s+(binary|cli)", re.I)
    offenders = [str(f.relative_to(_REPO)) for f in (_SURFACES + [_CLI_DOC])
                 if bad.search(_read(f))]
    assert not offenders, "false gemini-retirement framing survives in: " + ", ".join(offenders)


@pytest.mark.dev_repo
def test_cli_reference_renamed_neutral():
    old_agy = _SKILLS / "use-mcp" / "references" / "agy-cli-integration.md"
    old_gem = _SKILLS / "use-mcp" / "references" / "gemini-cli-integration.md"
    assert _CLI_DOC.exists(), "llm-cli-integration.md missing"
    assert not old_agy.exists(), "agy-cli-integration.md must be renamed to the neutral doc"
    assert not old_gem.exists(), "old gemini-cli-integration.md must not exist"
    body = _read(_CLI_DOC)
    assert "gemini" in body and "agy" in body, "neutral CLI doc must cover both gemini and agy"


@pytest.mark.dev_repo
def test_mcp_proxy_contract_present():
    contract = _SKILLS / "use-mcp" / "references" / "mcp-proxy-contract.md"
    assert contract.exists(), "mcp-proxy-contract.md missing"
    assert "BEGIN CONTRACT" in _read(contract)


@pytest.mark.dev_repo
def test_live_genai_api_untouched_overreach_guard():
    # The discrimination safety net: a genai-API consumer keeps its API import.
    batch = _SKILLS / "ai-multimodal" / "scripts" / "gemini_batch_process.py"
    assert "from google import genai" in _read(batch)
