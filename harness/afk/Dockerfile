# ralph-sandbox-harness — the Ralph sandbox image with the harness guardrail's
# runtime dependency baked in.
#
# Derived (not forked) from the upstream Ralph sandbox. The harness rides into
# the container on the workspace bind-mount (harness/ + .claude/settings.json),
# so nothing harness-specific is baked here except the two things the mount
# cannot carry: the Python interpreter + PyYAML the compliance hooks need, and
# the HARNESS_ROOT env the hooks resolve against.
#
# Floating base tag is deliberate (no pin): the sandbox follows upstream so
# security updates land automatically. Compatibility is assured at runtime by
# the gate-live probe each run, not by a frozen version string — if an upstream
# update ever broke the in-loop gate, the probe surfaces it and the launch
# falls back rather than running a bare bypass. Break-glass: pin a digest via
# RALPH_IMAGE=...@sha256:... at launch.
FROM docker.io/daonhan/ralph-sandbox:latest

USER root

# python3-yaml / python3-pytest via apt (not pip) — Debian's externally-managed
# environment (PEP 668) rejects a global pip install, and apt is the right home
# for a dependency the gate treats as load-bearing.
RUN apt-get update \
    && apt-get install -y python3 python3-yaml python3-pytest \
    && rm -rf /var/lib/apt/lists/*

# Hooks are registered as `python3 $HARNESS_ROOT/harness/hooks/*.py`. Ralph
# bind-mounts the workspace at /home/agent/workspace and runs claude there with
# a fixed argv (no --settings), so baking the env is how the hooks resolve
# without touching that argv.
ENV HARNESS_ROOT=/home/agent/workspace

USER agent
