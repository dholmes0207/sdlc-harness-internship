"""bun/deno publish must be detected as a ship stage (gate-surface parity)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness" / "scripts"))
import stage_detector as sd  # noqa: E402


def test_bun_publish_is_ship():
    assert sd.detect_stage("bun publish") == "ship"


def test_deno_publish_is_ship():
    assert sd.detect_stage("deno publish") == "ship"


def test_bun_publish_with_flags_is_ship():
    assert sd.detect_stage("bun publish --access public") == "ship"


def test_bun_install_is_not_ship():
    assert sd.detect_stage("bun install") is None


def test_quoted_bun_publish_is_not_a_head():
    # inside a quoted argument it is text, not a command head
    assert sd.detect_stage('echo "bun publish"') is None


def test_publish_with_flag_before_verb_is_not_a_bypass():
    # a flag/arg between the tool and the verb must NOT evade the gate
    assert sd.detect_stage("npm --registry http://x publish") == "ship"
    assert sd.detect_stage("pnpm --foo bar publish") == "ship"
    assert sd.detect_stage("cargo --locked publish") == "ship"


def test_deploy_with_flag_before_verb_is_not_a_bypass():
    assert sd.detect_stage("vercel --prod deploy") == "deploy"
    assert sd.detect_stage("wrangler --env prod deploy") == "deploy"


def test_pr_with_flag_before_verb_is_not_a_bypass():
    assert sd.detect_stage("gh --repo o/r pr create") == "pr"


def test_unrelated_install_is_not_ship():
    assert sd.detect_stage("npm install lodash") is None


def test_line_continuation_is_not_a_bypass():
    # SD-1: a backslash-newline between tool and verb must not slip the gate
    assert sd.detect_stage("git \\\npush origin main") == "push"
    assert sd.detect_stage("npm \\\npublish") == "ship"
    assert sd.detect_stage("vercel \\\ndeploy --prod") == "deploy"
    assert sd.detect_stage("gh \\\npr create") == "pr"


def test_quoted_verb_is_not_a_bypass():
    # SD-2: the shell strips the quotes, so the verb still runs
    assert sd.detect_stage('npm "publish"') == "ship"
    assert sd.detect_stage("npm 'publish'") == "ship"


def test_exec_eval_prefix_is_not_a_bypass():
    # SD-3: exec/eval run the following command
    assert sd.detect_stage("exec git push origin main") == "push"
    assert sd.detect_stage("eval git push origin main") == "push"
    assert sd.detect_stage("eval npm publish") == "ship"


import pytest  # noqa: E402

# SD-M1/M2: container-registry image push (ship) + infra-deploy CLIs (deploy) must
# fire the ship-class gates, not slip them (the in-session secret-scan/plan-approval
# gates only fire on a recognized ship/deploy stage).
_CONTAINER_SHIP = [
    "docker push myimg:1", "podman push reg/img", "docker buildx build -t x --push .",
    "helm push chart.tgz oci://reg", "skopeo copy a b",
    "mvn deploy", "gradle publish", "goreleaser release",
]
_INFRA_DEPLOY = [
    "terraform apply", "terraform apply -auto-approve", "kubectl apply -f x.yaml",
    "helm install r chart", "helm upgrade r chart", "serverless deploy",
    "gcloud app deploy", "aws deploy create-deployment", "eb deploy",
]
_BENIGN_NONE = [
    "docker build .", "docker pull img", "kubectl get pods", "terraform plan",
    "helm list", "aws s3 ls", "gcloud auth login", "mvn test", "kubectl describe pod x",
]


@pytest.mark.parametrize("cmd", _CONTAINER_SHIP)
def test_container_registry_push_is_ship(cmd):
    assert sd.detect_stage(cmd) == "ship", cmd


@pytest.mark.parametrize("cmd", _INFRA_DEPLOY)
def test_infra_deploy_clis_are_deploy(cmd):
    assert sd.detect_stage(cmd) == "deploy", cmd


@pytest.mark.parametrize("cmd", _BENIGN_NONE)
def test_benign_tool_subcommands_not_gated(cmd):
    assert sd.detect_stage(cmd) is None, cmd


# transparent exec wrappers must not let a ship/deploy verb slip (no transport
# backstop exists for ship/deploy — the in-session gate is their only gate).
@pytest.mark.parametrize("cmd", [
    "setsid npm publish", "doas cargo publish", "chronic npm publish",
    "ifne npm publish", "flock /tmp/l npm publish", "flock -x /tmp/l cargo publish",
    "flock -w 5 /tmp/l npm publish", "setsid vercel deploy", "flock /tmp/l terraform apply",
])
def test_transparent_exec_wrappers_do_not_slip(cmd):
    assert sd.detect_stage(cmd) in ("ship", "deploy"), cmd


@pytest.mark.parametrize("cmd", ["flock /tmp/l ls", "setsid ls -la", "doas whoami"])
def test_wrappers_over_benign_commands_not_gated(cmd):
    assert sd.detect_stage(cmd) is None, cmd
