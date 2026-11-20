"""Launch the composed camera server from camera_config.yaml.

    python -m gear_sonic.camera.run_from_config [--config PATH] [--port N] [--dry-run]

Reads the per-view driver/device/rotate180 mapping (default:
gear_sonic/camera/camera_config.yaml next to this file), resolves each device
to something composed_camera accepts, prints the resolved plan, and execs

    python -m gear_sonic.camera.composed_camera --<view>-camera ... --<view>-device-id ...

so ps/systemd see the real server process. The systemd unit installed by
gear_sonic/scripts/install_camera_service.sh points here — swapping a camera
means editing the yaml and restarting the service, never the unit file.

View names are free (lowercase, digits, '_'); each view is published under its name.
ego_view is what the data exporter always records and VLA inference reads.

Optional per view (realsense, usb, oak):  resolution: [width, height]
    publish that view at this size instead of the driver default (424x240; OAK 640x480).
    A RealSense only accepts its own color modes; a USB camera is captured at least as
    large and center-cropped to the aspect ratio; an OAK scales on the device.
    `hd: true` is short for [1280, 720]. The data exporter records whatever size is
    published, so this is also the resolution of recorded datasets. A RealSense needs a
    USB3 link for 1280x720@30.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

FIXED_VIEWS = ("ego_view", "head", "left_wrist", "right_wrist")  # any other name works too
VIEW_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
DRIVERS = ("usb", "realsense", "oak", "oak_mono", "zed")
DRIVER_RESOLUTIONS = {"realsense": (424, 240), "usb": (424, 240), "oak": (640, 480), "oak_mono": (640, 480)}
HD_RESOLUTION = (1280, 720)  # per view with `hd: true`


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


def _resolution(view: str, driver: str, spec: dict) -> tuple[int, int] | None:
    """The view's `resolution: [width, height]` (or `hd: true`), None for the driver default."""
    if spec.get("hd") and "resolution" in spec:
        _fail(f"{view}: set either 'resolution' or 'hd', not both")
    if spec.get("hd"):
        resolution = HD_RESOLUTION
    elif "resolution" in spec:
        try:
            width, height = (int(v) for v in spec["resolution"])
        except (TypeError, ValueError):
            _fail(f"{view}: resolution must be [width, height], e.g. [1280, 720] (got {spec['resolution']!r})")
        resolution = (width, height)
    else:
        return None
    if driver not in DRIVER_RESOLUTIONS:
        _fail(f"{view}: a resolution is supported for drivers {', '.join(DRIVER_RESOLUTIONS)} (got {driver})")
    return resolution


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
    resolutions: list[str] = []
    extra_views: dict[str, dict] = {}
    configured: set[str] = set()

    for view, spec in cfg["views"].items():
        if not VIEW_NAME_RE.fullmatch(str(view)):
            _fail(f"view name '{view}' must be lowercase letters, digits and '_' (e.g. 'chest_cam')")
        if not spec:
            continue
        configured.add(view)
        driver = spec.get("driver")
        if driver not in DRIVERS:
            _fail(f"{view}: driver must be one of {', '.join(DRIVERS)} (got {driver!r})")
        device = spec.get("device")
        if device in (None, ""):
            _fail(f"{view}: 'device' is required")
        device = _resolve_usb(device) if driver == "usb" else str(device)
        if view in FIXED_VIEWS:  # composed_camera has dedicated options for these
            prefix = view.replace("_", "-")
            argv += [f"--{prefix}-camera", driver, f"--{prefix}-device-id", device]
        else:
            extra_views[view] = {"camera": driver, "device_id": device}
        if spec.get("rotate180"):
            rotate.append(view)
        resolution = _resolution(view, driver, spec)
        if resolution:
            resolutions.append(f"{view}={resolution[0]}x{resolution[1]}")
        size = "x".join(map(str, resolution or DRIVER_RESOLUTIONS.get(driver, ()))) or "default size"
        print(f"[camera-config] {view}: {driver} @ {device} {size}"
              + (" (rotated 180°)" if spec.get("rotate180") else ""))

    if "ego_view" not in configured:
        argv += ["--ego-view-camera", "None"]  # composed_camera defaults ego_view to an OAK
    if extra_views:
        argv += ["--extra-views", json.dumps(extra_views)]
    if rotate:
        argv += ["--rotate-180", ",".join(rotate)]
    if resolutions:
        argv += ["--resolutions", ",".join(resolutions)]
    argv += ["--port", str(args.port or cfg.get("port", 5555)), "--fps", str(cfg.get("fps", 30))]

    print("[camera-config] exec:", " ".join(argv))
    if args.dry_run:
        return
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.execv(sys.executable, argv)


if __name__ == "__main__":
    main()
