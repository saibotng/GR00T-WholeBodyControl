import errno

import list_cameras
from list_cameras import FRMSIZEENUM, Camera, config_entry, usb_entries, usb_modes

SPEDAL = "/dev/v4l/by-id/usb-Image+_Spedal 4K-video-index0"
C922_A = "/dev/v4l/by-id/usb-046d_C922_Pro_Stream_Webcam-video-index0"
C922_B = "/dev/v4l/by-id/usb-046d_C922_Pro_Stream_Webcam_2-video-index0"
PORT = "/dev/v4l/by-path/platform-3610000.usb-usb-0:2.1.3:1.0-video-index0"
MJPG = int.from_bytes(b"MJPG", "little")


def test_usb_cameras_matched_by_a_typeable_part_of_their_name():
    (spedal,) = usb_entries({SPEDAL: PORT})
    assert spedal.device == "match:Image+_Spedal"
    assert PORT in spedal.note


def test_the_same_model_twice_is_bound_to_its_port():
    first, second = usb_entries({C922_A: PORT, C922_B: None})
    assert first.device == PORT
    assert second.device == "match:046d_C922_Pro_Stream_Webcam_2"


def test_enum_framesizes_request_matches_videodev2():
    assert list_cameras.VIDIOC_ENUM_FRAMESIZES == 0xC02C564A  # from linux/videodev2.h


def test_usb_modes_reads_the_mjpg_frame_sizes(tmp_path, monkeypatch):
    """A fake camera answering VIDIOC_ENUM_FRAMESIZES like the kernel: two MJPG sizes."""
    offered = [(1920, 1080), (1280, 720)]

    def ioctl(fd, request, buffer):
        index, pixel_format, *_ = FRMSIZEENUM.unpack(buffer)
        if pixel_format != MJPG or index >= len(offered):
            raise OSError(errno.EINVAL, "no more sizes")
        FRMSIZEENUM.pack_into(buffer, 0, index, pixel_format, 1, *offered[index], *[0] * 6)

    monkeypatch.setattr(list_cameras.fcntl, "ioctl", ioctl)
    device = tmp_path / "video0"
    device.touch()
    assert usb_modes(str(device)) == ("MJPG", offered)


def test_config_entry():
    camera = Camera("Intel RealSense D405, USB 3.2", "realsense", "0123", modes="native: 424x240")
    assert config_entry(camera).splitlines() == [
        "  <view>:  # Intel RealSense D405, USB 3.2",
        "    driver: realsense",
        '    device: "0123"',
        "    resolution: [424, 240]  # native: 424x240",
    ]
