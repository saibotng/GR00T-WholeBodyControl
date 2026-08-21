"""Generic USB webcam driver using OpenCV.

No hardware SDK needed — works with any UVC-compatible camera visible as
``/dev/video*``.  Only requires ``opencv-python``.
"""

import time
from typing import Any

import cv2
import numpy as np

try:
    import gymnasium as gym
except ImportError:
    gym = None  # type: ignore[assignment]

from gear_sonic.camera.sensor import Sensor
from gear_sonic.camera.sensor_server import CameraMountPosition


class USBCameraConfig:
    """Configuration for generic USB camera."""

    image_dim: tuple = (1280, 720)
    """Capture resolution requested from the camera (width, height)."""

    output_dim: tuple = (424, 240)
    """Published frame size (width, height). Captured frames are center-cropped
    to this aspect ratio and downscaled, matching the RealSense streams."""

    fps: int = 30
    device_index: int = 0


class USBCameraSensor(Sensor):
    """Sensor for generic USB cameras using OpenCV VideoCapture."""

    def __init__(
        self,
        config: USBCameraConfig = USBCameraConfig(),
        mount_position: str = CameraMountPosition.EGO_VIEW.value,
        device_index: int | str | None = None,
        rotate_180: bool = False,
    ):
        self.config = config
        self.mount_position = mount_position
        self._rotate_180 = rotate_180

        idx = device_index if device_index is not None else config.device_index

        self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open USB camera at index {idx}")

        # MJPG first: many UVC cameras only serve higher modes compressed.
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.image_dim[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.image_dim[1])
        self.cap.set(cv2.CAP_PROP_FPS, config.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        print(f"[{mount_position}] Warming up USB camera...")
        for _ in range(10):
            ret, _ = self.cap.read()
            if ret:
                break
            time.sleep(0.1)

        print(f"[{mount_position}] USB camera opened at index {idx}")
        width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"  Capture resolution: {width}x{height}")
        print(f"  Output resolution: {config.output_dim[0]}x{config.output_dim[1]}")
        print(f"  FPS: {self.cap.get(cv2.CAP_PROP_FPS)}")
        print(f"  Rotate 180: {rotate_180}")

    def _crop_and_resize(self, frame: np.ndarray) -> np.ndarray:
        out_w, out_h = self.config.output_dim
        h, w = frame.shape[:2]
        if (w, h) == (out_w, out_h):
            return frame
        target_aspect = out_w / out_h
        crop_w = min(w, int(round(h * target_aspect)))
        crop_h = min(h, int(round(w / target_aspect)))
        x0 = (w - crop_w) // 2
        y0 = (h - crop_h) // 2
        frame = frame[y0 : y0 + crop_h, x0 : x0 + crop_w]
        return cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)

    def read(self) -> dict[str, Any] | None:
        ret, frame = self.cap.read()
        if not ret or frame is None:
            print(f"[{self.mount_position}] USB camera read failed: ret={ret}")
            return None

        frame = self._crop_and_resize(frame)
        if self._rotate_180:
            frame = np.ascontiguousarray(frame[::-1, ::-1])
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return {
            "timestamps": {self.mount_position: time.time()},
            "images": {self.mount_position: frame_rgb},
        }

    def serialize(self, data: dict[str, Any]) -> dict[str, Any]:
        from gear_sonic.camera.sensor_server import ImageMessageSchema

        serialized_msg = ImageMessageSchema(timestamps=data["timestamps"], images=data["images"])
        return serialized_msg.serialize()

    def observation_space(self):
        if gym is None:
            return None
        return gym.spaces.Dict(
            {
                "color_image": gym.spaces.Box(
                    low=0,
                    high=255,
                    shape=(self.config.output_dim[1], self.config.output_dim[0], 3),
                    dtype=np.uint8,
                ),
            }
        )

    def close(self):
        if self.cap is not None:
            self.cap.release()
