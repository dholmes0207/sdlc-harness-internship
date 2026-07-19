"""hs:spec templates: generate_templates.py token substitution,
optional-section drop, unresolved-token guard, and the full alloc-then-write flow."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_DIR = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec"
_SPEC_SCRIPTS = _SPEC_DIR / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(_SPEC_SCRIPTS, [
    "encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph",
    "fs_guard", "template_id_alloc", "generate_templates",
])
spec_graph = _mods["spec_graph"]
generate_templates = _mods["generate_templates"]
frontmatter_parser = _mods["frontmatter_parser"]

_STORY_TEMPLATE = (_SPEC_DIR / "assets" / "templates" / "story.md").read_text(encoding="utf-8")


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _seed_prd_and_epic(root: Path) -> None:
    _write(root, "docs/product/PRODUCT.md",
           "---\nid: PRODUCT\ntype: product\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# P\n")
    _write(root, "docs/product/prds/x.md",
           "---\nid: PRD-X\ntype: prd\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# PRD X\n")
    _write(root, "docs/product/epics/PRD-X-E1.md",
           "---\nid: PRD-X-E1\ntype: epic\nprd: PRD-X\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# Epic\n")


# ---------------------------------------------------------------------------
# generate story template — answers dict -> valid frontmatter + AC list
# ---------------------------------------------------------------------------

def test_render_story_template_valid_frontmatter_and_ac(tmp_path):
    values = generate_templates.fill_defaults(
        {
            "id": "PRD-X-E1-S1",
            "epic": "PRD-X-E1",
            "personas": ["shopper"],
            "scope": "in",
            "moscow": "must",
            "size": "S",
            "horizon": "now",
            "acceptance_criteria": [
                "Given a cart, when checkout, then order is placed.",
            ],
            "title": "Checkout",
            "persona": "shopper",
            "want": "to check out",
            "so_that": "I receive my order",
            "acceptance_criteria_bullets": "- Given a cart, when checkout, then order is placed.",
        },
        "story", "PRD-X-E1-S1", "en",
    )
    rendered = generate_templates.render(_STORY_TEMPLATE, values, keep_optional=[])

    out = tmp_path / "story.md"
    out.write_text(rendered, encoding="utf-8")
    parsed = frontmatter_parser.parse_file(out)
    assert parsed["ok"] is True
    fm = parsed["frontmatter"]
    assert fm["id"] == "PRD-X-E1-S1"
    assert fm["epic"] == "PRD-X-E1"
    assert fm["acceptance_criteria"] == [
        "Given a cart, when checkout, then order is placed."]
    assert "Given a cart, when checkout, then order is placed." in rendered
    # Optional sections not requested are dropped entirely.
    assert "## Notes" not in rendered
    assert "## Dependencies" not in rendered


def test_render_story_template_keeps_requested_optional_section():
    values = generate_templates.fill_defaults(
        {"id": "PRD-X-E1-S1", "epic": "PRD-X-E1", "notes": "design TBD"},
        "story", "PRD-X-E1-S1", "en",
    )
    rendered = generate_templates.render(_STORY_TEMPLATE, values, keep_optional=["notes"])
    assert "## Notes" in rendered
    assert "design TBD" in rendered
    assert "## Dependencies" not in rendered  # not requested, still dropped


# ---------------------------------------------------------------------------
# unresolved token — never writes garbage; analytical-script contract is
# exit 0 with a JSON `error`/`written:false` finding, never a bare traceback
# (generate_templates.py's own docstring / validation-rules-spec.md:63).
# ---------------------------------------------------------------------------

def test_render_rejects_hyphenated_unresolved_token():
    bad_template = "---\nid: {{id}}\n---\n\n# {{title}}\n\nbroken: {{bad-key}}\n"
    with pytest.raises(ValueError, match="unresolved template token"):
        generate_templates.render(bad_template, {"id": "X", "title": "T"}, [])


def test_main_writes_nothing_on_unresolved_token(tmp_path, monkeypatch, capsys):
    _seed_prd_and_epic(tmp_path)
    # A story template shadowing the real one, deliberately carrying a token
    # TOKEN_RE cannot match (hyphenated key) so it survives substitution.
    bad_templates_dir = tmp_path / "bad-templates"
    bad_templates_dir.mkdir()
    (bad_templates_dir / "story.md").write_text(
        "---\nid: {{id}}\nepic: {{epic}}\n---\n\n# {{title}}\n\n{{bad-key}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_templates, "TEMPLATES_DIR", bad_templates_dir)
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--write",
    ])

    ret = generate_templates.main()

    out = json.loads(capsys.readouterr().out)
    assert ret == 0                      # analytical-script contract: never non-zero
    assert out["written"] is False
    assert out["error"] == "invalid_input"
    assert "unresolved template token" in out["message"]
    stories_dir = tmp_path / "docs" / "product" / "stories"
    assert not stories_dir.exists() or list(stories_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# full alloc-then-write flow through _run (id allocation + fs_guard fence +
# refuse-to-clobber all exercised together)
# ---------------------------------------------------------------------------

def test_run_allocates_id_and_writes_story_under_docs_product(tmp_path, monkeypatch):
    _seed_prd_and_epic(tmp_path)
    # `--values` accepts a JSON file OR an inline string (load_values); a file
    # sidesteps Path(spec).exists() raising ENAMETOOLONG on a long inline string.
    values_file = tmp_path / "values.json"
    values_file.write_text(json.dumps({
        "title": "Checkout", "persona": "shopper", "want": "to check out",
        "so_that": "I receive my order",
        "acceptance_criteria_bullets": "- Given a cart, when checkout, then order is placed.",
        "acceptance_criteria": ["Given a cart, when checkout, then order is placed."],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--values", str(values_file), "--write",
    ])
    ret = generate_templates.main()
    assert ret == 0

    out_file = tmp_path / "docs" / "product" / "stories" / "PRD-X-E1-S1.md"
    assert out_file.is_file()
    parsed = frontmatter_parser.parse_file(out_file)
    assert parsed["frontmatter"]["id"] == "PRD-X-E1-S1"
    assert parsed["frontmatter"]["status"] == "draft"


def test_run_writes_template_through_atomic_writer(tmp_path, monkeypatch):
    # The generated artifact is a fixed docs/product path a human/CI reads while a
    # --force re-render may rewrite it — a bare open(path,"w") truncates at open()
    # time, exposing a 0-byte window to a concurrent reader. The write must route
    # through write_text_atomic (temp + os.replace). Spy on it: a bare-write impl
    # never calls it, so `seen` stays empty and the assertion fails (red/green).
    _seed_prd_and_epic(tmp_path)
    values_file = tmp_path / "values.json"
    values_file.write_text(json.dumps({
        "title": "Checkout", "persona": "shopper", "want": "to check out",
        "so_that": "I receive my order",
        "acceptance_criteria_bullets": "- Given a cart, when checkout, then order is placed.",
        "acceptance_criteria": ["Given a cart, when checkout, then order is placed."],
    }), encoding="utf-8")
    seen = {}
    real = getattr(generate_templates, "write_text_atomic", None)

    def _spy(path, text, *a, **k):
        seen["path"] = Path(path)
        seen["text"] = text
        if real is not None:
            return real(path, text, *a, **k)

    monkeypatch.setattr(generate_templates, "write_text_atomic", _spy, raising=False)
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--values", str(values_file), "--write",
    ])
    assert generate_templates.main() == 0
    out_file = tmp_path / "docs" / "product" / "stories" / "PRD-X-E1-S1.md"
    assert seen.get("path") == out_file
    assert out_file.read_text(encoding="utf-8") == seen["text"]


# ---------------------------------------------------------------------------
# Regression guard: `--type change_log_entry --write` used to `from
# change_log_writer import write_change_log_entry` — a module that was cut,
# so a real invocation raised ModuleNotFoundError. change_log_entry is
# dropped from TYPE_TEMPLATE/argparse choices entirely; every SURVIVING type
# must still generate + --write cleanly (none of them may reach for a module
# that isn't there).
# ---------------------------------------------------------------------------

# One valid --id per surviving type (bypasses the parent-scoped allocator so
# each type can be exercised in isolation, without seeding a docs/product/
# tree of parents first).
_ALL_REMAINING_TYPE_IDS = {
    "product": "PRODUCT",
    "vision": "VISION",
    "brd": "BRD",
    "prd": "PRD-X",
    "epic": "PRD-X-E1",
    "story": "PRD-X-E1-S1",
    "exec_summary": "EXEC-SUMMARY",
    "release_notes": "RELEASE-NOTES",
    "sign_off": "SIGN-OFF",
}


def test_change_log_entry_dropped_from_type_choices():
    assert "change_log_entry" not in generate_templates.TYPE_TEMPLATE
    assert set(generate_templates.TYPE_TEMPLATE) == set(_ALL_REMAINING_TYPE_IDS)


def test_generate_templates_source_never_imports_the_cut_writer():
    src = (_SPEC_SCRIPTS / "generate_templates.py").read_text(encoding="utf-8")
    assert "change_log_writer" not in src


def test_generate_templates_help_omits_change_log_entry(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["generate_templates.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        generate_templates.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "change_log_entry" not in out
    assert "story" in out  # sanity: the surviving types are still offered


@pytest.mark.parametrize(
    "target_type,artifact_id", sorted(_ALL_REMAINING_TYPE_IDS.items())
)
def test_every_remaining_type_writes_without_crashing(
    tmp_path, monkeypatch, capsys, target_type, artifact_id
):
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", target_type,
        "--id", artifact_id, "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert "error" not in out, f"{target_type} failed to generate: {out}"


# ---------------------------------------------------------------------------
# fill_defaults must coerce a non-list/non-dict caller value for a
# LIST_FIELDS/MAP_FIELDS key too, not just restore the default on an
# explicit `None` -- a caller-supplied scalar (`--values
# '{"personas":"power users"}'`) used to ride through untouched and render as
# a bare YAML scalar, which a downstream `for p in personas` reader
# char-splits into single-letter "personas".
# ---------------------------------------------------------------------------

def test_fill_defaults_coerces_scalar_list_field_to_single_item_list():
    out = generate_templates.fill_defaults(
        {"personas": "power users"}, "story", "PRD-X-E1-S1", "en",
    )
    assert out["personas"] == ["power users"]


def test_fill_defaults_coerces_scalar_map_field_to_empty_dict():
    out = generate_templates.fill_defaults(
        {"competitive_parity": "not a mapping"}, "prd", "PRD-X", "en",
    )
    assert out["competitive_parity"] == {}


def test_fill_defaults_still_restores_none_list_field():
    out = generate_templates.fill_defaults(
        {"personas": None}, "story", "PRD-X-E1-S1", "en",
    )
    assert out["personas"] == []


def test_render_scalar_persona_value_no_longer_char_splits(tmp_path):
    values = generate_templates.fill_defaults(
        {
            "id": "PRD-X-E1-S1", "epic": "PRD-X-E1", "personas": "power users",
            "scope": "in", "moscow": "must", "size": "S", "horizon": "now",
            "title": "Checkout", "persona": "shopper", "want": "to check out",
            "so_that": "I receive my order",
            "acceptance_criteria_bullets": "- x",
        },
        "story", "PRD-X-E1-S1", "en",
    )
    rendered = generate_templates.render(_STORY_TEMPLATE, values, keep_optional=[])
    out = tmp_path / "story.md"
    out.write_text(rendered, encoding="utf-8")
    parsed = frontmatter_parser.parse_file(out)
    assert parsed["ok"] is True
    assert parsed["frontmatter"]["personas"] == ["power users"]


# ---------------------------------------------------------------------------
# The unresolved-token residual scan runs on the PRE-substitution template
# text now, not the rendered output -- a legitimate caller value that happens
# to CONTAIN literal "{{...}}" text (e.g. a list value that json.dumps
# preserves verbatim) must not be misread as an unresolved template token.
# ---------------------------------------------------------------------------

def test_render_caller_value_with_literal_braces_is_not_rejected():
    template = "---\nid: {{id}}\nacceptance_criteria: {{acceptance_criteria}}\n---\n\n# {{id}}\n"
    values = {
        "id": "X",
        "acceptance_criteria": ["contains a literal {{token}} in the text"],
    }
    rendered = generate_templates.render(template, values, keep_optional=[])
    assert "{{token}}" in rendered  # preserved verbatim, not stripped or rejected


# ---------------------------------------------------------------------------
# A Unicode line-separator in a caller value must be rejected by the same guard
# that already rejects \n/\r. PyYAML's plain-scalar scanner folds U+2028 /
# U+2029 / U+0085 (NEL) into line breaks, so `owner: "x\u2028status: approved"`
# re-materializes on re-read as a SECOND YAML line (`status: approved`), forging
# a pre-approved artifact past the fill_defaults status=='approved' guard — that
# guard only inspects the raw pre-substitution value, never the byte a separator
# smuggles in. Same class as the \n/\r reject, a different (invisible) vector.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_render_rejects_unicode_line_separator_in_value(sep):
    template = "---\nid: {{id}}\nowner: {{owner}}\n---\n\n# {{id}}\n"
    with pytest.raises(ValueError, match="single-line"):
        generate_templates.render(
            template, {"id": "PRD-X", "owner": "x%sstatus: approved" % sep}, [],
        )


def test_main_rejects_separator_forged_approval_and_writes_nothing(
    tmp_path, monkeypatch, capsys
):
    _seed_prd_and_epic(tmp_path)
    values_file = tmp_path / "values.json"
    values_file.write_text(
        json.dumps({"title": "Checkout", "owner": "x\u2028status: approved"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--values", str(values_file), "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "invalid_input"
    stories_dir = tmp_path / "docs" / "product" / "stories"
    assert not stories_dir.exists() or list(stories_dir.iterdir()) == []


def test_separator_forged_approval_never_lands_as_approved_on_disk(
    tmp_path, monkeypatch, capsys
):
    # Belt-and-suspenders: the guard blocks the write, so nothing lands; but even
    # if a regression let a file through, it must never parse back approved.
    _seed_prd_and_epic(tmp_path)
    values_file = tmp_path / "values.json"
    values_file.write_text(
        json.dumps({"title": "Checkout", "owner": "x\u2028status: approved"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--values", str(values_file), "--write",
    ])
    generate_templates.main()
    capsys.readouterr()
    out_file = tmp_path / "docs" / "product" / "stories" / "PRD-X-E1-S1.md"
    if out_file.exists():
        parsed = frontmatter_parser.parse_file(out_file)
        assert parsed["frontmatter"].get("status") != "approved"


def test_reject_prd_collision_message_names_actual_suffix():
    # The rejection itself is correct; the message text must name the actual
    # offending suffix ("-E9"), not `slug.split('-E', 1)[-1]` (which drops the
    # "-E" and misleadingly reports the bare "-'9'").
    template_id_alloc = _mods["template_id_alloc"]
    with pytest.raises(ValueError) as exc:
        template_id_alloc.reject_prd_collision("AUTH-E9", "--slug 'AUTH-E9'")
    message = str(exc.value)
    assert "-E9" in message
    assert "-'9'" not in message


def test_run_refuses_to_clobber_existing_file_without_force(tmp_path, monkeypatch, capsys):
    _seed_prd_and_epic(tmp_path)
    argv = [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1",
        "--values", json.dumps({"title": "Checkout"}),
        "--write",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert generate_templates.main() == 0
    capsys.readouterr()  # drain first write's stdout

    # Second invocation targets a fresh --id (S2) so it doesn't collide by
    # allocation, then re-target the SAME id explicitly to force the clobber path.
    monkeypatch.setattr(sys, "argv", argv[:-1] + ["--id", "PRD-X-E1-S1", "--write"])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "exists"


# ---------------------------------------------------------------------------
# --id override must run the same PRD-looks-like-epic/story collision
# guard allocate_id() applies on its slug path (template_id_alloc.reject_prd_collision()).
# Without it, `--type prd --id PRD-AUTH-E9` and `--type epic --id PRD-AUTH-E9`
# both succeed (different output paths so the exists() guard never fires),
# minting two artifacts sharing one id.
# ---------------------------------------------------------------------------

def test_run_rejects_prd_id_that_collides_with_epic_grammar(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "prd",
        "--id", "PRD-AUTH-E9", "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "invalid_input"
    assert "epic" in out["message"].lower()
    assert not (tmp_path / "docs" / "product" / "prds").exists()


def test_run_rejects_prd_id_that_collides_with_story_grammar(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "prd",
        "--id", "PRD-AUTH-E9-S1", "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "invalid_input"


# ---------------------------------------------------------------------------
# When both --id and --parent are supplied for epic/story, the parent
# implied by --id must match --parent. Without the check, `--type story
# --id PRD-FOO-E1-S1 --parent PRD-FOO-E2` writes frontmatter epic: PRD-FOO-E2
# under an id that says its epic is E1 -- a corrupted graph edge.
# ---------------------------------------------------------------------------

def test_run_rejects_story_id_parent_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--id", "PRD-FOO-E1-S1", "--parent", "PRD-FOO-E2", "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "invalid_input"
    assert "parent" in out["message"].lower()
    assert not (tmp_path / "docs" / "product" / "stories").exists()


def test_run_rejects_epic_id_parent_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "epic",
        "--id", "PRD-FOO-E1", "--parent", "PRD-BAR", "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out["written"] is False
    assert out["error"] == "invalid_input"
    assert "parent" in out["message"].lower()


def test_run_allows_story_id_parent_when_consistent(tmp_path, monkeypatch, capsys):
    _seed_prd_and_epic(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "generate_templates.py", "--root", str(tmp_path), "--type", "story",
        "--id", "PRD-X-E1-S1", "--parent", "PRD-X-E1",
        "--values", json.dumps({"title": "Checkout"}), "--write",
    ])
    ret = generate_templates.main()
    out = json.loads(capsys.readouterr().out)
    assert ret == 0
    assert out.get("error") is None
    assert out["written"] is True


def test_generate_write_serializes_under_flock(tmp_path):
    # generate_templates mints an id from the whole graph then writes; without a
    # lock two concurrent --write calls under the same parent read the same graph,
    # allocate the same next id, and the second write silently clobbers the first
    # (both report written:true — real data loss). Assert the --write path
    # serializes on docs/product/.generate.lock: while an external holder holds
    # LOCK_EX, a --write invocation must BLOCK, then proceed once released.
    import fcntl
    import subprocess

    _seed_prd_and_epic(tmp_path)
    lock_path = tmp_path / "docs" / "product" / ".generate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(_SPEC_SCRIPTS / "generate_templates.py"),
        "--root", str(tmp_path), "--type", "story",
        "--parent", "PRD-X-E1", "--write",
    ]
    with open(lock_path, "a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            # Lock held -> the child cannot enter its allocate→write section.
            with pytest.raises(subprocess.TimeoutExpired):
                proc.communicate(timeout=3)
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    # Lock released -> child proceeds, allocates, writes, exits cleanly.
    out, _err = proc.communicate(timeout=15)
    assert proc.returncode == 0, _err
    assert json.loads(out)["written"] is True


def test_template_id_alloc_anchors_reject_trailing_newline():
    # Cycle-31 hardened id_grammar to \Z; template_id_alloc's accept-validators
    # must match that SSOT — a trailing-newline slug/id must NOT validate (Python
    # `$` matches before a final \n; `\Z` does not).
    template_id_alloc = _mods["template_id_alloc"]
    assert template_id_alloc.SLUG_PATTERN_FOR_PRD.match("AUTH\n") is None
    assert template_id_alloc.ID_PATTERN_OVERRIDE["brd"].match("BRD\n") is None
    assert template_id_alloc.ID_PATTERN_OVERRIDE["exec_summary"].match("EXEC-SUMMARY\n") is None
