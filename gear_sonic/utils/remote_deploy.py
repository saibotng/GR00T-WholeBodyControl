"""Run the C++ deploy (gear_sonic_deploy/deploy.sh) on the robot's onboard computer.

Used by launch_data_collection.py and launch_inference.py when ``--deploy-host`` is set.
Then only the deploy runs on the robot. Teleop, the VLA client and the data
exporter stay on the workstation and talk to it over ZMQ:

    workstation  --pose/tokens :5556-->  robot deploy  (deploy connects to <workstation ip>)
    workstation  <--state  :5557------   robot deploy  (deploy binds *:5557)

The deploy pane runs ``ssh -t <host> ... ./deploy.sh ...``. Keys typed in the pane
(deploy.sh confirmation, k/i/p, O = e-stop) go through the SSH session, and so
does Ctrl+C. If the session ends, the remote shell is sent SIGHUP.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shlex
import subprocess

DEFAULT_REMOTE_REPO = "~/tng/GR00T-WholeBodyControl"

# The pane's ssh client gives up after ~3 s without an answer from the robot, so a dead
# link shows up in the pane quickly. This only ends the LOCAL ssh client: the robot's
# sshd may not notice for a long time, and until then the deploy keeps running there.
SSH_OPTIONS = ("-o", "ServerAliveInterval=1", "-o", "ServerAliveCountMax=3")


@dataclass
class RemoteDeploy:
    host: str
    """SSH destination (alias from ~/.ssh/config or user@ip)."""

    remote_repo: str = DEFAULT_REMOTE_REPO
    """GR00T-WholeBodyControl checkout on the robot."""

    def ip(self) -> str:
        """IP the SSH destination resolves to (follows ~/.ssh/config aliases)."""
        return resolve_ssh_host_ip(self.host)

    def run(self, cmd: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
        """Run a non-interactive command on the robot (BatchMode, no tty)."""
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self.host, cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def resolve_ssh_host_ip(host: str) -> str:
    """Hostname that ``ssh <host>`` would connect to, per ``ssh -G`` (user@ part dropped)."""
    out = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname":
            return value.strip()
    return host.split("@")[-1]


def local_ip_towards(remote_ip: str) -> str:
    """Source IP this machine uses to reach ``remote_ip``. The deploy connects to it."""
    out = subprocess.run(
        ["ip", "-4", "route", "get", remote_ip], capture_output=True, text=True, check=True
    ).stdout
    tokens = out.split()
    if "src" in tokens:
        return tokens[tokens.index("src") + 1]
    raise RuntimeError(f"no source address towards {remote_ip} in: {out.strip()!r}")


def deploy_model_files(checkpoint: str, obs_config: str, planner: str) -> list[str]:
    """Files deploy.sh loads (relative to gear_sonic_deploy/). Defaults mirror deploy.sh."""
    checkpoint = checkpoint or "policy/release/model"
    return [
        f"{checkpoint}_decoder.onnx",
        f"{checkpoint}_encoder.onnx",
        obs_config or "policy/release/observation_config.yaml",
        planner or "planner/target_vel/V2/planner_sonic.onnx",
    ]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_remote_models(
    remote: RemoteDeploy, local_deploy_dir: Path, rel_files: list[str]
) -> list[str]:
    """Compare model files workstation vs robot by sha256. Returns a list of problems.

    This guards the decoder trap: the decoder must match the one the dataset / VLA
    checkpoint was recorded with. With the deploy on the robot, the robot's copy is
    what runs, so the workstation copy alone proves nothing.
    """
    problems = []
    remote_dir = f"{remote.remote_repo}/gear_sonic_deploy"
    quoted = " ".join(shlex.quote(f) for f in rel_files)
    # ~ must stay unquoted so the remote shell expands it
    res = remote.run(f"cd {remote_dir} && sha256sum {quoted}", timeout=120.0)
    remote_sums = {}
    for line in res.stdout.splitlines():
        digest, _, name = line.partition("  ")
        remote_sums[name.strip()] = digest
    for rel in rel_files:
        local = local_deploy_dir / rel
        if not local.exists():
            problems.append(f"{rel}: missing on the workstation ({local})")
            continue
        if rel not in remote_sums:
            problems.append(f"{rel}: missing on the robot ({remote.host}:{remote_dir}/{rel})")
            continue
        if remote_sums[rel] != _sha256(local):
            problems.append(f"{rel}: differs between workstation and robot")
    if res.returncode != 0 and not remote_sums:
        problems.append(f"ssh {remote.host} failed: {res.stderr.strip() or res.stdout.strip()}")
    return problems


def check_remote_deploy_code(remote: RemoteDeploy, local_repo: Path) -> list[str]:
    """Compare the gear_sonic_deploy source tree (git tree hash) workstation vs robot.

    The robot builds and runs its own checkout. A different tree means different
    C++ than the workstation's. That is a warning, not an error.
    """
    def script(repo: str) -> str:
        # line 1: tree hash; a second line only if tracked files are modified
        return (
            f"cd {repo} && git rev-parse HEAD:gear_sonic_deploy && "
            f"git status --porcelain -- gear_sonic_deploy | grep -v '^??' | head -1"
        )

    def parse(res: subprocess.CompletedProcess) -> tuple[str, bool]:
        lines = res.stdout.splitlines()
        return (lines[0] if lines else ""), len(lines) > 1

    warnings = []
    local_tree, local_dirty = parse(
        subprocess.run(["bash", "-c", script(shlex.quote(str(local_repo)))],
                       capture_output=True, text=True)
    )
    remote_tree, remote_dirty = parse(remote.run(script(remote.remote_repo)))
    if not remote_tree:
        warnings.append(f"could not read the robot checkout {remote.host}:{remote.remote_repo}")
    elif local_tree != remote_tree:
        warnings.append(
            f"gear_sonic_deploy differs: workstation tree {local_tree[:10]}, robot {remote_tree[:10]} "
            f"(the robot runs its own checkout — pull it to the same commit)"
        )
    if remote_dirty:
        warnings.append("robot checkout has uncommitted changes in gear_sonic_deploy")
    if local_dirty:
        warnings.append("workstation checkout has uncommitted changes in gear_sonic_deploy")
    return warnings


def remote_deploy_pane_command(remote: RemoteDeploy, deploy_args: str, env: dict[str, str]) -> str:
    """Shell line for the deploy pane: ssh -t into the robot and run deploy.sh there.

    ``bash -ic`` so the robot's ~/.bashrc runs (TensorRT_ROOT, CUDA paths); a plain
    ``ssh host cmd`` is non-interactive and most .bashrc files return early then.
    """
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    inner = (
        f"cd {remote.remote_repo}/gear_sonic_deploy && "
        f"{env_prefix + ' ' if env_prefix else ''}./deploy.sh {deploy_args}"
    )
    remote_cmd = f"bash -ic {shlex.quote(inner)}"
    ssh = " ".join(["ssh", "-t", *SSH_OPTIONS, shlex.quote(remote.host)])
    return f"{ssh} {shlex.quote(remote_cmd)}"


def preflight(
    remote: RemoteDeploy,
    local_repo: Path,
    model_files: list[str],
    skip_model_check: bool,
) -> tuple[str, str]:
    """Resolve IPs and run the safety checks. Returns (robot_ip, workstation_ip).

    Raises SystemExit with a readable message on any hard failure.
    """
    try:
        robot_ip = remote.ip()
        workstation_ip = local_ip_towards(robot_ip)
    except (subprocess.CalledProcessError, RuntimeError) as e:
        raise SystemExit(f"ERROR: cannot resolve the route to --deploy-host {remote.host}: {e}")

    try:
        res = remote.run("true", timeout=15.0)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"ERROR: ssh {remote.host} timed out — is the robot connected?")
    if res.returncode != 0:
        raise SystemExit(
            f"ERROR: ssh {remote.host} failed (non-interactive, needs key auth): {res.stderr.strip()}"
        )

    res = remote.run(f"test -x {remote.remote_repo}/gear_sonic_deploy/deploy.sh")
    if res.returncode != 0:
        raise SystemExit(
            f"ERROR: {remote.host}:{remote.remote_repo}/gear_sonic_deploy/deploy.sh not found "
            f"(set --deploy-remote-repo)"
        )

    if skip_model_check:
        print("WARNING: --deploy-skip-model-check: robot model files NOT compared")
    else:
        problems = check_remote_models(remote, local_repo / "gear_sonic_deploy", model_files)
        if problems:
            msg = "\n".join(f"  - {p}" for p in problems)
            raise SystemExit(
                "ERROR: deploy model files on the robot do not match the workstation:\n"
                f"{msg}\n"
                "  Sync them: tng/scripts/sync_models_to_robot.sh "
                f"{remote.host} {remote.remote_repo}\n"
                "  (or --deploy-skip-model-check if the difference is intended)"
            )
        print(f"  Robot models:    match the workstation ({len(model_files)} files, sha256)")

    for w in check_remote_deploy_code(remote, local_repo):
        print(f"WARNING: {w}")

    return robot_ip, workstation_ip
