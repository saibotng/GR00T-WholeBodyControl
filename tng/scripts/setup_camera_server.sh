#!/usr/bin/env bash
# Set up the camera server on the robot's onboard computer, from a clone of this fork at the
# commit the project pins; nothing else is needed. Idempotent: rerun after checking out another
# commit. Needs uv, Internet and sudo (udev rules, systemd service).
#   1. RealSense udev rules (camera access without root)
#   2. .venv_camera: gear_sonic[camera] (depthai for OAK) plus pyrealsense2 and pyyaml, on
#      Python 3.10 (pyrealsense2 has no aarch64 wheel for 3.11, and its 3.12 wheels need
#      glibc 2.38, newer than the robot's Ubuntu 22.04); rebuilt when the checked-out commit
#      changes
#   3. the connected cameras, each with a config entry (tng/scripts/list_cameras.sh)
#   4. systemd service composed_camera_server: gear_sonic.camera.run_from_config with
#      gear_sonic/camera/camera_config.yaml; started once the config is complete
#
#   tng/scripts/setup_camera_server.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "${1:-}" in -h | --help) sed -n '2,/^set /s/^# \{0,1\}//p' "$0" && exit 0 ;; esac
cd "$REPO"
PY="$REPO/.venv_camera/bin/python"
CONFIG="$REPO/gear_sonic/camera/camera_config.yaml"
UNIT=/etc/systemd/system/composed_camera_server.service
RULES=/etc/udev/rules.d/99-realsense-libusb.rules

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
command -v uv >/dev/null || {
    echo "uv is required: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
}

if [ ! -f "$RULES" ]; then
    log "RealSense udev rules (sudo)"
    sudo curl -fsSL -o "$RULES" \
        https://raw.githubusercontent.com/IntelRealSense/librealsense/v2.59.1/config/99-realsense-libusb.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger
fi

COMMIT="$(git rev-parse HEAD)"
if [ ! -x "$PY" ] || [ "$(cat .venv_camera/commit 2>/dev/null)" != "$COMMIT" ]; then
    log "Camera environment .venv_camera"
    uv venv --clear --python 3.10 --prompt gear_sonic_camera .venv_camera
    uv pip install --python "$PY" -e "$REPO/gear_sonic[camera]" pyrealsense2 pyyaml
    echo "$COMMIT" >.venv_camera/commit
fi

log "Connected cameras"
"$PY" tng/scripts/list_cameras.py || echo "(could not list the cameras)"

log "systemd service composed_camera_server (sudo)"
sudo tee "$UNIT" >/dev/null <<UNIT
[Unit]
Description=Robot camera server (SONIC composed camera, $CONFIG)
After=network.target

[Service]
Type=simple
User=$USER
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$REPO
ExecStart=$PY -m gear_sonic.camera.run_from_config --config $CONFIG
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable composed_camera_server
if "$PY" -m gear_sonic.camera.run_from_config --config "$CONFIG" --dry-run; then
    sudo systemctl restart composed_camera_server
    log "Camera server running. Logs: journalctl -u composed_camera_server -f"
else
    log "Not started: fill in $CONFIG (cameras above), then: sudo systemctl restart composed_camera_server"
fi
