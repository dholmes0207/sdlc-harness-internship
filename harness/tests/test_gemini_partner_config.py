"""Loader contract for the gemini-partner lane config (phase 1).

The loader is gate-grade (fail-CLOSED on a forbidden combo / unknown model), so
the suite drives it with scratch YAML files and asserts SystemExit on the reject
paths. Round-trip is checked per-field: LESSONS says a field only survives if the
loader carries it in BOTH read and write, so each axis is flipped independently.
"""
import textwrap

import pytest

import gemini_partner_config as gpc


# Canonical, S1-safe base. Tests copy + flip one axis so a round-trip miss on any
# single field is isolated (not masked by another field also defaulting).
_BASE = {
    "master": "off",
    "mode": "partner",
    "write": "read_only",
    "stop_review_gate": "off",
    "purposes": {
        "research": "flash", "scout": "flash", "review": "pro",
        "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro",
    },
    "route_all_surface": ["research", "scout"],
    "overrides": {},
    "timeouts": {"default": 120, "scout": 180},
    "retry": {"max_attempts": 2, "on_markers": ["rate_limit", "overloaded"]},
    "secret_scrub": "warn",
}


def _write(tmp_path, **overrides):
    import yaml
    cfg = {**_BASE, **overrides}
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


# --- T1: shipped default is the safe corner --------------------------------
def test_t1_shipped_default_safe_corner():
    cfg = gpc.resolve()  # no path/env → shipped harness/data/gemini-partner.yaml
    assert cfg["master"] == "off"
    assert cfg["mode"] == "partner"
    assert cfg["write"] == "read_only"
    assert cfg["stop_review_gate"] == "off"


# --- T2: per-field non-default round-trip -----------------------------------
@pytest.mark.parametrize("field,value", [
    ("master", "on"),
    ("mode", "route-all"),          # write stays read_only → no S1
    ("write", "sandbox_write"),     # mode stays partner    → no S1
    ("stop_review_gate", "advisory"),
])
def test_t2_roundtrip_each_axis(tmp_path, field, value):
    cfg = gpc.resolve(_write(tmp_path, **{field: value}))
    assert cfg[field] == value


def test_t2_bare_off_on_not_folded_to_bool(tmp_path):
    # YAML 1.1 folds bare `off`/`on` to bool; the loader must recover the token.
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(textwrap.dedent("""\
        master: on
        mode: partner
        write: read_only
        stop_review_gate: off
        purposes: {research: flash, scout: flash, review: pro, critique: pro, redteam: pro, delegate: pro, fix: pro}
        route_all_surface: [research, scout]
        overrides: {}
        timeouts: {default: 120, scout: 180}
        retry: {max_attempts: 2, on_markers: [rate_limit]}
        secret_scrub: warn
    """), encoding="utf-8")
    cfg = gpc.resolve(p)
    assert cfg["master"] == "on"
    assert cfg["stop_review_gate"] == "off"


# --- T3: S1 forbidden combo rejected at load --------------------------------
def test_t3_s1_route_all_plus_sandbox_write_rejects(tmp_path):
    p = _write(tmp_path, mode="route-all", write="sandbox_write")
    with pytest.raises(SystemExit) as e:
        gpc.resolve(p)
    assert "S1" in str(e.value)


# --- T3b: a missing axis key fails CLOSED with a clean reason (not KeyError) -
def test_t3b_missing_axis_key_systemexit(tmp_path):
    import yaml
    cfg = {**_BASE}
    del cfg["master"]
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        gpc.resolve(p)
    assert "master" in str(e.value)  # actionable reason, not a raw KeyError


def test_t3c_timeouts_without_default_rejected(tmp_path):
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, timeouts={"scout": 5}))  # no `default`
    assert "timeouts" in str(e.value) and "default" in str(e.value)


# --- T4: S7 unknown override model rejected ---------------------------------
def test_t4_s7_unknown_override_model_rejects(tmp_path):
    p = _write(tmp_path, overrides={"foo": "gemini-9-nope"})
    with pytest.raises(SystemExit) as e:
        gpc.resolve(p)
    msg = str(e.value)
    assert "gemini-9-nope" in msg and "S7" in msg


# --- T5: S6 normalize — master off inertizes stop gate ----------------------
def test_t5_s6_master_off_normalizes(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, master="off", stop_review_gate="advisory",
                             mode="route-all"))
    eff = gpc.effective(cfg)
    assert eff["stop_review_gate"] == "off"
    assert eff["mode"] == "inert"
    assert eff["write"] == "read_only"


def test_t5_master_on_leaves_config_intact(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, master="on", stop_review_gate="advisory"))
    eff = gpc.effective(cfg)
    assert eff["stop_review_gate"] == "advisory"
    assert eff["mode"] == "partner"


