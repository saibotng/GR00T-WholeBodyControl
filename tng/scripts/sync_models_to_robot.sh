#!/bin/bash
# Copy the deploy's ONNX models (policy/ + planner/) from this workstation to the robot's
# checkout, for running the C++ deploy onboard (launch_*.py --deploy-host).
#
#   tng/scripts/sync_models_to_robot.sh <ssh-host> [remote-repo] [--dry-run]
#   e.g. tng/scripts/sync_models_to_robot.sh g1new
#
# Only *.onnx files are copied. TensorRT engines (*.trt) are built per GPU and stay
# out: the robot builds its own on the first deploy start, which can take minutes.
# The git-tracked observation_config yamls come with `git pull` on the robot instead,
# so the robot's checkout stays clean.
# Needs rsync on both machines. Nothing is deleted on the robot.
set -euo pipefail

if [[ $# -lt 1 || "$1" == -h || "$1" == --help ]]; then
    sed -n '2,12p' "$0"; exit 1
fi
HOST="$1"; shift
REMOTE_REPO="~/tng/GR00T-WholeBodyControl"
if [[ $# -gt 0 && "$1" != --* ]]; then REMOTE_REPO="$1"; shift; fi
DRY=()
if [[ "${1:-}" == "--dry-run" ]]; then DRY=(--dry-run); fi

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../gear_sonic_deploy" && pwd)"
cd "$DEPLOY_DIR"

echo "Syncing ONNX models: $DEPLOY_DIR/{policy,planner} -> $HOST:$REMOTE_REPO/gear_sonic_deploy/"
# -L: copy the files symlinks point to (models are often linked into a checkout)
rsync -avL "${DRY[@]}" --prune-empty-dirs \
    --include='*/' --include='*.onnx' --exclude='*' \
    policy planner "$HOST:$REMOTE_REPO/gear_sonic_deploy/"
echo "Done. Verify: launch_*.py --deploy-host $HOST --dry-run (compares sha256)."
