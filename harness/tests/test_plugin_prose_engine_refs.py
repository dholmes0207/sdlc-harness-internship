r"""CI grep-guard: no hardcoded `python3 harness/` run-ref may reappear in plugin
prose. Under the courier those must resolve through
`"${HARNESS_BIN_ROOT:-.}"/harness/...` — this covers `harness/scripts/`,
`harness/plugins/.../scripts/`, and `harness/hooks/` alike. A regression that
reintroduces the bare literal is caught here.

Skill/agent `.md` prose is auto-migrated by sweep_engine_refs.py. Workflow `.js`
prose is NOT auto-swept (a mechanical replace would emit an UN-escaped `${...}`
that the JS template layer interpolates — broken); those files carry the same
env-path form with the `$` escaped for JS (`\${HARNESS_BIN_ROOT:-.}`), applied by
hand. This guard covers all three subtrees so either path can't regress.

`python3 -m pytest harness/tests/` is NOT flagged (the ` -m pytest ` between breaks
the `python3 harness/` substring), and the env-prefixed form carries `"${...}"`
between `python3 ` and `harness/`, so neither the correct migrated form nor a pytest
invocation trips this guard.

Tracked-only (git grep). The forbidden pattern is built from fragments so this
guard never trips on its own source (memory: git-grep absence-guard sees
tracked-only). A line that intentionally cites the old form must carry a
`# learn:` prefix (mirrors the CI invariant #1 whitelist).
"""
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Assemble the banned literal from parts — never write it whole in a tracked file.
_BANNED = "python3 " + "harness/"

_TARGETS = ("harness/plugins/hs/skills", "harness/plugins/hs/agents",
            "harness/plugins/hs/workflows")


def _hits():
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "grep", "-n", "-F", _BANNED, "--", *_TARGETS],
        capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    # allow an explicit citation of the old form behind a learn: marker
    return [l for l in lines if "# learn:" not in l and "learn:" not in l.split(":", 2)[-1][:40]]


def test_no_bare_run_ref_in_plugin_prose():
    hits = _hits()
    assert not hits, (
        "bare `%s` run-ref(s) found in plugin prose — migrate via "
        "sweep_engine_refs.py to the env-path form:\n%s"
        % (_BANNED, "\n".join(hits[:20])))
