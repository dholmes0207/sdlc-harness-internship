"""test_scope_hint.py — the Lever A scope classifier (suggest-only).

scope_hint.classify reads a raw prompt and returns a coarse difficulty band plus
whether a heavy SDLC skill was invoked. It NEVER changes a mode or downgrades
anything — the injector uses the verdict only to append a one-line advisory when a
trivial task rides a heavy skill. Pure, stdlib-only; over-flagging toward `risky`
is the safe bias (a false `risky` just withholds the lighter-mode suggestion).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import scope_hint  # noqa: E402


def test_keyword_forces_risky():
    v = scope_hint.classify("update auth schema in one file")
    assert v["level"] == "risky"


def test_keyword_stem_matches_plurals():
    # stem/substring, not \bword\b — plurals + variants still trip risky.
    assert scope_hint.classify("run the migrations now")["level"] == "risky"
    assert scope_hint.classify("add authentication to the endpoint")["level"] == "risky"
    assert scope_hint.classify("touch up the schemas file")["level"] == "risky"


def test_destructive_verbs_are_risky():
    # the guarded direction: a genuinely destructive op must NEVER read trivial,
    # or the heavy-skill advisory would nudge "consider a lighter path" over it.
    for prompt in ("/hs:plan drop the users table",
                   "/hs:plan truncate the orders table",
                   "deletion of all customer records",
                   "deploy the release to production",
                   "revoke access for user bob",
                   "grant admin permission to bob",
                   "rotate the api key",
                   "wipe the staging database"):
        assert scope_hint.classify(prompt)["level"] == "risky", prompt


def test_version_numbers_not_counted_as_files():
    # 3.12/3.13/... must not inflate the file-mention count into `standard`.
    assert scope_hint.classify("bump version to 3.12 and 3.13 and 3.14 and 3.15")["level"] == "trivial"


def test_small_scope_trivial():
    # positive evidence of small scope: a tiny-scope verb (`typo`) reads trivial
    # even with no file path named.
    assert scope_hint.classify("fix typo in README")["level"] == "trivial"


def test_no_positive_trivial_signal_is_standard():
    # The corrected bias: absence of a scope signal is NOT triviality. A prose
    # prompt that names no file and carries no tiny-scope verb reads `standard`,
    # never `trivial` — so a heavy-skill prose task stops earning the lighter-path
    # nudge (the false-positive the old `no file mention -> trivial` default fired).
    assert scope_hint.classify("giải thích hàm parse_config làm gì")["level"] == "standard"


def test_prose_architecture_task_under_heavy_skill_not_trivial():
    # the exact misfire: a big architectural task written in prose names no file,
    # so the old classifier read it identical to `fix typo`. It must now be
    # `standard` — no positive trivial signal — so no nudge rides a real /hs:plan.
    for prompt in ("/hs:plan redesign the whole caching layer across the app",
                   "/hs:cook rework the entire orchestrator retry and backoff"):
        v = scope_hint.classify(prompt)
        assert v["level"] == "standard", prompt
        assert v["heavy_skill"] is True


def test_three_file_boundary():
    three = scope_hint.classify("edit alpha.py beta.py gamma.py")
    four = scope_hint.classify("edit alpha.py beta.py gamma.py delta.py")
    assert three["level"] == "trivial"
    assert four["level"] == "standard"


def test_never_downgrades():
    # the verdict is advisory data only — it must NOT carry a mode/action to apply.
    v = scope_hint.classify("/hs:plan fix typo")
    assert "mode" not in v
    assert "action" not in v
    assert set(v.keys()) == {"level", "reason", "heavy_skill"}


def test_heavy_skill_detected():
    assert scope_hint.classify("/hs:plan fix typo")["heavy_skill"] is True
    assert scope_hint.classify("hs:cook the plan")["heavy_skill"] is True
    assert scope_hint.classify("just answer this question")["heavy_skill"] is False


def test_risky_under_heavy_skill_stays_risky():
    v = scope_hint.classify("/hs:plan migrate the auth schema")
    assert v["level"] == "risky"
    assert v["heavy_skill"] is True
