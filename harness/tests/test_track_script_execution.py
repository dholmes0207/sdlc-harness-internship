"""test_track_script_execution.py — exit inference precision (P3b hardening).

This host exposes no reliable numeric exit code in the PostToolUse:Bash payload,
so `exit` is inferred. The AUTHORITATIVE signal is tool_response.is_error /
interrupted. When BOTH are absent the only honest fallback is a high-precision
anchor: a success never prints "exit code 1". The old fallback scanned stderr
for bare Error|Exception|Traceback, which over-matched benign output ("Error
handling OK", a test that prints the word "Traceback") and inflated the failure
rate the reliability lens reads. Unknown ⇒ success.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _p in (str(_HOOKS), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import track_script_execution as t  # noqa: E402


# --- authoritative host signal wins -------------------------------------------

def test_is_error_true_infers_1_regardless_of_stderr():
    assert t.infer_exit({"is_error": True, "stderr": ""}, "") == 1


def test_interrupted_true_infers_1():
    assert t.infer_exit({"interrupted": True}, "") == 1


# --- no host signal: benign stderr must NOT inflate to a failure --------------

def test_benign_error_word_in_stderr_is_not_a_failure():
    assert t.infer_exit({}, "Error handling OK\n") == 0


def test_benign_exception_word_in_stderr_is_not_a_failure():
    assert t.infer_exit({}, "Exception path exercised; all good\n") == 0


def test_word_traceback_in_text_is_not_a_failure():
    assert t.infer_exit({}, "see the Traceback section of the docs\n") == 0


def test_clean_run_with_empty_stderr_is_success():
    assert t.infer_exit({"stdout": "ok", "stderr": ""}, "") == 0


def test_no_errors_found_message_is_success():
    assert t.infer_exit({}, "No errors found. 0 failures.\n") == 0


# --- no host signal: only an explicit non-zero exit phrase counts -------------

def test_explicit_nonzero_exit_code_phrase_infers_1():
    assert t.infer_exit({}, "command failed: exit code 1\n") == 1


def test_returned_non_zero_exit_status_phrase_infers_1():
    assert t.infer_exit({}, "subprocess returned non-zero exit status 2\n") == 1


def test_exit_code_zero_is_success():
    assert t.infer_exit({}, "finished: exit code 0\n") == 0
