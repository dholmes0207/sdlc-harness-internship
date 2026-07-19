"""Shared test scaffolding for the spec-graph / fence / memory-gap suites.

Plain helpers (NOT fixtures) so call semantics stay identical to the source
suite this scaffolding mirrors. The valid-spec fixture tree is built
PROGRAMMATICALLY into a temp dir at import instead of being committed as
files: the repo only carries markdown under plans/ and docs/, so test data
lives in code and materializes per run.

  - ``VALID``     — the read-only valid-spec tree every ``make_proj`` copies.
  - ``make_proj`` — writable copy of the fixture, optional git-init baseline.
  - ``append_to`` — append a line to a docs/product artifact.
"""

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# The dev who configured THIS repo runs with HARNESS_* posture overrides exported
# (terminal voice / guard / stage / agent-permissions point at dev files, injected
# via the .claude/settings.json `env` block). Those must NOT bleed into the suite:
# every test asserts SHIPPED-default behavior unless it sets its own override via
# monkeypatch. Scrub posture pointers once per test so a configured dev session
# and a clean CI run see identical defaults. Mirrors the pre-push hook's HARNESS_*
# scrub — posture input is the tracked/default file, never the ambient env. A test
# that needs an override re-sets it with monkeypatch (runs after this autouse
# fixture, so it wins).
#
# The keep-set is the PLUMBING allowlist (identity + state/log placement seeds
# tests set themselves), mirroring session_init._BASELINE_ENV. Everything else
# under HARNESS_* is posture and is scrubbed. Allowlisting (not a fixed posture
# list) is staleness-proof: a new posture knob — e.g. HARNESS_AGENT_PERMISSIONS_-
# OVERLAY — is scrubbed automatically, so a configured dev shell never re-reds the
# suite each time a knob is added.
_PLUMBING_ENV = frozenset({
    "HARNESS_USER", "HARNESS_AGENT", "HARNESS_STATE_DIR",
    "HARNESS_HOOK_LOG_DIR", "HARNESS_ROOT",
})


