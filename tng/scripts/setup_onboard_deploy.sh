#!/usr/bin/env bash
# Set up the robot's onboard computer (Jetson Orin, JetPack 6) to run SONIC's C++ controller for
# launch_*.py --deploy-host (tng/ONBOARD.md). Run it on the robot, in the clone of this fork at the
# commit the project pins (the camera server's clone). Idempotent; needs sudo and internet.
#   1. JetPack 6 check, power mode MAXN (sudo nvpmodel -m 0)
#   2. jetson_clocks without a password: deploy.sh runs `sudo jetson_clocks` on every start, which
#      would otherwise wait for a password in the controller pane
#   3. DLA packages, git-lfs, rsync
#   4. TensorRT 10.7 (checked only: the TAR needs an NVIDIA login) and TensorRT_ROOT in ~/.bashrc
#   5. the controller's Git LFS files, its dependencies (gear_sonic_deploy/scripts/install_deps.sh)
#      and its build (just build)
#   6. the readiness check (tng/scripts/check_onboard.sh)
# The SONIC models come from the workstation (tng/scripts/sync_models_to_robot.sh). The first
# controller start builds the TensorRT engines; the planner alone takes several minutes.
#
#   tng/scripts/setup_onboard_deploy.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "${1:-}" in -h | --help) sed -n '2,/^set /s/^# \{0,1\}//p' "$0" && exit 0 ;; esac
DEPLOY="$REPO/gear_sonic_deploy"
TRT="${TensorRT_ROOT:-$HOME/TensorRT}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2 && exit 1; }

grep -qs R36 /etc/nv_tegra_release || die "JetPack 6 (L4T R36) required: docs/source/references/jetpack6.md"

log "Power mode MAXN"
nvpmodel -q 2>/dev/null | grep -q MAXN || sudo nvpmodel -m 0

log "jetson_clocks without a password"
RULE=/etc/sudoers.d/sonic-jetson-clocks
if ! sudo test -f "$RULE"; then
    echo "$USER ALL=(root) NOPASSWD: $(command -v jetson_clocks)" | sudo tee "$RULE.new" >/dev/null
    sudo visudo -cf "$RULE.new" && sudo chmod 440 "$RULE.new" && sudo mv "$RULE.new" "$RULE"
fi

log "Packages"
PACKAGES=(nvidia-l4t-dla-compiler libcudla-dev-12-6 git-lfs rsync)
dpkg -s "${PACKAGES[@]}" >/dev/null 2>&1 || sudo apt-get install -y "${PACKAGES[@]}"

log "TensorRT 10.7"
header="$(find -L "$TRT" -name NvInferVersion.h 2>/dev/null | head -1)"
version="$(awk '$1 == "#define" && $2 ~ /^NV_TENSORRT_(MAJOR|MINOR)$/ {v = v (v ? "." : "") $3} END {print v}' \
    "${header:-/dev/null}")"
[ "$version" = 10.7 ] || die "TensorRT 10.7 not found in $TRT (found: ${version:-none}). Download the
TensorRT 10.7 TAR for JetPack 6 / L4T aarch64 (https://developer.nvidia.com/tensorrt/download/10x,
NVIDIA login), extract it to ~/TensorRT, then run this script again."
grep -qs 'TensorRT_ROOT=' ~/.bashrc || echo "export TensorRT_ROOT=$TRT" >>~/.bashrc

log "Controller: Git LFS files, dependencies, build"
git -C "$REPO" lfs install --local >/dev/null
git -C "$REPO" lfs pull --include="gear_sonic_deploy/**" || die "Git LFS pull failed. Without internet,
copy the files from the workstation's clone (from g1-sonic-vla):
  rsync -a --exclude=target/ --exclude='*.trt' third_party/GR00T-WholeBodyControl/gear_sonic_deploy/ \
      <robot>:$REPO/gear_sonic_deploy/"
SDK="$DEPLOY/thirdparty/unitree_sdk2/lib/aarch64/libunitree_sdk2.a"
[ "$(stat -c %s "$SDK")" -gt 1000000 ] || die "$SDK is still an LFS placeholder"
command -v just cmake clang >/dev/null || bash "$DEPLOY/scripts/install_deps.sh"
(
    cd "$DEPLOY"
    export TensorRT_ROOT="$TRT"
    set +eu # like deploy.sh: setup_env.sh is not written for strict mode
    source scripts/setup_env.sh
    set -eu
    just build
)

log "Readiness"
bash "$REPO/tng/scripts/check_onboard.sh"