# --- T6: purpose tier maps through resolve_model ----------------------------
def test_t6_tier_for_review_is_pro(tmp_path):
    import resolve_model
    cfg = gpc.resolve(_write(tmp_path))
    assert gpc.tier_for(cfg, "review") == resolve_model.resolve_model(tier="pro")
    assert gpc.tier_for(cfg, "research") == resolve_model.resolve_model(tier="flash")


def test_t6_is_advisory_split():
    assert gpc.is_advisory("research") is True
    assert gpc.is_advisory("redteam") is True
    assert gpc.is_advisory("delegate") is False
    assert gpc.is_advisory("fix") is False


# --- T7: env-override reads the dev file ------------------------------------
def test_t7_env_override_reads_dev_file(tmp_path, monkeypatch):
    p = _write(tmp_path, route_all_surface="all")
    monkeypatch.setenv("HARNESS_GEMINI_PARTNER", str(p))
    cfg = gpc.resolve()  # no explicit path → env wins over shipped
    assert cfg["route_all_surface"] == "all"


def test_t7_explicit_path_beats_env(tmp_path, monkeypatch):
    (tmp_path / "dev").mkdir(exist_ok=True)
    dev = _write(tmp_path / "dev", route_all_surface="all")
    shipped = _write(tmp_path, route_all_surface=["research"])
    monkeypatch.setenv("HARNESS_GEMINI_PARTNER", str(dev))
    cfg = gpc.resolve(shipped)
    assert cfg["route_all_surface"] == ["research"]


# --- T8: timeout resolution with fallback -----------------------------------
def test_t8_timeout_for_verb_then_default(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, timeouts={"default": 120, "scout": 180}))
    assert gpc.timeout_for(cfg, "scout") == 180
    assert gpc.timeout_for(cfg, "review") == 120  # no per-verb → default


# --- Phase 5: route_all_injection axis + S8/S9/S6' --------------------------
def test_route_all_injection_roundtrip_on(tmp_path):
    # non-default (on) must survive read→write; route-all so no S9, read_only so no S8
    cfg = gpc.resolve(_write(tmp_path, mode="route-all", route_all_injection="on"))
    assert cfg["route_all_injection"] == "on"


def test_route_all_injection_default_off(tmp_path):
    # absent → off (OPTIONAL, not a required key — F2)
    assert gpc.resolve(_write(tmp_path))["route_all_injection"] == "off"


def test_s8_reject_injection_plus_sandbox_write(tmp_path):
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, mode="route-all", route_all_injection="on",
                           write="sandbox_write"))
    assert "S8" in str(e.value)


def test_s9_reject_injection_without_route_all(tmp_path):
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, mode="partner", route_all_injection="on"))
    assert "S9" in str(e.value)


def test_s6_master_off_normalizes_injection(tmp_path):
    # master off ⇒ effective view forces injection off (S6'), even if the file says on.
    cfg = gpc.resolve(_write(tmp_path, master="off", mode="route-all",
                             route_all_injection="on"))
    assert gpc.effective(cfg)["route_all_injection"] == "off"


def test_bool_fold_recovery(tmp_path):
    # YAML folds bare `on` → bool True; the loader must recover the token
    import yaml
    cfg = {**_BASE, "mode": "route-all", "route_all_injection": True}
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    assert gpc.resolve(p)["route_all_injection"] == "on"


def _inj_cfg(surface, injection="on"):
    return {"mode": "route-all", "route_all_injection": injection,
            "route_all_surface": surface}


def test_should_route_injection(monkeypatch):
    monkeypatch.setattr(gpc, "_skill_is_injectable", lambda name: name == "research")
    cfg = _inj_cfg(["research", "scout"])
    assert gpc.should_route_injection(cfg, "research") is True
    assert gpc.should_route_injection({**cfg, "route_all_injection": "off"}, "research") is False
    assert gpc.should_route_injection({**cfg, "mode": "partner"}, "research") is False


def test_should_route_injection_composes_should_route(monkeypatch):
    # skill NOT in route_all_surface → should_route False → injection False
    monkeypatch.setattr(gpc, "_skill_is_injectable", lambda name: True)
    assert gpc.should_route_injection(_inj_cfg(["research"]), "scout") is False


def test_should_route_injection_absent_field_false(monkeypatch):
    # skill in surface + injection on, but injectable field absent → False (fail-closed)
    monkeypatch.setattr(gpc, "_skill_is_injectable", lambda name: False)
    assert gpc.should_route_injection(_inj_cfg(["research"]), "research") is False


# --- Phase 2: engine axis + auto-detect + per-engine models ------------------
def test_p2t1_engine_gemini_print_pinned(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, engine="gemini-print"))
    assert cfg["engine"] == "gemini-print"
    assert gpc._resolve_engine(cfg) == "gemini-print"


def test_p2t1b_engine_gemini_acp_rejected_breaking(tmp_path):
    # BREAKING (ACP retired): the old engine name is no longer a valid enum member.
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, engine="gemini-acp"))
    assert "engine" in str(e.value)