@pytest.fixture(autouse=True)
def _scrub_dev_posture_env(monkeypatch, request):
    for name in [k for k in os.environ
                 if k.startswith("HARNESS_") and k not in _PLUMBING_ENV]:
        monkeypatch.delenv(name, raising=False)
    # Hermetic actor: resolve_actor returns "ci" whenever a CI marker is set
    # (hook_runtime), so the suite must run as if local — otherwise every test that
    # asserts a user actor reds ON the CI runner. A test that specifically wants CI
    # behaviour re-sets the marker with monkeypatch (runs after this autouse fixture).
    for name in ("CI", "GITLAB_CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    # Hermetic engine detection: the gemini lane's `engine: auto` resolves from
    # GEMINI_API_KEY presence (key -> gemini-print, none -> agy-print). Pin a DUMMY
    # key so `auto` deterministically picks gemini-print on ANY machine, and pin the
    # gemini print transport to the FAKE via HARNESS_GEMINI_PRINT_CMD so the route
    # never spawns a real `gemini -p` (nor, on a keyless default, a real `agy`). This
    # is the single global seam that keeps every test that drives the gemini engine
    # off the wire — a test wanting gemini DOWN re-points the seam at an exit-1 fake,
    # a test wanting keyless/agy delenv-s the key; both run AFTER this autouse fixture
    # so they win.
    #
    # EXCEPT the live lanes: a @real_gemini / @real_agy test opts into a REAL engine
    # spawn and self-skips when the real credential is absent. Clobbering the ambient
    # key with a dummy would make @real_gemini run against an invalid key instead of
    # skipping, and pinning a gemini key would derail @real_agy's OAuth path. Leave
    # their ambient env untouched.
    live = (request.node.get_closest_marker("real_gemini")
            or request.node.get_closest_marker("real_agy"))
    if not live:
        monkeypatch.setenv("GEMINI_API_KEY", "test-dummy-key-not-real")
        fake_print = (Path(__file__).resolve().parent / "fixtures"
                      / "fake_gemini_print.py")
        monkeypatch.setenv("HARNESS_GEMINI_PRINT_CMD",
                           "%s %s" % (sys.executable, fake_print))


@pytest.fixture(autouse=True)
def _reset_hook_runtime_caches():
    """hook_runtime memoizes the parsed hooks config (and the nudge-channel map) in a
    module-level cache. A test that points HARNESS_HOOK_CONFIG at a scratch yaml and reads a
    hook's state populates that cache from the scratch; monkeypatch restores the env at
    teardown but NOT the cache, so a later test asserting a SHIPPED hook default (e.g.
    goal_cycle_nudge enabled) reads the stale scratch config and reds — order-dependent under
    xdist loadfile packing. Clear the caches around every test so a config read always
    re-derives from the current (scrubbed) env. One place beats chasing each contaminator's
    teardown, and a new config-reading test is immunized automatically."""
    hooks_dir = str(Path(__file__).resolve().parent.parent / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    try:
        import hook_runtime as _hr
    except Exception:  # noqa: BLE001 — no hook_runtime (non-harness layout) → nothing to reset
        yield
        return
    _resets = ("_reset_config_cache", "_reset_nudge_channels_cache",
               "_reset_pending_system_messages")

    def _clear():
        for name in _resets:
            fn = getattr(_hr, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001 — a reset must never break a test
                    pass

    _clear()
    yield
    _clear()


# --- dev-repo-only test gating ------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_dev_tree(root: Path) -> bool:
    """True iff `root` is the harness DEVELOPMENT checkout, not an installed copy.
    The build toolkit (release/pack.py) is git-tracked in the dev repo but NEVER
    shipped in the bundle (pack.py's own contract), so its presence is the
    tamper-free signal that dev-only artifacts (docs/STANDARDIZE.md, the decision
    ledger, the dev CLAUDE.md rule routing) exist to assert against."""
    return (Path(root) / "release" / "pack.py").is_file()


def pytest_configure(config):
    """Register the markers the SHIPPED conftest owns so they travel with the
    bundle: an installed copy has no pyproject.toml, but the markers and their
    skip wiring must still work without an "unknown mark" warning. The live-lane
    marks (real_*) are declared in pyproject.toml too, which is NOT shipped — so
    they warn on installed copies; declare them here as well. The tests carrying
    them self-skip when their real credential is absent, so an installed run stays
    clean."""
    config.addinivalue_line(
        "markers",
        "dev_repo: asserts harness-development-repo facts (docs/ provenance, "
        "decision ledger, dev CLAUDE.md routing); auto-skipped on installed copies")
    for _lane in ("real_gemini", "real_agy", "real_sdk", "real_host"):
        config.addinivalue_line(
            "markers",
            "%s: live-lane test that spawns a real engine; self-skips when its "
            "credential is absent" % _lane)


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.dev_repo tests off the dev tree. install.sh runs this
    suite on the TARGET, where dev-only artifacts are absent; those tests assert
    harness-development facts and cannot — and should not — pass on an installed
    copy. Skipping where inapplicable is not weakening: the dev suite runs them in
    full (release/pack.py is present), so a real regression still trips here."""
    if is_dev_tree(_REPO_ROOT):
        return
    skip = pytest.mark.skip(reason="dev-repo-only: not the harness development tree")
    for item in items:
        if item.get_closest_marker("dev_repo"):
            item.add_marker(skip)


# --- stashed-skill coupling: drop off-skill tests from collection -------------
# A default-off install stashes ~70 skills under plugins/hs/disabled-skills/. A
# test that does `sys.path.insert(plugins/hs/skills/<off>/scripts)` + top-level
# import errors AT COLLECTION on such a target (the path is gone), aborting the
# whole run. Tests that merely reference an off skill's path at runtime fail the
# same way. Both are the SAME coupling: the test targets a skill that is not
# shipped here. Compute those files and hand them to pytest's `collect_ignore`.
#
# Self-gating by design: on the dev checkout NOTHING is stashed (disabled-skills/
# is empty or absent), so the ignore list is empty and every test runs — the dev
# tree stays the full-coverage gate. Only an installed default-off copy narrows.
#
# Match ONLY `skills/<name>/scripts` — the import-coupling signature. A test that
# imports a stashed skill's script at module level errors at COLLECTION on the
# target; the `/scripts` anchor is what separates that from tests that merely name
# a skill's SKILL.md / references/ path as DATA (those run + assert fine, and
# several — omit-record, hs-cli enable/disable — are exactly the default-off
# machinery that MUST run on the target). Widen only if a real collection error
# survives with a non-scripts coupling.
_SKILL_REF = re.compile(r"plugins/hs/skills/([A-Za-z0-9][A-Za-z0-9_-]*)/scripts(?![\w-])")
# pathlib-segmented form: `"skills" / "skill-creator" / "scripts"`. Some tests
# build the scripts path segment-by-segment, so the contiguous string above never
# appears in source. Match the quoted-segment chain too.
_SKILL_REF_SEG = re.compile(
    r'["\']skills["\']\s*/\s*["\']([A-Za-z0-9][A-Za-z0-9_-]*)["\']\s*/\s*["\']scripts["\']')


def _referenced_skills(text):
    return set(_SKILL_REF.findall(text)) | set(_SKILL_REF_SEG.findall(text))


def stashed_skill_coupled_files(tests_dir, skills_dir, stash_dir):
    """test_*.py basenames whose source imports a skill's script (contiguous
    `skills/<name>/scripts` OR pathlib-segmented) where <name> is stashed (present
    under stash_dir with a SKILL.md, absent from skills_dir). A skill living in
    BOTH trees is ON (re-enabled) and its tests are kept."""
    tests_dir, skills_dir, stash_dir = Path(tests_dir), Path(skills_dir), Path(stash_dir)
    if not stash_dir.is_dir():
        return []
    stashed = {
        p.name for p in stash_dir.iterdir()
        if (p / "SKILL.md").is_file() and not (skills_dir / p.name / "SKILL.md").is_file()
    }
    if not stashed:
        return []
    out = []
    for f in sorted(tests_dir.glob("test_*.py")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if _referenced_skills(text) & stashed:
            out.append(f.name)
    return out


# --- release-toolkit coupling: drop tests that import release/pack.py ----------
# A handful of dev-only tests (courier tree / lifecycle) do a top-level
# `import pack` (+ siblings) from release/, the build toolkit that is git-tracked
# in the dev repo but NEVER shipped (pack.py's own contract, the same signal
# is_dev_tree keys on). On an installed copy release/ is absent, so those modules
# ImportError AT COLLECTION and abort the whole run. Match the bare import so the
# files are dropped from collection there, exactly as the dev tree (where the
# toolkit is present) keeps and runs them in full.
_RELEASE_IMPORT = re.compile(r"^\s*(?:import|from)\s+pack(?=\s|$)", re.M)


def release_toolkit_coupled_files(tests_dir):
    """test_*.py basenames whose source top-level-imports the release build
    toolkit (release/pack.py). That module is not shipped, so an installed copy
    ImportErrors on these at collection; the caller drops them only off the dev
    tree, so coverage is unchanged where release/pack.py is present."""
    out = []
    for f in sorted(Path(tests_dir).glob("test_*.py")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if _RELEASE_IMPORT.search(text):
            out.append(f.name)
    return out


_TESTS_DIR = Path(__file__).resolve().parent
collect_ignore = stashed_skill_coupled_files(
    _TESTS_DIR,
    _REPO_ROOT / "harness/plugins/hs/skills",
    _REPO_ROOT / "harness/plugins/hs/disabled-skills",
)
# Off the dev tree (installed copy: no release/pack.py) also drop the tests that
# import the un-shipped release toolkit — otherwise they abort collection. On the
# dev checkout is_dev_tree is True, so nothing is added and they run in full.
if not is_dev_tree(_REPO_ROOT):
    collect_ignore = sorted(set(collect_ignore) | set(
        release_toolkit_coupled_files(_TESTS_DIR)))

# One small product spec: PRODUCT + vision + BRD (2 goals) + 1 PRD chain down
# to a story with acceptance criteria. The body-hash and AC-hash tests anchor
# on exact strings in here ("they reach the home page.", "$1M ARR") — keep
# those stable or update the test anchors with them.
_FIXTURE_FILES = {
    "docs/product/PRODUCT.md": """---
id: PRODUCT
type: product
status: approved
lang: en
owner: Jane Doe
version: 1.0.0
created: 2026-05-28
updated: 2026-05-28
name: "Acme Shop"
one_line_description: "A web storefront for boutique fashion brands."
current_implementation: "early prototype"
deployment: "Vercel + Supabase"
roadmap_one_liner: "Launch checkout flow this quarter."
core_value: "Help boutique brands sell directly to fans without middlemen."
personas: [shopper, store-admin]
---

# Acme Shop — Product Context

Thin labels for the Acme Shop fixture.
""",
    "docs/product/vision.md": """---
id: VISION
type: vision
status: approved
lang: en
owner: Jane Doe
version: 1.0.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper, store-admin]
---

# Vision — Acme Shop

## Problem Narrative

Boutique fashion brands sell through marketplaces that take 30%+ and bury
them under competitors. Their fans want to support them directly but have no
easy path. Acme Shop closes that gap.

## Value Proposition

For boutique brands, this is the only storefront that lets them keep 95% of
revenue and message fans directly.
""",
    "docs/product/brd.md": """---
id: BRD
type: brd
status: approved
lang: en
owner: Jane Doe
version: 1.0.0
created: 2026-05-28
updated: 2026-05-28
goals:
  - id: BRD-G1
    title: "Reach $1M ARR in 12 months"
    status: approved
    metrics: [arr]
  - id: BRD-G2
    title: "Hit 80% repeat-purchase rate"
    status: approved
    metrics: [repeat-rate]
---

# BRD

Reach the ARR goal by acquiring brands and converting their fans.
""",
    "docs/product/prds/auth.md": """---
id: PRD-AUTH
type: prd
brd_goals: [BRD-G1]
status: approved
lang: en
owner: Jane Doe
version: 1.0.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper]
scope: in
moscow: must
horizon: now
metrics: [signup-conversion]
---

