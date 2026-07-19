"""test_config_writers.py — narrow write CLIs for the two gate-adjacent configs
that were read-only until now: team.yaml (roster + claims) and output.yaml
(generated-prose language). /hs:setup drives these.

team.yaml is gate config: the writer accepts reviewers + lease_s, but REFUSES
allow_self_review — flipping solo-mode on is a posture decision that must be a
deliberate, git-visible hand edit, not a one-liner the setup flow can do for you
(the plan-approval role check is the backstop). An explicit --file keeps the
no-ambient-env-override property: only a visible argument can redirect the write,
never an environment variable.
"""
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import output_config as oc  # noqa: E402

_SEED_TEAM = (
    "# team.yaml — roster + claims (hand note kept across writes)\n"
    "reviewers: []\n"
    "allow_self_review: false\n"
    "claims:\n"
    "  lease_s: 14400\n"
)
_SEED_OUTPUT = "# output.yaml\nlanguage: vi\nhumanize: true\n"


def _seed(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- output_config.save_output + CLI ------------------------------------------

def test_save_output_writes_language_and_humanize(tmp_path):
    p = _seed(tmp_path, "output.yaml", _SEED_OUTPUT)
    oc.save_output({"language": "en", "humanize": False}, path=p)
    loaded = oc.load_output(path=p)
    assert loaded["language"] == "en"
    assert loaded["humanize"] is False


def test_save_output_rejects_bad_language(tmp_path):
    p = _seed(tmp_path, "output.yaml", _SEED_OUTPUT)
    before = p.read_text(encoding="utf-8")
    try:
        oc.save_output({"language": "fr"}, path=p)
        raised = False
    except oc.OutputConfigError:
        raised = True
    assert raised
    assert p.read_text(encoding="utf-8") == before


def _run_output(*args):
    return subprocess.run([sys.executable, str(_SCRIPTS / "output_config.py"), *args],
                          capture_output=True, text=True)


def test_cli_output_set_language(tmp_path):
    p = _seed(tmp_path, "output.yaml", _SEED_OUTPUT)
    r = _run_output("--file", str(p), "--set", "language=en")
    assert r.returncode == 0, r.stderr
    assert oc.load_output(path=p)["language"] == "en"
