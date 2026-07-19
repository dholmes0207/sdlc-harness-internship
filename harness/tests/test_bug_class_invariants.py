"""test_bug_class_invariants.py — cross-cutting invariants over harness/.

Not unit tests: each test sweeps the tree for a CLASS of bug that review
caught once and must never come back. Failures name the offending file(s).

  1. Hooks never import skill code (hooks are runtime, skills are prose).
  2. Every compliance-class hook is registered in hooks-registration.yaml
     (co-presence: a gate that exists but is never wired protects nothing).
  3. OWNERSHIP: no file under harness/ references ClaudeKit's skill/hook
     paths under its dot-claude tree. ClaudeKit is MIT OSS so its code may be
     PORTED (copied/adapted) into harness/ — but harness stays self-contained
     and never couples to that tree at runtime, so a path reference is still
     banned. Lines that cite a dot-claude path need a `# learn:` prefix.
  4. Machine-written store writes go through the shared writers (trace_log
     append_event / telemetry append_event) — no ad-hoc JSONL writes into
     state/ from elsewhere.
  5. Wording: fs_guard is a "script-path containment helper" — no file may
     call it a "write-fence" (it cannot stop raw LLM writes; the name must
     not promise what the mechanism cannot do).
  6. Every harness-owned skill declares a compliance-tier the governance
     model recognizes (catalog.tier_problems()).
"""
import re
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent


def _py_files(subdir=None):
    base = _HARNESS / subdir if subdir else _HARNESS
    return [p for p in base.rglob("*.py") if "state" not in p.parts]


def _all_text_files():
    out = []
    for p in _HARNESS.rglob("*"):
        if not p.is_file() or "state" in p.parts:
            continue
        if p.suffix in (".py", ".md", ".yaml", ".yml", ".sh", ".json"):
            out.append(p)
    return out


class TestHookImportBoundary:
    def test_hooks_never_import_skill_code(self):
        # Skills are prose for the LLM; hooks are runtime. A hook importing
        # from skills/ would couple enforcement to documentation.
        offenders = []
        # Match the `skills` package as a whole path segment (top-level `import skills`
        # or a dotted `foo.skills`), NOT any identifier that merely ENDS in "skills":
        # a scripts lib like `disabled_skills` is runtime code, not skill prose, and
        # must not trip this. The boundary before `skills` is a `.` or the segment
        # start, never a `_`/word char.
        for p in _py_files("hooks"):
            text = p.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*(?:from|import)\s+(?:[\w.]+\.)?skills?\b", text) \
                    or "harness/skills" in text:
                offenders.append(str(p))
        assert not offenders, "hooks importing skill code: %s" % offenders

    def test_gate_path_never_imports_the_network_adapter(self):
        # A flaky remote provider must not be able to block or unblock a
        # push: nothing under hooks/ may import the task-store adapter, and
        # artifact_check (the gate's validator) may not import the adapter
        # OR claims (claims would drag the mirror's network code into the
        # gate's import graph).
        pattern = re.compile(
            r"(?m)^\s*(?:from|import)\s+(task_store|task_store_github)\b")
        offenders = []
        for p in _py_files("hooks"):
            if pattern.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p))
        ac = _HARNESS / "scripts" / "artifact_check.py"
        gate_pattern = re.compile(
            r"(?m)^\s*(?:from|import)\s+(claims|task_store|task_store_github)\b")
        if gate_pattern.search(ac.read_text(encoding="utf-8")):
            offenders.append(str(ac))
        assert not offenders, (
            "network adapter reachable from the gate path: %s" % offenders)


class TestComplianceRegistration:
    def test_every_compliance_hook_is_registered(self):
        reg = (_HARNESS / "install" / "hooks-registration.yaml").read_text(
            encoding="utf-8")
        # a compliance hook migrated into hook_dispatch.py fires as a dispatcher core,
        # not its own command — it is registered via hook-dispatch.yaml (counted ONLY
        # when the dispatcher itself is wired). hook_dispatch.py is the multiplexer,
        # not a leaf gate (its posture is per-core), so it is exempt from this check.
        disp = _HARNESS / "data" / "hook-dispatch.yaml"
        dispatched = set()
        if "hook_dispatch.py" in reg and disp.is_file():
            dispatched = set(re.findall(r'module:\s*([A-Za-z0-9_]+)',
                                        disp.read_text(encoding="utf-8")))
        missing = []
        for p in _py_files("hooks"):
            if p.name == "hook_dispatch.py":
                continue  # the dispatcher itself is not a leaf gate
            text = p.read_text(encoding="utf-8")
            if re.search(r'(?m)^HOOK_CLASS\s*=\s*["\']compliance["\']', text):
                if p.name not in reg and p.stem not in dispatched:
                    missing.append(p.name)
        assert not missing, (
            "compliance hooks not wired in hooks-registration.yaml nor hook-dispatch.yaml: "
            "%s (a gate that is never registered never gates)" % missing)