# Auth PRD

Lets shoppers sign in.
""",
    "docs/product/epics/PRD-AUTH-E1.md": """---
id: PRD-AUTH-E1
type: epic
prd: PRD-AUTH
brd_goals: [BRD-G1]
status: draft
lang: en
owner: Jane Doe
version: 0.1.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper]
scope: in
moscow: must
horizon: now
---

# Sign-In Epic

Lets shoppers sign in with email + password.
""",
    "docs/product/stories/PRD-AUTH-E1-S1.md": """---
id: PRD-AUTH-E1-S1
type: story
epic: PRD-AUTH-E1
status: draft
lang: en
owner: Jane Doe
version: 0.1.0
created: 2026-05-28
updated: 2026-05-28
personas: [shopper]
scope: in
moscow: must
size: S
horizon: now
acceptance_criteria:
  - "Given a registered user, when they enter correct credentials, then they reach the home page."
  - "Given five failed attempts, when they try again, then they are rate-limited for 15 minutes."
---

# Sign-In Story

As a shopper I want to sign in so that I can resume my saved cart.
""",
}


def _build_valid() -> Path:
    base = Path(tempfile.mkdtemp(prefix="harness-valid-spec-"))
    atexit.register(shutil.rmtree, base, True)
    root = base / "valid-spec"
    for rel, content in _FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


VALID = _build_valid()


def _git(root: Path, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   capture_output=True, text=True)


def _git_out(root: Path, *args) -> str:
    """Like _git but returns stdout (for callers that read git output)."""
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def make_proj(tmp_path: Path, git: bool = True) -> Path:
    """A writable copy of the valid-spec fixture, optionally a committed git
    repo so the fence scan has a clean working-tree baseline (only NEW touches
    show)."""
    proj = tmp_path / "proj"
    shutil.copytree(VALID, proj)
    if git:
        _git(proj, "init", "-q")
        _git(proj, "config", "user.email", "t@t.t")
        _git(proj, "config", "user.name", "t")
        _git(proj, "add", "-A")
        _git(proj, "commit", "-q", "-m", "base")
    return proj


def append_to(proj: Path, rel: str, line: str):
    p = proj / "docs" / "product" / rel
    p.write_text(p.read_text(encoding="utf-8") + line, encoding="utf-8")
