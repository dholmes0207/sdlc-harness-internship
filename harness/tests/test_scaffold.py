"""Tests for scaffold.py — deterministic plan/report skeletons from templates.

scaffold renders the template files under harness/templates/ with {{TOKEN}}
substitution, stamps the harness provenance frontmatter via artifact_stamp
(so a new plan never carries a stale kit_digest), and writes into plans/ only —
a slug that tries to climb out (../, a slash) is rejected before any write.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import scaffold  # noqa: E402


class TestRender:
    def test_substitutes_known_tokens(self):
        out = scaffold.render("a {{TITLE}} b {{ID}}", {"TITLE": "X", "ID": "42"})
        assert out == "a X b 42"

    def test_repeated_token_all_replaced(self):
        out = scaffold.render("{{S}}-{{S}}", {"S": "z"})
        assert out == "z-z"

    def test_unfilled_tokens_reported(self):
        text = "{{A}} kept {{B}}"
        assert scaffold.unfilled_tokens(scaffold.render(text, {"A": "x"})) == ["B"]

    def test_no_tokens_left_after_full_fill(self):
        out = scaffold.render("{{A}}{{B}}", {"A": "1", "B": "2"})
        assert scaffold.unfilled_tokens(out) == []


class TestSlugGuard:
    @pytest.mark.parametrize("bad", ["../evil", "a/b", "Up", "has space", "", "-x", "x-"])
    def test_bad_slug_rejected(self, bad):
        with pytest.raises(ValueError):
            scaffold.validate_slug(bad)

    @pytest.mark.parametrize("ok", ["plan", "a-b-c", "feat-123", "x1"])
    def test_good_slug_ok(self, ok):
        assert scaffold.validate_slug(ok) == ok


class TestScaffoldPlan:
    def _call(self, root, **kw):
        kw.setdefault("plan_id", "260618-2251")
        kw.setdefault("slug", "demo-plan")
        kw.setdefault("title", "Demo Plan")
        kw.setdefault("mode", "hard")
        kw.setdefault("tdd", True)
        kw.setdefault("phases", ["scout", "build"])
        return scaffold.scaffold_plan(root=root, **kw)

    def test_creates_dir_and_files(self, tmp_path):
        # templates live in the real repo; copy them under the tmp root
        _seed_templates(tmp_path)
        d = self._call(tmp_path)
        assert d == tmp_path / "plans" / "260618-2251-demo-plan"
        assert (d / "plan.md").is_file()
        # one phase file per phase, numbered, under the phases/ subdir
        assert (d / "phases" / "phase-1-scout.md").is_file()
        assert (d / "phases" / "phase-2-build.md").is_file()
        # not at the plan-dir root (guards a regression back to the flat layout)
        assert not (d / "phase-1-scout.md").exists()

    def test_plan_frontmatter_stamped_and_filled(self, tmp_path):
        _seed_templates(tmp_path)
        d = self._call(tmp_path)
        text = (d / "plan.md").read_text(encoding="utf-8")
        assert "title: \"Demo Plan\"" in text
        assert "id: 260618-2251-demo-plan" in text
        # the machine-stamped provenance keys are present (reused artifact_stamp)
        assert "harness_version:" in text
        assert "harness_kit_digest:" in text
        assert "harness_schema_version:" in text
        # no unfilled placeholders leaked into the artifact
        assert scaffold.unfilled_tokens(text) == [], scaffold.unfilled_tokens(text)

    def test_phase_files_named_and_filled(self, tmp_path):
        _seed_templates(tmp_path)
        d = self._call(tmp_path)
        p1 = (d / "phases" / "phase-1-scout.md").read_text(encoding="utf-8")
        assert "phase: 1" in p1
        assert scaffold.unfilled_tokens(p1) == []

    def test_plan_author_stamped_from_actor(self, tmp_path, monkeypatch):
        # upstream fix: scaffold stamps `author:` into plan.md frontmatter so
        # plan_approval._resolve_author finds it and --author is never demanded.
        _seed_templates(tmp_path)
        monkeypatch.setenv("HARNESS_USER", "alice@example.com")
        for var in ("HARNESS_AGENT", "CI", "GITHUB_ACTIONS", "GITLAB_CI"):
            monkeypatch.delenv(var, raising=False)
        d = self._call(tmp_path)
        text = (d / "plan.md").read_text(encoding="utf-8")
        assert "author: user:alice@example.com" in text
        assert scaffold.unfilled_tokens(text) == []

    def test_plan_author_strips_agent_suffix(self, tmp_path, monkeypatch):
        # the plan author is the human identity, not the agent lane — a
        # HARNESS_AGENT suffix (user:x/agent:y) must not leak into `author:`.
        _seed_templates(tmp_path)
        monkeypatch.setenv("HARNESS_USER", "bob@example.com")
        monkeypatch.setenv("HARNESS_AGENT", "developer")
        for var in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"):
            monkeypatch.delenv(var, raising=False)
        d = self._call(tmp_path)
        text = (d / "plan.md").read_text(encoding="utf-8")
        assert "author: user:bob@example.com" in text
        assert "/agent:" not in text.split("author:", 1)[1].splitlines()[0]

    def test_refuses_clobber_without_force(self, tmp_path):
        _seed_templates(tmp_path)
        self._call(tmp_path)
        with pytest.raises(FileExistsError):
            self._call(tmp_path)

    def test_force_overwrites(self, tmp_path):
        _seed_templates(tmp_path)
        self._call(tmp_path)
        d = self._call(tmp_path, force=True)  # no raise
        assert (d / "plan.md").is_file()

    def test_bad_slug_no_write(self, tmp_path):
        _seed_templates(tmp_path)
        with pytest.raises(ValueError):
            self._call(tmp_path, slug="../escape")
        assert not (tmp_path / "plans").exists() or not any(
            (tmp_path / "plans").iterdir())


class TestScaffoldReport:
    def test_creates_report_path(self, tmp_path):
        _seed_templates(tmp_path)
        p = scaffold.scaffold_report(
            root=tmp_path, report_id="260618-2251", slug="findings",
            rtype="research", title="Findings")
        assert p == (tmp_path / "plans" / "reports"
                     / "research-260618-2251-findings-report.md")
        assert p.is_file()
        text = p.read_text(encoding="utf-8")
        assert "harness_version:" in text
        assert scaffold.unfilled_tokens(text) == []


class TestCLI:
    def test_print_writes_nothing(self, tmp_path):
        _seed_templates(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "scaffold.py"), "plan",
             "--slug", "cli-demo", "--title", "CLI Demo", "--id", "260618-2251",
             "--root", str(tmp_path), "--print"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "title:" in proc.stdout
        assert not (tmp_path / "plans").exists()


class TestTemplateEngineRelative:
    """Templates load beside the script (engine root), NOT from --root.

    Under a global install the project output-root carries no
    harness/templates/ tree ("no per-project tree copy") — scaffold must still
    find the templates beside itself and write output under --root. These cases
    deliberately DO NOT seed templates into the tmp output root.
    """

    def test_plan_reads_templates_engine_relative(self, tmp_path):
        d = scaffold.scaffold_plan(
            root=tmp_path, plan_id="260618-2251", slug="global-demo",
            title="Global Demo", phases=["scout"])
        assert d == tmp_path / "plans" / "260618-2251-global-demo"
        assert (d / "plan.md").is_file()
        assert (d / "phases" / "phase-1-scout.md").is_file()

    def test_report_reads_templates_engine_relative(self, tmp_path):
        p = scaffold.scaffold_report(
            root=tmp_path, report_id="260618-2251", slug="global-findings",
            rtype="research", title="Global Findings")
        assert p.is_file()

    def test_provenance_stamps_engine_release_not_output_root(self, tmp_path):
        # The provenance stamp (harness_version + kit_digest) identifies the
        # ENGINE that produced the artifact — it must be read beside the script,
        # like the canonical stamper (artifact_stamp reads harness_paths.root()),
        # NOT from the empty output --root. Under global the output root has no
        # harness/ tree, so reading release from it stamps a bogus 0.0.0-dev +
        # empty digest. Same class of bug as the template read.
        import harness_release  # noqa: E402 — resolves the engine/bin identity
        engine = harness_release.read_release()
        assert engine["kit_digest"], "engine must expose a non-empty kit_digest"
        d = scaffold.scaffold_plan(
            root=tmp_path, plan_id="260618-2251", slug="prov-demo",
            title="Prov Demo", phases=["scout"])
        text = (d / "plan.md").read_text(encoding="utf-8")
        assert "harness_version: %s" % engine["harness_version"] in text
        assert "harness_kit_digest: %s" % engine["kit_digest"] in text


def _seed_templates(root: Path) -> None:
    """Copy the shipped templates into a tmp root so scaffold reads them there."""
    src = _ROOT / "harness" / "templates"
    dst = root / "harness" / "templates"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.md"):
        (dst / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    # scaffold stamps via artifact_stamp -> harness_release.read_release, which
    # reads the manifest for the kit_digest; a tmp root has none, so it falls
    # back to the dev digest. Provide a minimal release marker is unnecessary.
