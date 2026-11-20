"""List the cameras the camera server can use, with a camera_config.yaml entry for each.

Started by tng/scripts/list_cameras.sh and tng/scripts/setup_camera_server.sh in .venv_camera.
Covers the server's drivers realsense (by serial number), oak (by device ID) and usb (UVC
cameras, by name or USB port), each with the resolutions the camera offers natively.
"""

import fcntl
import glob
import itertools
import os
import re
import struct
from dataclasses import dataclass

HEADER = """\
Cameras for gear_sonic/camera/camera_config.yaml (under views:). Rename each <view> to ego_view,
left_wrist or right_wrist and choose its resolution (the driver default is set): a RealSense
accepts only its native modes; a USB camera is captured at least at 1280x720, center-cropped to
the resolution's aspect ratio and scaled; an OAK scales to any size. Add `rotate180: true` for a
camera mounted upside down. Unplug cameras one at a time to see which is which.
After editing: sudo systemctl restart composed_camera_server
"""

# Linux videodev2: VIDIOC_ENUM_FRAMESIZES = _IOWR('V', 74, struct v4l2_frmsizeenum), whose
# fields are index, pixel_format, type, a union of 6 u32 (width, height first) and 2 reserved.
FRMSIZEENUM = struct.Struct("=3I6I2I")
VIDIOC_ENUM_FRAMESIZES = (3 << 30) | (FRMSIZEENUM.size << 16) | (ord("V") << 8) | 74
FRMSIZE_TYPE_DISCRETE = 1


@dataclass
class Camera:
    description: str
    driver: str
    device: str
    resolution: tuple[int, int] = (424, 240)
    modes: str = ""
    note: str = ""


def _sizes(modes: list[tuple[int, int]]) -> str:
    return ", ".join(f"{width}x{height}" for width, height in sorted(set(modes), reverse=True))


def _realsense_modes(device) -> list[tuple[int, int]]:
    """Color modes the server can request: RGB at 30 fps, as offered on the current USB link."""
    import pyrealsense2 as rs

    modes = []
    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            if (
                profile.stream_type() == rs.stream.color
                and profile.format() == rs.format.rgb8
                and profile.fps() == 30
            ):
                video = profile.as_video_stream_profile()
                modes.append((video.width(), video.height()))
    return modes


def realsense_cameras() -> list[Camera]:
    import pyrealsense2 as rs

    cameras = []
    for device in rs.context().query_devices():
        serial = device.get_info(rs.camera_info.serial_number)
        usb = device.get_info(rs.camera_info.usb_type_descriptor)
        name = device.get_info(rs.camera_info.name)
        try:
            modes = f"native (RGB, 30 fps, USB {usb} link): {_sizes(_realsense_modes(device))}"
        except RuntimeError as error:
            modes = f"native modes unknown ({error})"
        cameras.append(Camera(f"{name}, USB {usb}", "realsense", serial, modes=modes))
    return cameras


def oak_cameras() -> list[Camera]:
    import depthai as dai

    cameras = []
    for info in dai.Device.getAllAvailableDevices():
        try:
            with dai.Device(info) as device:
                sensor_modes = [
                    (config.width, config.height)
                    for features in device.getConnectedCameraFeatures()
                    if features.socket == dai.CameraBoardSocket.CAM_A
                    for config in features.configs
                ]
            modes = f"color sensor modes: {_sizes(sensor_modes)}; scales to any size"
        except (RuntimeError, AttributeError) as error:
            modes = f"sensor modes unknown ({error})"
        cameras.append(Camera(f"OAK at {info.name}", "oak", info.getDeviceId(), (640, 480), modes))
    return cameras


def _frame_sizes(fd: int, pixel_format: int):
    for index in itertools.count():
        request = bytearray(FRMSIZEENUM.pack(index, pixel_format, 0, *[0] * 8))
        try:
            fcntl.ioctl(fd, VIDIOC_ENUM_FRAMESIZES, request)
        except OSError:
            return
        _, _, kind, *size = FRMSIZEENUM.unpack(request)
        if kind == FRMSIZE_TYPE_DISCRETE:
            yield size[0], size[1]
        else:  # a continuous or stepwise range: min/max/step width, then min/max/step height
            yield size[1], size[4]
            return


def usb_modes(device: str) -> tuple[str, list[tuple[int, int]]]:
    """Frame sizes the camera offers in MJPG, which the server requests, else in YUYV."""
    fd = os.open(device, os.O_RDWR)
    try:
        for fourcc in ("MJPG", "YUYV"):
            sizes = list(_frame_sizes(fd, int.from_bytes(fourcc.encode(), "little")))
            if sizes:
                return fourcc, sizes
        return "", []
    finally:
        os.close(fd)


def usb_cameras() -> list[Camera]:
    """UVC cameras by their /dev/v4l/by-id name; RealSense ones belong to the realsense driver."""
    by_path = {os.path.realpath(p): p for p in glob.glob("/dev/v4l/by-path/*-video-index0")}
    by_id = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
    ports = {
        path: by_path.get(os.path.realpath(path))
        for path in by_id
        if "realsense" not in path.lower()
    }
    cameras = usb_entries(ports)
    for camera, path in zip(cameras, ports, strict=True):
        try:
            fourcc, sizes = usb_modes(path)
            camera.modes = f"native ({fourcc}): {_sizes(sizes)}" if sizes else "no modes reported"
        except OSError as error:
            camera.modes = f"native modes unknown ({error.strerror})"
    return cameras


def usb_entries(ports: dict[str, str | None]) -> list[Camera]:
    """One entry per by-id path (mapped to its by-path port, if known): `match:` on the longest
    plain part of its name, so it follows the camera to any port; by port where that part also
    names another camera (the same model twice). by-id names may hold characters that are hard
    to type, e.g. non-breaking spaces, hence no full path."""
    names = {
        path: os.path.basename(path).removeprefix("usb-").removesuffix("-video-index0")
        for path in ports
    }
    tokens = {
        path: max(re.findall(r"[A-Za-z0-9_.+-]+", name), key=len) for path, name in names.items()
    }
    cameras = []
    for path, port in ports.items():
        if sum(tokens[path].lower() in other.lower() for other in ports) == 1:
            note = f'bound to this USB port instead: device: "{port}"' if port else ""
            cameras.append(
                Camera(f"USB camera {names[path]}", "usb", f"match:{tokens[path]}", note=note)
            )
        else:
            cameras.append(
                Camera(f"USB camera {names[path]}, model connected twice", "usb", port or path)
            )
    return cameras


def config_entry(camera: Camera) -> str:
    width, height = camera.resolution
    lines = [
        f"  <view>:  # {camera.description}",
        f"    driver: {camera.driver}",
        f'    device: "{camera.device}"',
        f"    resolution: [{width}, {height}]" + (f"  # {camera.modes}" if camera.modes else ""),
    ]
    if camera.note:
        lines.append(f"    # {camera.note}")
    return "\n".join(lines)


def main() -> None:
    cameras, unchecked = [], []
    for kind, discover in (
        ("RealSense", realsense_cameras),
        ("OAK", oak_cameras),
        ("USB", usb_cameras),
    ):
        try:
            cameras += discover()
        except ImportError as error:
            unchecked.append(f"{kind} not checked: {error}")
    print(HEADER)
    for camera in cameras:
        print(config_entry(camera) + "\n")
    if not cameras:
        print("No RealSense, OAK or USB camera found: check cables and USB power. RealSense")
        print("cameras need the udev rules from tng/scripts/setup_camera_server.sh.")
    for line in unchecked:
        print(line)


if __name__ == "__main__":
    main()
