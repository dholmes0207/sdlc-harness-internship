"""test_stage_detector.py — command → SDLC stage detection table.

Boundary-strict by design: a pattern only matches at a command
boundary (start of string or after ;, &, |, (, newline). Consequences the
table pins down: `echo "git push"` does NOT match (string in an argument, not
a command head). A wrapper AT a command head IS unwrapped — `sh -c 'git push'`
and `eval '...'` are recursed, and a path-qualified binary (`/usr/bin/git`) is
still that binary; but a wrapper INSIDE a quoted argument stays invisible
(over-detection toward false-block is the safe direction, never a missed real
stage). The transport pre-push hook still backstops push regardless of spelling.
Free-floating ship/release words only surface through guess_stage (advisory
sampling, never a gate signal).
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stage_detector import detect_stage, guess_stage  # noqa: E402


POSITIVE = [
    ("git push", "push"),
    ("git push origin main", "push"),
    ("git -C /repo push", "push"),
    ("git --no-pager push", "push"),
    ("cd x && git push", "push"),
    ("make build; git push", "push"),
    ("(git push)", "push"),
    ("echo hi | git push", "push"),  # after a pipe boundary it IS a command head
    ("git commit -m 'x'", "commit"),
    ("git -C /repo commit -am wip", "commit"),
    ("gh pr create --fill", "pr"),
    ("gh pr merge 42", "pr"),
    ("gh pr ready", "pr"),
    ("gh release create v1.0.0", "ship"),
    ("npm publish", "ship"),
    ("pnpm publish --access public", "ship"),
    ("wrangler deploy", "deploy"),
    ("vercel deploy --prod", "deploy"),
    ("netlify deploy", "deploy"),
    ("firebase deploy", "deploy"),
    ("fly deploy", "deploy"),
    ("railway deploy", "deploy"),
    ("npm run deploy", "deploy"),
    ("yarn deploy", "deploy"),
    # Benign prefixes that keep the verb a real command head: variable
    # assignments (quoted values included) and exec-style wrappers. These all
    # genuinely RUN the stage command, so the gate must see them.
    ("FOO=bar git push", "push"),
    ("GIT_SSH_COMMAND='ssh -i key' git push", "push"),
    ('GIT_TRACE="1" git push origin main', "push"),
    ("sudo git push", "push"),
    ("sudo -u deploy git push", "push"),
    ("command git push", "push"),
    ("time git push", "push"),
    ("nohup git push &", "push"),
    ("nice -n 10 git push", "push"),
    ("stdbuf -o0 git push", "push"),
    ("timeout 30 git push", "push"),
    ("env GIT_SSH=x git push", "push"),
    ("env -i PATH=/bin git push", "push"),
    ("xargs git push", "push"),
    ("sudo env FOO=1 git push", "push"),
    ("CI=1 gh pr create --fill", "pr"),
    ("HTTPS_PROXY=proxy:8080 gh pr create", "pr"),
    ("CI=1 npm publish", "ship"),
    ("sudo npm publish", "ship"),
    ("VAR=1 wrangler deploy", "deploy"),
    # Command positions the original boundary class missed: backticks and
    # brace groups execute their body; loop/conditional bodies after
    # do/then/else are command heads too.
    ("`git push`", "push"),
    ("echo before `git push` after", "push"),
    ("{ git push; }", "push"),
    ("for t in a b; do git push; done", "push"),
    ("if true; then git push; fi", "push"),
    # Path-qualified binaries: an absolute / relative path to the tool is still
    # that tool (defense-in-depth hardening, not a new gate layer).
    ("/usr/bin/git push", "push"),
    ("./gh pr create", "pr"),
    ("/usr/local/bin/vercel deploy", "deploy"),
    # Wrapper unwrap: sh -c / eval hide the real head in a quoted payload. The
    # transport pre-push hook still backstops push; this adds in-session reach.
    ("sh -c 'git push'", "push"),
    ('bash -c "gh pr merge"', "pr"),
    ("eval 'npm publish'", "ship"),
    ("sh -c 'sh -c \"git push\"'", "push"),   # nested wrapper, recursed
]

NEGATIVE = [
    'echo "git push"',          # argument string, not a command head
    "echo 'npm publish'",
    "git pushing",              # word boundary: not the push subcommand
    "git push-helper",
    "cat docs/release-notes.md",
    "grep release config.yaml",
    'echo "sh -c \'git push\'"',  # wrapper INSIDE a quoted arg → not a head
    "ls -la",
    "python3 -m pytest harness/tests/ -q",
    "FOO=git push",             # assignment swallows the verb; this runs `push`
    "xargs echo git push",      # xargs runs echo — the verb is data, not a head
    "sudoku git push",          # wrapper names match whole words only
    "dosomething git push",     # `do` keyword matches whole words only
]


class TestDetectStage:
    @pytest.mark.parametrize("command,stage", POSITIVE)
    def test_positive(self, command, stage):
        assert detect_stage(command) == stage

    @pytest.mark.parametrize("command", NEGATIVE)
    def test_negative_boundary_strict(self, command):
        assert detect_stage(command) is None

    # npx/bunx/dlx run the following tool, so a deploy/ship CLI behind them is a
    # real (and otherwise ungated — no transport backstop) deploy/ship.
    @pytest.mark.parametrize("command,stage", [
        ("npx vercel deploy", "deploy"),
        ("bunx wrangler deploy", "deploy"),
        ("npx -y netlify deploy", "deploy"),
        ("pnpm dlx vercel deploy", "deploy"),
        ("yarn dlx wrangler deploy", "deploy"),
    ])
    def test_npx_style_wrappers_detected(self, command, stage):
        assert detect_stage(command) == stage

    # Polyglot package release verbs beyond the npm family — each unambiguously
    # ships a package, so the in-session gate (the only gate for these — no git
    # transport) must require the ship artifacts.
    @pytest.mark.parametrize("command,stage", [
        ("cargo publish", "ship"),
        ("poetry publish --build", "ship"),
        ("twine upload dist/*", "ship"),
        ("gem push pkg-1.0.gem", "ship"),
        ("dotnet nuget push pkg.nupkg", "ship"),
    ])
    def test_polyglot_publish_detected(self, command, stage):
        assert detect_stage(command) == stage

    def test_empty_and_none_safe(self):
        assert detect_stage("") is None
        assert detect_stage(None) is None


class TestGuessStage:
    def test_free_floating_release_word_guesses_ship(self):
        assert guess_stage("cat docs/release-notes.md") == "ship"
        assert guess_stage("grep release config.yaml") == "ship"

    def test_free_floating_deploy_word_guesses_deploy(self):
        assert guess_stage("./scripts/deploy-helper.sh --dry-run") == "deploy"

    def test_sh_dash_c_payload_is_at_least_guessable(self):
        # The boundary-strict detector misses it; guess sampling still sees it
        # so the advisory trace collects evasion patterns for later analysis.
        assert guess_stage("sh -c 'npm publish'") == "ship"

    def test_plain_commands_yield_no_guess(self):
        assert guess_stage("ls -la") is None
        assert guess_stage('echo "git push"') is None  # push is not a guess word

    def test_detected_command_also_guessable_but_gate_prefers_detect(self):
        # guess_stage is sampling-only; the gate consults detect_stage first.
        assert detect_stage("wrangler deploy") == "deploy"
