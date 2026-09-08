#!/usr/bin/env bash
# Install (or update) the composed_camera_server systemd unit — run ON THE MACHINE
# that has the cameras (the G1's Jetson, or a PC with the USB camera).
#
#   bash gear_sonic/scripts/install_camera_service.sh
#
# The unit execs gear_sonic.camera.run_from_config, which reads
# gear_sonic/camera/camera_config.yaml. Swapping/re-plugging a camera means
# editing THAT yaml and `sudo systemctl restart composed_camera_server` —
# this installer only needs to run again if the repo or venv path changes.
#
# Env overrides:  VENV=<python>  (default: <repo>/.venv_camera/bin/python)
#                 SERVICE=<name> (default: composed_camera_server)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${VENV:-$REPO/.venv_camera/bin/python}"
SERVICE="${SERVICE:-composed_camera_server}"
UNIT="/etc/systemd/system/$SERVICE.service"
CONFIG="$REPO/gear_sonic/camera/camera_config.yaml"

[ -x "$PY" ] || { echo "ERROR: $PY not found/executable (set VENV=...)"; exit 1; }
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG missing"; exit 1; }
"$PY" -c "import yaml" 2>/dev/null || { echo "Installing pyyaml into the venv..."; "$PY" -m pip install -q pyyaml; }

echo "Validating $CONFIG (device resolution runs on THIS machine):"
"$PY" -m gear_sonic.camera.run_from_config --config "$CONFIG" --dry-run

if [ -f "$UNIT" ]; then
    sudo cp "$UNIT" "$UNIT.bak-$(date +%y%m%d_%H%M%S)"
    echo "Backed up existing unit to $UNIT.bak-…"
fi

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Composed camera server (config: $CONFIG)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$PY -m gear_sonic.camera.run_from_config --config $CONFIG
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"
sleep 2
systemctl --no-pager -l status "$SERVICE" | head -12
echo
echo "Done. Camera changes from now on: edit $CONFIG, then: sudo systemctl restart $SERVICE"
