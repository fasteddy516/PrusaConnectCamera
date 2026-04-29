"""Capture backend abstraction.

Supported backends:
  V4L2  — USB webcams captured via ``ffmpeg``
  CSI   — Raspberry Pi camera module captured via ``libcamera-still``

Backend selection is driven by the ``driver`` field in each camera's config.
Required binaries are validated at startup; a missing binary is a
non-recoverable error for the affected camera.
"""

import abc
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)


class CaptureError(Exception):
    """Raised when a capture attempt fails transiently."""


class CaptureBackend(abc.ABC):
    """Abstract base for all capture backends."""

    @abc.abstractmethod
    def capture(self) -> bytes:
        """Capture one JPEG frame and return the raw bytes."""


class RTSPSnapshotBackend(CaptureBackend):
    """Capture a JPEG frame from a local RTSP stream using ``ffmpeg``."""

    BINARY = "ffmpeg"

    def __init__(self, stream_url: str, width: int, height: int) -> None:
        self._stream_url = stream_url
        self._width = width
        self._height = height

    def capture(self) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                [
                    self.BINARY,
                    "-y",
                    "-rtsp_transport", "tcp",
                    "-i", self._stream_url,
                    "-frames:v", "1",
                    "-vf", f"scale={self._width}:{self._height}",
                    "-q:v", "2",
                    tmp_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(
                f"ffmpeg timed out capturing from RTSP stream {self._stream_url}"
            ) from exc

        try:
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                raise CaptureError(
                    f"ffmpeg exited {result.returncode} capturing from RTSP stream {self._stream_url}: {stderr}"
                )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class V4L2Backend(CaptureBackend):
    """Capture a JPEG still from a V4L2 device using ``ffmpeg``."""

    BINARY = "ffmpeg"

    def __init__(self, device_path: str, width: int, height: int) -> None:
        self._device = device_path
        self._width = width
        self._height = height

    def capture(self) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                [
                    self.BINARY,
                    "-y",                          # overwrite output
                    "-f", "v4l2",                  # V4L2 input format
                    "-video_size", f"{self._width}x{self._height}",
                    "-i", self._device,
                    "-vf", "select='gte(n,20)'",   # discard early dark frames
                    "-frames:v", "1",              # single frame
                    "-q:v", "2",                   # high JPEG quality
                    tmp_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(f"ffmpeg timed out capturing from {self._device}") from exc
        finally:
            pass  # cleanup in outer finally

        try:
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                raise CaptureError(
                    f"ffmpeg exited {result.returncode} capturing from {self._device}: {stderr}"
                )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class CSIBackend(CaptureBackend):
    """Capture a JPEG still from a Raspberry Pi CSI camera via ``libcamera-still``."""

    BINARY = "libcamera-still"

    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def capture(self) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                [
                    self.BINARY,
                    "--width", str(self._width),
                    "--height", str(self._height),
                    "-n",           # no preview
                    "-t", "1",      # 1 ms timeout — capture immediately
                    "-o", tmp_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise CaptureError("libcamera-still timed out") from exc
        finally:
            pass

        try:
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                raise CaptureError(
                    f"libcamera-still exited {result.returncode}: {stderr}"
                )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def create_backend(camera: dict) -> CaptureBackend:
    """Instantiate the correct backend for *camera*'s driver field."""
    driver = camera["driver"]
    width = camera["resolution"]["width"]
    height = camera["resolution"]["height"]
    if driver == "V4L2":
        return V4L2Backend(camera["device_path"], width, height)
    if driver == "CSI":
        return CSIBackend(width, height)
    raise ValueError(f"Unknown driver {driver!r}.")


def validate_backends(cameras: list) -> None:
    """Verify that every required backend binary is present in PATH.

    Raises RuntimeError listing all missing binaries so the operator can
    install them before the service starts.
    """
    needed: dict[str, str] = {}
    for cam in cameras:
        driver = cam["driver"]
        if driver == "V4L2":
            needed[V4L2Backend.BINARY] = "V4L2 cameras (install ffmpeg)"
        elif driver == "CSI":
            needed[CSIBackend.BINARY] = "CSI cameras (install libcamera-tools)"

    missing = [
        f"{binary} — needed for {desc}"
        for binary, desc in needed.items()
        if shutil.which(binary) is None
    ]
    if missing:
        raise RuntimeError(
            "Required capture backend(s) not found in PATH:\n  "
            + "\n  ".join(missing)
        )