def test_p2t2_engine_agy_print_pinned_ignores_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-present")  # pin wins over env (D7)
    cfg = gpc.resolve(_write(tmp_path, engine="agy-print"))
    assert gpc._resolve_engine(cfg) == "agy-print"


def test_p2t3_auto_with_key_picks_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-present")
    cfg = gpc.resolve(_write(tmp_path, engine="auto"))
    assert gpc._resolve_engine(cfg) == "gemini-print"


def test_p2t4_auto_without_key_picks_agy(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = gpc.resolve(_write(tmp_path, engine="auto"))
    assert gpc._resolve_engine(cfg) == "agy-print"


def test_p2t5_engine_enum_rejected(tmp_path):
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, engine="banana"))
    assert "engine" in str(e.value) and "one of" in str(e.value)


def test_p2t6_missing_engine_defaults_auto(tmp_path):
    # an old config without `engine` loads (back-compat) and defaults to auto
    cfg = gpc.resolve(_write(tmp_path))  # _BASE has no engine key
    assert cfg["engine"] == "auto"


def test_p2t7_flat_overrides_still_s7_rejected(tmp_path):
    # the flat `overrides` reader is the gemini branch — S7 model-SSOT check intact
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, overrides={"review": "gemini-9-nope"}))
    assert "S7" in str(e.value)


def test_p2t7b_flat_overrides_preserved(tmp_path):
    # a flat {review: id} override round-trips unchanged (gemini branch, F1)
    cfg = gpc.resolve(_write(tmp_path, overrides={"review": "gemini-2.5-pro"}))
    assert cfg["overrides"] == {"review": "gemini-2.5-pro"}


def test_p2t8_overrides_agy_outside_allowlist_rejected(tmp_path):
    with pytest.raises(SystemExit) as e:
        gpc.resolve(_write(tmp_path, overrides_agy={"review": "GPT-9 Turbo"}))
    assert "allowlist" in str(e.value)


def test_p2t8b_overrides_agy_in_allowlist_ok(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, overrides_agy={"review": "Gemini 3.1 Pro (High)"}))
    assert gpc._agy_model_for(cfg, "review") == "Gemini 3.1 Pro (High)"


def test_p2t9_engine_models_default_reasoning(tmp_path):
    cfg = gpc.resolve(_write(tmp_path))  # no engine_models → defaults
    assert gpc._agy_model_for(cfg, "research") == "Gemini 3.5 Flash (Medium)"  # flash
    assert gpc._agy_model_for(cfg, "review") == "Gemini 3.1 Pro (High)"        # pro


def test_p2t9b_uncovered_tier_warns_at_load_and_falls_back(tmp_path, capsys):
    purposes = {"research": "flash", "scout": "flash", "review": "pro",
                "critique": "pro", "redteam": "pro", "delegate": "pro",
                "fix": "pro", "weird": "ultra"}  # 'ultra' has no engine_model
    cfg = gpc.resolve(_write(tmp_path, purposes=purposes))
    assert "ultra" in capsys.readouterr().err  # WARN at load, not a crash
    # runtime fall-back to the flash default, still no crash
    assert gpc._agy_model_for(cfg, "weird") == "Gemini 3.5 Flash (Medium)"


def test_p2t10_engine_non_default_roundtrips(tmp_path):
    cfg = gpc.resolve(_write(tmp_path, engine="agy-print"))
    assert gpc.effective(cfg)["engine"] == "agy-print"  # not backfilled to auto


# --- Phase 7: agy login heuristic + inert-auth-early -------------------------
def test_p7_agy_login_present_via_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AGY_CMD", raising=False)
    monkeypatch.setenv("HARNESS_AGY_HOME", str(tmp_path / "agyhome"))
    assert gpc._agy_login_present() is False  # presence heuristic — dir absent
    (tmp_path / "agyhome").mkdir()
    assert gpc._agy_login_present() is True   # present (NOT validity — canary confirms)


def test_p7_agy_login_present_via_override_cmd(monkeypatch):
    monkeypatch.setenv("HARNESS_AGY_CMD", "python3 fake")
    assert gpc._agy_login_present() is True   # a wired override binary counts


def test_p7_auth_inert_auto_no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HARNESS_AGY_CMD", raising=False)
    monkeypatch.setenv("HARNESS_AGY_HOME", str(tmp_path / "none"))
    cfg = gpc.resolve(_write(tmp_path, engine="auto"))
    assert gpc._auth_inert(cfg) is True


def test_p7_auth_inert_false_with_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-present")
    cfg = gpc.resolve(_write(tmp_path, engine="auto"))
    assert gpc._auth_inert(cfg) is False


def test_p7_auth_inert_false_when_pinned(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HARNESS_AGY_CMD", raising=False)
    monkeypatch.setenv("HARNESS_AGY_HOME", str(tmp_path / "none"))
    cfg = gpc.resolve(_write(tmp_path, engine="agy-print"))
    assert gpc._auth_inert(cfg) is False  # a pin is never inert-auth
