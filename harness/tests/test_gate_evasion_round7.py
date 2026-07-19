"""Round-7 second-order evasion regressions.

A re-attack on the round-6 fixes found combined-flag wrappers + intra-token quotes
slipping stage detection, and subshell-cd / tee / sed / dd / python writes slipping
the artifact-forgery gate. These lock the fixes: detection normalizes the wrapper
and quote spellings; the forgery gate detects the artifacts dir as a write REGION
(path + write indicator) rather than reconstructing the exact target.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
sys.path.insert(0, str(ROOT / "harness" / "hooks"))
import stage_detector as sd  # noqa: E402
import gate_stage as gs  # noqa: E402


# ---- F5: combined-flag / long-flag shell -c wrappers ----
def test_combined_flag_c_wrapper_unwraps():
    assert sd.detect_stage("bash -lc 'npm publish'") == "ship"
    assert sd.detect_stage("bash -ec 'vercel deploy'") == "deploy"
    assert sd.detect_stage("bash --login -c 'git push'") == "push"
    assert sd.detect_stage("sh -xc 'git push'") == "push"


def test_plain_c_wrapper_still_works():
    assert sd.detect_stage("sh -c 'git push'") == "push"


# ---- F6: intra-token quote / ANSI-C / backslash verbs ----
def test_intra_token_quoted_verb_detected():
    assert sd.detect_stage("npm pu'bl'ish") == "ship"
    assert sd.detect_stage("np'm' publish") == "ship"
    assert sd.detect_stage("npm $'publish'") == "ship"


def test_unrelated_command_not_detected():
    assert sd.detect_stage("ls -la") is None
    assert sd.detect_stage("git status") is None


# ---- F1-F4 + sed/dd: artifact-forgery region check ----
def _blocked(cmd):
    return gs._artifact_forgery_reason(cmd) is not None


def test_subshell_cd_write_is_forgery():
    assert _blocked("(cd plans/p1/artifacts && echo forged > plan-approval.json)")
    assert _blocked("pushd plans/p1/artifacts && echo x > verification.json")


def test_tee_sed_dd_to_artifact_is_forgery():
    assert _blocked("echo x | tee 'plans/p1/artifacts/verification.json'")
    assert _blocked("sed -i s/a/b/ plans/p1/artifacts/verification.json")
    assert _blocked("dd of='plans/p1/artifacts/verification.json'")


def test_python_join_write_to_artifact_is_forgery():
    cmd = ("python3 -c \"from pathlib import Path; "
           "(Path('plans/p1/artifacts')/'verification.json').write_text('x')\"")
    assert _blocked(cmd)


def test_concatenated_quote_target_is_forgery():
    assert _blocked("echo x >'plans/p1/'\"artifacts/verification.json\"")


def test_reading_an_artifact_is_not_forgery():
    # reads must NOT be blocked — the gate stops writes, not inspection
    assert not _blocked("cat plans/p1/artifacts/verification.json")
    assert not _blocked("grep PASS plans/p1/artifacts/verification.json")


def test_heredoc_to_other_file_mentioning_artifact_is_not_forgery():
    # writing a doc/plan whose CONTENT mentions an artifact path (the redirect
    # targets some OTHER file) must not over-block — heredoc bodies are stripped
    cmd = "cat >> BACKLOG.md <<'EOF'\nsee plans/p1/artifacts/x.json\nEOF"
    assert not _blocked(cmd)


def test_heredoc_into_an_artifact_is_forgery():
    # but a heredoc whose redirect TARGET is the artifact is still forgery
    cmd = "cat > plans/p1/artifacts/verification.json <<'EOF'\n{}\nEOF"
    assert _blocked(cmd)


def test_reading_or_copying_an_artifact_out_is_not_over_blocked():
    # round-8 regression: the region check must not block a command that READS an
    # artifact (or merely names its path) while writing ELSEWHERE
    assert not _blocked("cat plans/p1/artifacts/verification.json > /tmp/x")
    assert not _blocked("cp plans/p1/artifacts/verification.json /tmp/backup.json")
    assert not _blocked('echo "see plans/p1/artifacts/x.json" >> docs/notes.md')
    assert not _blocked('sed -i "s|x|plans/p1/artifacts|" docs/a.md')
    assert not _blocked("diff plans/p1/artifacts/a.json plans/p1/artifacts/b.json")


def test_quoted_cp_mv_install_into_artifact_is_forgery():
    # round-9: a quoted cp/mv/install DEST that is an artifact (layer-2 masked it)
    assert _blocked("cp /tmp/forged 'plans/p1/artifacts/verification.json'")
    assert _blocked("mv /tmp/forged 'plans/p1/artifacts/verification.json'")
    assert _blocked("install -m644 /tmp/x 'plans/p1/artifacts/verification.json'")


def test_quoted_sed_target_artifact_is_forgery():
    # round-9: sed -i with a quoted target FILE that is the artifact
    assert _blocked("sed -i 's/a/b/' 'plans/p1/artifacts/verification.json'")


def test_round9_fix_does_not_re_introduce_over_block():
    # cp reading an artifact OUT, sed naming it in the SCRIPT, and an artifact path
    # quoted in heredoc CONTENT written elsewhere must all stay allowed
    assert not _blocked("cp plans/p1/artifacts/verification.json /tmp/backup.json")
    assert not _blocked('sed -i "s|x|plans/p1/artifacts|" docs/a.md')
    assert not _blocked(
        "cat >> notes.md <<'E'\ncp /tmp/f 'plans/p1/artifacts/v.json'\nE")