class TestOwnershipBoundary:
    def test_no_reference_to_claudekit_tree(self):
        # The hard constraint: harness code never references ClaudeKit's
        # skill/hook paths. The banned literal is assembled so THIS file
        # does not itself contain it; a line explaining a LEARNED pattern
        # must carry the `# learn:` whitelist prefix.
        banned = re.compile(r"\.claude/(?:%s|%s)/" % ("skills", "hooks"))
        offenders = []
        for p in _all_text_files():
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if banned.search(line) and "# learn:" not in line:
                    offenders.append("%s:%d" % (p, i))
        assert not offenders, (
            "harness/ must not reference ClaudeKit's tree (learn-only "
            "boundary); annotate learned-pattern notes with `# learn:`.\n%s"
            % "\n".join(offenders))


class TestPortedGraphStaysDecoupled:
    """The product-spec graph builder began as a verbatim port; the standards
    graph builds on an independently-typed core. Neither side may import the
    other — that decoupling is what lets each evolve without rippling into the
    other's test surface. The port is no longer byte-frozen against its upstream:
    it carries one approved crash-safety divergence — build_nodes guards the
    tree-relative path against a symlink resolving outside the tree so build_graph
    keeps its never-raise contract. Provenance: docs/STANDARDIZE.md."""

    def test_ported_builder_survives_a_symlink_escaping_the_tree(self, tmp_path):
        # The approved divergence from the verbatim port (replaces the retired
        # byte-identical-to-HEAD freeze): an artifact that is a symlink resolving
        # OUTSIDE the product tree has no tree-relative path. build_graph must keep
        # its never-raise contract and surface the escapee as a parse_error.
        import shutil
        import sys
        scripts = _HARNESS / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from conftest import VALID
        import spec_graph
        proj = tmp_path / "proj"
        shutil.copytree(VALID, proj)
        real = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
        outside = tmp_path / "outside-story.md"
        shutil.copy(real, outside)
        link = proj / "docs" / "product" / "stories" / "PRD-LINK.md"
        link.symlink_to(outside)
        graph = spec_graph.build_graph(proj)  # must NOT raise
        assert any("PRD-LINK.md" in pe["file"] for pe in graph["parse_errors"]), (
            "the symlink escaping the product tree must surface as a parse_error: %s"
            % graph["parse_errors"])

    def test_ported_builder_does_not_import_the_shared_core(self):
        # Importing the shared core into the frozen port would couple them and a
        # later core change would ripple into the port's test surface.
        src = (_HARNESS / "scripts" / "spec_graph.py").read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*(?:from|import)\s+graph_core\b", src), (
            "the ported builder must not import the shared graph core")

    def test_shared_core_does_not_import_a_ported_domain_builder(self):
        # The generic core must not reach back into a domain builder; it
        # reproduces the shared semantics independently.
        src = (_HARNESS / "scripts" / "graph_core.py").read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*(?:from|import)\s+spec_graph\b", src), (
            "the shared graph core must not import the ported domain builder")


class TestSkillTierDiscipline:
    """Every harness-owned skill must declare a compliance-tier the governance
    model recognizes — an unknown or absent tier is a field that enforces
    nothing. catalog.tier_problems() is the single source of truth."""

    def test_all_owned_skills_declare_a_valid_compliance_tier(self):
        import sys
        scripts = _HARNESS / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import catalog
        problems = catalog.tier_problems(_HARNESS / "plugins" / "hs" / "skills")
        assert not problems, (
            "owned skills must declare a valid compliance-tier "
            "(workflow|gate|telemetry|knowledge):\n%s" % "\n".join(problems))


class TestStoreWriteDiscipline:
    def test_state_writes_go_through_shared_writers(self):
        # Only the designated writer modules may open files under the state
        # tree: everyone else calls their append_event APIs. An ad-hoc write
        # bypasses actor enrichment + the append-only discipline.
        allowed = {"trace_log.py", "telemetry_paths.py", "session_init.py",
                   "hook_runtime.py", "harness_paths.py"}
        suspicious = re.compile(
            r"open\([^)]*(?:trace|telemetry|sessions)[^)]*[\"'](?:w|a)")
        offenders = []
        for p in _py_files():
            if p.name in allowed or p.parent.name == "tests":
                continue
            if suspicious.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p))
        assert not offenders, "ad-hoc state writes outside writers: %s" % offenders


