"""artifact_io.py — the one gate-artifact writer: run_seq stamp + atomic write.

The three gate producers (plan_approval, write_verification, write_review_decision)
route their final write through stamp_and_write, so run_seq is stamped in a SINGLE
place (D1) and every gate artifact lands atomically. The orchestrator's watchdog reads
these by run_seq to reject a stale/prior-run artifact — which only works if the
producer stamps it.

D1 boundary discipline: tầng-1 does NOTHING with run_seq's semantics — it reads the env
the orchestrator exported and writes the field. Env absent → run_seq:null; a standalone
harness (no orchestrator) writes null forever and stays correct.
"""
import json
import os
from pathlib import Path

_ENV_KEY = "HARNESS_RUN_SEQ"


class CrossVolumeError(RuntimeError):
    """The .tmp landed on a different volume than the target dir, so os.replace could
    not be atomic. Raised BEFORE any replace so the reader never sees a torn file."""


def _run_seq_from_env(env=None):
    """The orchestrator-exported run_seq as int, or None when absent/blank/malformed.
    Fail-open to null (never raise): a dev running the gate without an orchestrator
    must still write a valid artifact (back-compat)."""
    env = os.environ if env is None else env
    raw = env.get(_ENV_KEY)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _serialize(path: Path, rec: dict) -> str:
    if path.suffix == ".yaml":
        import yaml
        return yaml.safe_dump(rec, allow_unicode=True, sort_keys=False)
    return json.dumps(rec, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path, body, *, newline=None) -> None:
    """Write `body` to `path` atomically AND durably. A .tmp in the SAME dir is
    fsync'd before os.replace, so "no torn/empty file" holds across a crash or power
    loss too: os.replace makes the directory entry atomic, but the tmp's data blocks
    may still be unflushed (the classic rename-without-fsync gap → an empty/partial
    file after power loss). A cross-volume tmp fails loud (CrossVolumeError) instead
    of a silently non-atomic replace, and a stray .tmp is never left for a reader to
    mistake for content. `newline` is passed straight to open() — register writers
    pass "" to keep literal CRLF/LF bytes."""
    path = Path(path)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        if os.stat(tmp).st_dev != os.stat(path.parent).st_dev:
            raise CrossVolumeError(
                "tmp %s and target dir %s are on different volumes — os.replace would "
                "not be atomic; refusing a torn write" % (tmp, path.parent))
        os.replace(tmp, path)
    except BaseException:
        # never leave a stray .tmp a reader might later mistake for content
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def stamp_and_write(path, record, *, env=None) -> dict:
    """Stamp run_seq (from HARNESS_RUN_SEQ, null if absent) into a COPY of `record`,
    serialize by suffix (.yaml/.json), and write it via atomic_write_text (same-dir
    fsync'd .tmp + same-volume assert + os.replace). Returns the stamped record."""
    path = Path(path)
    rec = dict(record)
    rec["run_seq"] = _run_seq_from_env(env)
    atomic_write_text(path, _serialize(path, rec))
    return rec
