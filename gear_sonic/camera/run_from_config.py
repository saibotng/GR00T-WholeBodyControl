"""Launch the composed camera server from camera_config.yaml.

    python -m gear_sonic.camera.run_from_config [--config PATH] [--port N] [--dry-run]

Reads the per-view driver/device/rotate180 mapping (default:
gear_sonic/camera/camera_config.yaml next to this file), resolves each device
to something composed_camera accepts, prints the resolved plan, and execs

    python -m gear_sonic.camera.composed_camera --<view>-camera ... --<view>-device-id ...

so ps/systemd see the real server process. The systemd unit installed by
gear_sonic/scripts/install_camera_service.sh points here — swapping a camera
means editing the yaml and restarting the service, never the unit file.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

VIEWS = ("ego_view", "head", "left_wrist", "right_wrist")
DRIVERS = ("usb", "realsense", "oak", "oak_mono", "zed")


def _fail(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[camera-config] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _load(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        _fail("PyYAML missing in this venv — run:  pip install pyyaml")
    try:
        cfg = yaml.safe_load(path.read_text())
    except Exception as e:
        _fail(f"cannot parse {path}: {e}")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("views"), dict) or not cfg["views"]:
        _fail(f"{path}: need a top-level 'views:' mapping with at least one view")
    return cfg


def _resolve_usb(device: str) -> str:
    """Resolve a usb 'device' spec to an existing /dev node (kept as a path)."""
    device = str(device)
    if device.startswith("match:"):
        needle = device[len("match:"):].strip().lower()
        cands = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
        hits = [c for c in cands if needle in c.lower()]
        if not hits:
            _fail(f"no /dev/v4l/by-id/*-video-index0 matches '{needle}' — ls -l /dev/v4l/by-id/")
        return hits[0]
    if device.startswith("/dev/") or device.isdigit():
        if device.startswith("/dev/") and not os.path.exists(device):
            _fail(f"{device} does not exist — is the camera plugged into that port?")
        return device
    _fail(f"usb device must be match:<str>, /dev/... path, or an index (got {device!r})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=Path(__file__).with_name("camera_config.yaml"))
    ap.add_argument("--port", type=int, default=None, help="override the yaml port")
    ap.add_argument("--dry-run", action="store_true", help="print the composed_camera command and exit")
    args = ap.parse_args()

    cfg = _load(args.config)
    argv = [sys.executable, "-m", "gear_sonic.camera.composed_camera"]
    rotate: list[str] = []

    for view, spec in cfg["views"].items():
        if view not in VIEWS:
            _fail(f"unknown view '{view}' (expected one of {', '.join(VIEWS)})")
        if not spec:
            continue
        driver = spec.get("driver")
        if driver not in DRIVERS:
            _fail(f"{view}: driver must be one of {', '.join(DRIVERS)} (got {driver!r})")
        device = spec.get("device")
        if device in (None, ""):
            _fail(f"{view}: 'device' is required")
        device = _resolve_usb(device) if driver == "usb" else str(device)
        prefix = view.replace("_", "-")
        argv += [f"--{prefix}-camera", driver, f"--{prefix}-device-id", device]
        if spec.get("rotate180"):
            rotate.append(view)
        print(f"[camera-config] {view}: {driver} @ {device}" + (" (rotated 180°)" if spec.get("rotate180") else ""))

    if rotate:
        argv += ["--rotate-180", ",".join(rotate)]
    argv += ["--port", str(args.port or cfg.get("port", 5555)), "--fps", str(cfg.get("fps", 30))]

    print("[camera-config] exec:", " ".join(argv))
    if args.dry_run:
        return
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    main()
