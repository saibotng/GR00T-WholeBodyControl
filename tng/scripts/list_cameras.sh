#!/usr/bin/env bash
# List the cameras connected to this machine that the camera server can use (RealSense, OAK,
# USB/UVC), each with its native resolutions and an entry for gear_sonic/camera/camera_config.yaml.
# Run it where the cameras are plugged in, after tng/scripts/setup_camera_server.sh. A RealSense
# or OAK that the running camera server streams from can't be queried: stop the service first
# (sudo systemctl stop composed_camera_server).
#
#   tng/scripts/list_cameras.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "${1:-}" in -h | --help) sed -n '2,/^set /s/^# \{0,1\}//p' "$0" && exit 0 ;; esac
PY="$REPO/.venv_camera/bin/python"
[ -x "$PY" ] || {
    echo "The camera environment is missing: run tng/scripts/setup_camera_server.sh" >&2
    exit 1
}
exec "$PY" "$REPO/tng/scripts/list_cameras.py"
