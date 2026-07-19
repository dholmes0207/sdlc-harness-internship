"""test_artifact_stamp.py — provenance frontmatter stamping (pure function).

stamp_markdown injects harness_version / harness_kit_digest / harness_schema_version
into a markdown file's YAML frontmatter (creating the block if absent), merging
into existing frontmatter without touching other keys or the body. It carries NO
wall-clock field, so stamping the same text twice is byte-identical — the hook
that calls it can safely re-fire on every write.
"""
import artifact_stamp


T = "1.2.3"
D = "deadbeef" * 8  # 64-hex-ish digest stand-in


class TestNoFrontmatter:
    def test_prepends_frontmatter_block(self):
        out = artifact_stamp.stamp_markdown("# Report\n\nbody line\n", T, D)
        assert out.startswith("---\n")
        assert f"harness_version: {T}" in out
        assert f"harness_kit_digest: {D}" in out
        assert "harness_schema_version: 1.0" in out
        # body preserved verbatim after the block
        assert out.endswith("# Report\n\nbody line\n")

    def test_body_and_evidence_unchanged(self):
        body = "# X\n\nSee `foo.py:42` and `abc123` SHA.\n\n```py\nx=1\n```\n"
        out = artifact_stamp.stamp_markdown(body, T, D)
        assert out.endswith(body)  # nothing inside the body rewritten


class TestExistingFrontmatter:
    def test_merges_without_clobbering(self):
        src = "---\ntitle: Foo\nstatus: completed\n---\n\n# Body\n"
        out = artifact_stamp.stamp_markdown(src, T, D)
        assert "title: Foo" in out
        assert "status: completed" in out
        assert f"harness_version: {T}" in out
        assert out.endswith("# Body\n")

    def test_updates_existing_stamp_in_place(self):
        once = artifact_stamp.stamp_markdown("# B\n", "1.0.0", "old")
        twice = artifact_stamp.stamp_markdown(once, "2.0.0", "new")
        assert "harness_version: 2.0.0" in twice
        assert "harness_version: 1.0.0" not in twice
        assert "harness_kit_digest: new" in twice
        assert "harness_kit_digest: old" not in twice
        # exactly one occurrence of each stamp key
        assert twice.count("harness_version:") == 1
        assert twice.count("harness_kit_digest:") == 1


class TestIdempotent:
    def test_stamp_twice_is_byte_identical_no_frontmatter(self):
        once = artifact_stamp.stamp_markdown("# Report\n\nbody\n", T, D)
        twice = artifact_stamp.stamp_markdown(once, T, D)
        assert once == twice

    def test_stamp_twice_is_byte_identical_with_frontmatter(self):
        src = "---\ntitle: Foo\n---\n\n# Body\n"
        once = artifact_stamp.stamp_markdown(src, T, D)
        twice = artifact_stamp.stamp_markdown(once, T, D)
        assert once == twice


def _fence_lines(text: str) -> int:
    """Count of `---` delimiter lines (universal-newline aware). A correctly
    stamped file has exactly two; a doubled-frontmatter bug yields four."""
    return sum(1 for ln in text.splitlines() if ln.strip() == "---")


class TestCRLFFrontmatter:
    """A Windows-authored artifact has CRLF frontmatter (`---\\r\\n`). It must be
    MERGED, not get a second LF `---` block prepended above it."""

    def test_crlf_frontmatter_merges_not_doubled(self):
        src = "---\r\ntitle: Foo\r\nstatus: completed\r\n---\r\n\r\n# Body\r\n"
        out = artifact_stamp.stamp_markdown(src, T, D)
        assert _fence_lines(out) == 2          # single merged block, not doubled
        assert "title: Foo" in out             # original keys kept
        assert "status: completed" in out
        assert f"harness_version: {T}" in out  # stamp merged in
        assert "# Body\r\n" in out             # CRLF body preserved verbatim

    def test_crlf_idempotent(self):
        src = "---\r\ntitle: Foo\r\n---\r\n# Body\r\n"
        once = artifact_stamp.stamp_markdown(src, T, D)
        twice = artifact_stamp.stamp_markdown(once, T, D)
        assert once == twice
        assert _fence_lines(once) == 2

    def test_crlf_updates_existing_stamp_in_place(self):
        once = artifact_stamp.stamp_markdown("---\r\ntitle: X\r\n---\r\nb\r\n",
                                             "1.0.0", "old")
        twice = artifact_stamp.stamp_markdown(once, "2.0.0", "new")
        assert twice.count("harness_version:") == 1
        assert "harness_version: 2.0.0" in twice
        assert "harness_version: 1.0.0" not in twice


class TestEmptyFrontmatter:
    """An empty frontmatter block (`---\\n---`) has a closing fence immediately
    after the opening one. It must be recognized and filled, not doubled."""

    def test_empty_frontmatter_with_body_merges(self):
        out = artifact_stamp.stamp_markdown("---\n---\n# Body\n", T, D)
        assert _fence_lines(out) == 2
        assert f"harness_version: {T}" in out
        assert out.endswith("# Body\n")

    def test_empty_frontmatter_no_body(self):
        out = artifact_stamp.stamp_markdown("---\n---\n", T, D)
        assert _fence_lines(out) == 2
        assert f"harness_version: {T}" in out

    def test_empty_frontmatter_at_eof_without_trailing_newline(self):
        out = artifact_stamp.stamp_markdown("---\n---", T, D)
        assert _fence_lines(out) == 2
        assert f"harness_version: {T}" in out

    def test_empty_frontmatter_idempotent(self):
        once = artifact_stamp.stamp_markdown("---\n---\n# Body\n", T, D)
        twice = artifact_stamp.stamp_markdown(once, T, D)
        assert once == twice
