#!/usr/bin/env bash
# Switch auto exposure on for every connected RealSense camera and print its state before and
# after (0 = off). A camera keeps a setting changed by another program (e.g. realsense-viewer)
# until it loses power, and the camera server sets none: with auto exposure off, a camera
# records too bright or too dark depending on the scene. Stops the camera server while it runs
# and starts it again afterwards (sudo).
#
#   tng/scripts/enable_auto_exposure.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "${1:-}" in -h | --help) sed -n '2,/^set /s/^# \{0,1\}//p' "$0" && exit 0 ;; esac
PY="$REPO/.venv_camera/bin/python"
SERVICE=composed_camera_server
[ -x "$PY" ] || {
    echo "The camera environment is missing: run tng/scripts/setup_camera_server.sh" >&2
    exit 1
}

if systemctl is-active --quiet "$SERVICE"; then
    sudo systemctl stop "$SERVICE"
    trap 'sudo systemctl start "$SERVICE" && echo "$SERVICE running again"' EXIT
fi

"$PY" - <<'EOF'
import pyrealsense2 as rs

devices = rs.context().query_devices()
if len(devices) == 0:
    print("no RealSense camera connected")
for device in devices:
    serial = device.get_info(rs.camera_info.serial_number)
    for sensor in device.query_sensors():
        if sensor.supports(rs.option.enable_auto_exposure):
            before = int(sensor.get_option(rs.option.enable_auto_exposure))
            sensor.set_option(rs.option.enable_auto_exposure, 1)
            after = int(sensor.get_option(rs.option.enable_auto_exposure))
            name = sensor.get_info(rs.camera_info.name)
            print(f"{serial} {name}: auto exposure {before} -> {after}")
EOF