class TestClaimLifecycleIsRenameOnly:
    def test_claims_module_never_deletes_a_claim_file(self):
        # Reclaim-by-delete-then-recreate is the empirically proven
        # split-brain primitive (losers destroy the winner's fresh claim).
        # The claims store moves files exclusively by rename/link; any
        # deletion call appearing in the module is that bug class returning.
        src = (_HARNESS / "scripts" / "claims.py").read_text(encoding="utf-8")
        banned = re.compile(r"\bunlink\b|os\.remove\b|\.rmdir\(|shutil\.rmtree")
        offenders = ["claims.py:%d: %s" % (i, line.strip())
                     for i, line in enumerate(src.splitlines(), 1)
                     if banned.search(line)]
        assert not offenders, (
            "claims.py must move claim files only by rename/link, never "
            "delete them:\n%s" % "\n".join(offenders))


class TestWording:
    def test_write_guard_never_overclaims_its_reach(self):
        # The honest name is "tool-mediated config-edit gate": it sees
        # Write/Edit/MultiEdit tool calls only — not Bash redirects, not
        # editors outside the session. Phrases promising more would teach
        # operators to trust a fence that is not there. Negated forms
        # ("not tamper-proof", "chưa tamper-proof") are the honesty the
        # invariant exists to protect, so only the positive claim is banned.
        banned = re.compile(
            r"blocks?\s+(?:all|every|any)\s+writes?"
            r"|(?<!not )(?<!chưa )(?<!không )(?<!never )tamper[- ]proof",
            re.IGNORECASE)
        offenders = []
        for p in _all_text_files():
            if p.name == "test_bug_class_invariants.py" or "standards" in p.parts:
                continue
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if banned.search(line):
                    offenders.append("%s:%d" % (p, i))
        assert not offenders, (
            "write_guard is a tool-mediated config-edit gate — never "
            "describe it as blocking all writes or as tamper-proof:\n%s"
            % "\n".join(offenders))

    def test_fs_guard_is_never_called_a_write_fence(self):
        # The honest name is "script-path containment helper": it disciplines
        # harness scripts, it cannot stop a raw LLM Write. "write-fence"
        # promises more than the mechanism delivers.
        banned = re.compile(r"write[- ]fence", re.IGNORECASE)
        offenders = []
        for p in _all_text_files():
            if p.name == "test_bug_class_invariants.py":
                continue  # this file names the banned term to ban it
            if "standards" in p.parts:
                # standards/ holds user-supplied input docs, not harness
                # prose — they may legitimately state the ban itself.
                continue
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if banned.search(line):
                    offenders.append("%s:%d" % (p, i))
        assert not offenders, (
            "fs_guard must be described as a script-path containment helper, "
            "never a write-fence:\n%s" % "\n".join(offenders))


class TestDecisionRegisterIntegrity:
    """The register allocates monotonic ids and append-time blocks a duplicate.
    But a git merge can still land two records carrying the same id without any
    append running, and ids are never reused — so the committed register must
    carry no duplicate id. This re-checks what append-time cannot see."""

    def test_decision_ids_are_unique(self):
        register = _HARNESS.parent / "docs" / "decisions.md"
        if not register.is_file():
            return  # no register yet — nothing to guard
        text = register.read_text(encoding="utf-8")
        ids = re.findall(r"(?m)^id:\s*(DEC-\d+)\s*$", text)
        seen, dups = set(), []
        for dec_id in ids:
            if dec_id in seen and dec_id not in dups:
                dups.append(dec_id)
            seen.add(dec_id)
        assert not dups, "duplicate decision ids in register: %s" % ", ".join(dups)


def test_every_declared_hook_class_is_valid():
    """The 3 valid HOOK_CLASS values drive fail-open (telemetry/nudge) vs fail-closed
    (compliance) posture. A typo'd class (e.g. "complaince") would silently miss the
    compliance wrapper and fail OPEN — a security regression. Lock the vocabulary so a
    new or edited hook cannot ship an unrecognized class."""
    valid = {"telemetry", "nudge", "compliance"}
    bad = []
    for p in _py_files("hooks"):
        m = re.search(r'(?m)^HOOK_CLASS\s*=\s*["\'](\w+)["\']',
                      p.read_text(encoding="utf-8"))
        if m and m.group(1) not in valid:
            bad.append("%s=%s" % (p.name, m.group(1)))
    assert not bad, "invalid HOOK_CLASS values (not in %s): %s" % (sorted(valid), bad)
