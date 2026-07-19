"""test_lens_skill_usage.py — the skill-usage lens: which skills are hot, which
owned skills are never invoked (read-only, advisory). Feeds hs:insights so the
LLM can suggest trimming dead skills / promoting hot paths — it never judges or
mutates.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_skill_usage as lens  # noqa: E402


def _seed_invocations(tmp_path, rows):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    lines = []
    now = datetime.now(timezone.utc)
    for skill, age_days, sess in rows:
        ts = (now - timedelta(days=age_days)).isoformat()
        lines.append(json.dumps({"ts": ts, "skill": skill, "session": sess,
                                 "via": "PreToolUse:Skill"}))
    (tel / "invocations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_skills(tmp_path, owned_names):
    sdir = tmp_path / "skills"
    for name in owned_names:
        d = sdir / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: hs:%s\ndescription: x\n---\n# hs:%s\n" % (name, name),
            encoding="utf-8")
    return sdir


def test_counts_top_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [
        ("hs:plan", 1, "s1"), ("hs:plan", 2, "s2"), ("hs:cook", 1, "s1"),
    ])
    sdir = _seed_skills(tmp_path, ["plan", "cook", "ship"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    counts = {row["skill"]: row["count"] for row in agg["top_skills"]}
    assert counts["plan"] == 2
    assert counts["cook"] == 1
    assert agg["total_invocations"] == 3


def test_never_used_owned_skills_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [("hs:plan", 1, "s1")])
    sdir = _seed_skills(tmp_path, ["plan", "cook", "ship"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    # cook + ship are owned but never invoked → flagged; plan is used → not flagged
    assert "cook" in agg["never_used_owned"]
    assert "ship" in agg["never_used_owned"]
    assert "plan" not in agg["never_used_owned"]


def test_old_invocations_excluded_by_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [("hs:plan", 90, "s1")])  # outside a 30-day window
    sdir = _seed_skills(tmp_path, ["plan"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    assert agg["total_invocations"] == 0
    assert "plan" in agg["never_used_owned"]   # no in-window use


def test_low_volume_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [("hs:plan", 1, "s1")])
    sdir = _seed_skills(tmp_path, ["plan"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    assert agg["gated"] is True   # one data point is below the low-volume threshold


def test_never_used_unreliable_when_corpus_too_sparse(tmp_path, monkeypatch):
    # 1 invocation but 3 owned skills → far too few data points to call the
    # other 2 "unused" (Skill-tool telemetry misses by-hand runs). The honesty
    # flag must say so, even though total>=low-volume threshold is irrelevant here.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [("hs:plan", 1, "s1")])
    sdir = _seed_skills(tmp_path, ["plan", "cook", "ship"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    assert agg["never_used_reliable"] is False     # too sparse to judge non-use


def test_never_used_reliable_when_corpus_covers_owned(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    # >= one invocation per owned skill on average → non-use is a real signal
    rows = [("hs:plan", 1, "s%d" % i) for i in range(6)]
    _seed_invocations(tmp_path, rows)
    sdir = _seed_skills(tmp_path, ["plan", "cook", "ship"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)
    assert agg["never_used_reliable"] is True
    assert "cook" in agg["never_used_owned"]       # the list is still computed


def test_skips_non_object_jsonl_line(tmp_path, monkeypatch):
    # a parseable-but-non-object line ([1,2,3], "x", 42) must be SKIPPED, not blank
    # the whole lens (it used to raise AttributeError → visible error row).
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    ts = datetime.now(timezone.utc).isoformat()
    (tel / "invocations.jsonl").write_text(
        '[1,2,3]\n"junk"\n{"ts":"%s","skill":"hs:plan","session":"s1"}\n' % ts,
        encoding="utf-8")
    sdir = _seed_skills(tmp_path, ["plan"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir)  # must not raise
    assert agg["total_invocations"] == 1
    assert {r["skill"]: r["count"] for r in agg["top_skills"]}["plan"] == 1


# --- off-skill re-enable demand surfacing ------------------------------------

def _seed_demand(tmp_path, rows):
    """rows = (skill, age_days, session, via). Demand markers keyed on the target."""
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = []
    for skill, age_days, sess, via in rows:
        ts = (now - timedelta(days=age_days)).isoformat()
        lines.append(json.dumps({"ts": ts, "skill": skill, "session": sess,
                                 "via": via, "proxy_invoked": True}))
    p = tel / "invocations.jsonl"
    prev = p.read_text(encoding="utf-8") if p.is_file() else ""
    p.write_text(prev + "\n".join(lines) + "\n", encoding="utf-8")


def _seed_disabled(tmp_path, names):
    """Minimal off-skill tree so disabled_skills.status(name)=='disabled'."""
    stash = tmp_path / "harness" / "plugins" / "hs" / "disabled-skills"
    for n in names:
        (stash / n).mkdir(parents=True, exist_ok=True)
        (stash / n / "SKILL.md").write_text(
            "---\nname: hs:%s\ndescription: x\n---\n# hs:%s\n" % (n, n), encoding="utf-8")
    rec = tmp_path / "harness" / "state" / "install-omitted-skills.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"omitted": list(names)}), encoding="utf-8")


def test_surfaces_reenable_only_above_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_disabled(tmp_path, ["critique", "brainstorm"])
    _seed_demand(tmp_path, [
        # critique wanted by 3 DISTINCT sessions (2 proxy_run + 1 router_block) → hint
        ("hs:critique", 1, "s1", "proxy_run"),
        ("hs:critique", 2, "s2", "proxy_run"),
        ("hs:critique", 3, "s3", "router_block"),
        # a 4th row from an already-counted session must NOT inflate the distinct count
        ("hs:critique", 1, "s1", "proxy_run"),
        # brainstorm only 2 distinct sessions → below threshold → NOT surfaced
        ("hs:brainstorm", 1, "s1", "proxy_run"),
        ("hs:brainstorm", 2, "s2", "proxy_run"),
    ])
    sdir = _seed_skills(tmp_path, ["plan"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir, root=tmp_path)
    cands = {c["skill"]: c for c in agg["reenable_candidates"]}
    assert "critique" in cands and cands["critique"]["sessions"] == 3
    assert "brainstorm" not in cands              # 2 sessions < threshold 3
    out = lens.render(agg)
    # the re-enable HINT is threshold-gated on distinct-session count; only critique
    # (3 sessions) crosses it.
    assert "hs-cli skills --enable critique" in out
    assert "--enable brainstorm" not in out       # sub-threshold demand → no hint


def test_demand_rows_not_counted_as_invocations(tmp_path, monkeypatch):
    # A demand marker records that an off skill was REACHED while off — it is not a
    # tool-invocation and must not inflate top_skills / total_invocations. Only the
    # real invocation (via PreToolUse:Skill) is a usage count.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_invocations(tmp_path, [("plan", 1, "s0")])     # one REAL invocation
    _seed_demand(tmp_path, [
        ("hs:critique", 1, "s1", "proxy_run"),
        ("hs:critique", 2, "s2", "router_block"),
    ])
    sdir = _seed_skills(tmp_path, ["plan", "critique"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir, root=tmp_path)
    counts = {row["skill"]: row["count"] for row in agg["top_skills"]}
    assert counts == {"plan": 1}                          # critique demand NOT counted
    assert agg["total_invocations"] == 1
    assert "critique" not in counts


def test_reenabled_skill_not_surfaced(tmp_path, monkeypatch):
    # Demand crossed the threshold, but the skill is LIVE now (not in the disabled
    # tree) → stale demand, never nag to re-enable something already on.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed_disabled(tmp_path, [])                          # nothing disabled
    _seed_demand(tmp_path, [
        ("hs:critique", 1, "s1", "proxy_run"),
        ("hs:critique", 2, "s2", "proxy_run"),
        ("hs:critique", 3, "s3", "proxy_run"),
    ])
    sdir = _seed_skills(tmp_path, ["plan"])
    agg = lens.gather(days=30, top=10, skills_dir=sdir, root=tmp_path)
    assert agg["reenable_candidates"] == []
    assert "--enable critique" not in lens.render(agg)
