#!/usr/bin/env python3
"""task_store.py — provider-configurable task-store surface (ABC + factory).

The ONLY harness code that may talk to a remote task provider. It is never
imported on the gate path: gate hooks and artifact checking stay
network-free by invariant (a flaky provider must not be able to block or
unblock a push). Claims import it lazily, mirror best-effort, and treat the
local claim file as the source of truth.

Surface is deliberately four methods — get_task / list_tasks / add_comment /
whoami. No state mutation, no assignee writes: the remote store is a shared
noticeboard, the claim files are the coordination mechanism. Neither GitHub
nor GitLab offers usable compare-and-swap over the REST surface, so anything
beyond advisory read+comment would be last-write-wins dressed up as safety.

Error taxonomy (all subclass TaskStoreError, every message actionable):
  ConfigError   — config file/key trouble; names the file and the key.
  AuthError     — 401/403; names WHICH env var to fix, never its value.
  NotFound      — 404.
  RateLimited   — 429; carries retry_after_s parsed from Retry-After.
  Unavailable   — 5xx / network / timeout; carries the attempt count.

Config = harness/data/task-store.yaml (human-edited YAML). The token itself
NEVER appears in any config file — the file names the env var that holds it.
"""

import os
import re
from pathlib import Path

_CONFIG_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "task-store.yaml"


class TaskStoreError(Exception):
    """Base for everything the adapter can raise."""


class ConfigError(TaskStoreError):
    """task-store.yaml missing/malformed — message names file + key."""


class AuthError(TaskStoreError):
    """401/403 — message names the token ENV VAR, never the token."""


class NotFound(TaskStoreError):
    """404 — ref does not exist (or the token cannot see it)."""


class RateLimited(TaskStoreError):
    """429 — carries the provider's requested backoff."""

    def __init__(self, msg, retry_after_s=None):
        super().__init__(msg)
        self.retry_after_s = retry_after_s


class Unavailable(TaskStoreError):
    """5xx / timeout / network failure — carries the attempt count."""

    def __init__(self, msg, attempts=1):
        super().__init__(msg)
        self.attempts = attempts


class TaskStoreAdapter:
    """Provider surface. Implementations return normalized task dicts:
    {ref, title, state, body, labels, author, url} with `state` one of
    open|closed and `labels` a list of plain names."""

    def get_task(self, ref):
        raise NotImplementedError

    def list_tasks(self, filter=None):
        raise NotImplementedError

    def add_comment(self, ref, body):
        raise NotImplementedError

    def whoami(self):
        raise NotImplementedError


# --------------------------------------------------------------- sanitize ---

_COMMENT_STRIP = re.compile(r"[`*_\[\]()<>#!|~\x00-\x1f\x7f]")


def sanitize_comment_text(text, max_len=300) -> str:
    """Neutralize env-derived strings (actor!) before they enter a markdown
    comment other humans read: strip markdown metacharacters and control
    chars, turn @ into (at) so no user/team can be pinged or spoofed, cap
    length."""
    out = _COMMENT_STRIP.sub("", str(text))
    out = out.replace("@", "(at)")
    return out[:max_len]


# ----------------------------------------------------------------- config ---

def _config_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    raw = os.environ.get("HARNESS_TASK_STORE_CONFIG")  # test seam
    return Path(raw) if raw else _CONFIG_DEFAULT


def load_config(path=None) -> dict:
    """Parse task-store.yaml. Missing/malformed → ConfigError naming the file."""
    import yaml  # lazy — adapter import must not require PyYAML until used

    p = _config_path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            "task-store config missing at %s — create it with:\n"
            "  provider: github\n"
            "  github: {repo: owner/name, token_env: GITHUB_TOKEN}" % p)
    except Exception as e:
        raise ConfigError("task-store config %s unreadable: %s" % (p, e))
    if not isinstance(raw, dict) or not raw.get("provider"):
        raise ConfigError(
            "task-store config %s is malformed — needs a top-level "
            "`provider:` key" % p)
    return raw


def load_adapter(path=None) -> TaskStoreAdapter:
    """Factory: config → provider instance. Unknown provider / missing keys
    raise ConfigError naming file + key."""
    p = _config_path(path)
    cfg = load_config(path=p)
    provider = str(cfg["provider"]).strip().lower()
    if provider == "github":
        section = cfg.get("github")
        if not isinstance(section, dict):
            raise ConfigError(
                "key `github:` missing in %s — the provider section must "
                "exist (repo, token_env)" % p)
        if not section.get("repo"):
            raise ConfigError("key `github.repo` missing in %s" % p)
        if not section.get("token_env"):
            raise ConfigError(
                "key `github.token_env` missing in %s — name the env var "
                "that holds the personal access token (the token itself "
                "never goes in this file)" % p)
        merged = dict(section)
        merged["timeouts"] = cfg.get("timeouts") or {}
        import task_store_github
        return task_store_github.GitHubTaskStore(merged)
    if provider == "gitlab":
        section = cfg.get("gitlab")
        if not isinstance(section, dict):
            raise ConfigError(
                "key `gitlab:` missing in %s — the provider section must "
                "exist (project, token_env)" % p)
        if not section.get("project"):
            raise ConfigError(
                "key `gitlab.project` missing in %s — the group/name path "
                "(e.g. mygroup/myrepo)" % p)
        if not section.get("token_env"):
            raise ConfigError(
                "key `gitlab.token_env` missing in %s — name the env var that "
                "holds the access token (the token itself never goes in this "
                "file)" % p)
        merged = dict(section)
        merged["timeouts"] = cfg.get("timeouts") or {}
        import task_store_gitlab
        return task_store_gitlab.GitLabTaskStore(merged)
    raise ConfigError(
        "unknown `provider: %s` in %s — supported: github, gitlab" % (provider, p))


def load_adapter_optional(path=None):
    """None when the config file simply does not exist (mirroring is opt-in);
    a PRESENT but broken config still raises — misconfiguration must be
    loud, only absence is silent."""
    p = _config_path(path)
    if not p.is_file():
        return None
    return load_adapter(path=p)
