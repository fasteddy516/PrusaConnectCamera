"""Local RTSP streaming support via MediaMTX and per-camera publisher loops."""

import logging
import os
import socket
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .scheduler import backoff_delay

log = logging.getLogger(__name__)

DEFAULT_RTSP_PORT = 8554


class StreamingError(Exception):
    """Raised when RTSP streaming setup fails at startup."""


def stream_path_for_camera(driver: str, usb_index: int, csi_index: int) -> str:
    """Return the RTSP path segment for a camera based on its driver/index."""
    if driver == "V4L2":
        return f"usb/{usb_index}"
    if driver == "CSI":
        return f"csi/{csi_index}"
    raise ValueError(f"Unsupported driver for streaming: {driver!r}")


def build_rtsp_url(host: str, port: int, path: str) -> str:
    """Return a full RTSP URL using standard syntax."""
    return f"rtsp://{host}:{port}/{path}"


def stream_host_for_logs(network_info: dict) -> str:
    """Pick a host/IP value suitable for startup stream URL logging."""
    for key in ("wifi_ipv4", "lan_ipv4", "wifi_ipv6", "lan_ipv6"):
        value = network_info.get(key)
        if value:
            return value
    return "127.0.0.1"


def validate_streaming_binaries(cameras: list[dict]) -> None:
    """Fail fast if streaming is enabled but required binaries are missing."""
    streaming_enabled = any(cam.get("streaming", True) for cam in cameras)
    if not streaming_enabled:
        return

    missing = []
    for binary in ("mediamtx", "ffmpeg"):
        if shutil.which(binary) is None:
            missing.append(binary)

    if any(cam["driver"] == "CSI" and cam.get("streaming", True) for cam in cameras):
        if shutil.which("libcamera-vid") is None:
            missing.append("libcamera-vid")

    if missing:
        unique = sorted(set(missing))
        raise RuntimeError(
            "Required streaming binary/binaries not found in PATH: "
            + ", ".join(unique)
            + ". Install MediaMTX and required camera tools, then restart."
        )


class MediaMTXService:
    """Manage the MediaMTX process and auto-generated config."""

    def __init__(self, state_dir: str, port: int = DEFAULT_RTSP_PORT) -> None:
        self._state_dir = Path(state_dir)
        self._port = port
        self._config_path = self._state_dir / "mediamtx.yml"
        self._log_path = self._state_dir / "mediamtx.log"
        self._pid_path = self._state_dir / "mediamtx.pid"
        self._log_handle = None
        self._process: subprocess.Popen | None = None
        self._using_external = False

    @property
    def port(self) -> int:
        return self._port

    def _config_text(self) -> str:
        return (
            "logLevel: warn\n"
            "rtsp: yes\n"
            f"rtspAddress: :{self._port}\n"
            "hls: no\n"
            "webrtc: no\n"
            "rtmp: no\n"
            "paths:\n"
            "  all:\n"
            "    source: publisher\n"
        )

    def _port_is_listening(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex(("127.0.0.1", self._port)) == 0

    def _read_pid_file(self) -> int | None:
        try:
            text = self._pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not text:
            return None
        try:
            pid = int(text)
        except ValueError:
            return None
        return pid if pid > 0 else None

    def _remove_pid_file(self) -> None:
        try:
            self._pid_path.unlink()
        except OSError:
            pass

    def _pid_is_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _cleanup_stale_owned_instance(self) -> None:
        pid = self._read_pid_file()
        if pid is None:
            return

        if not self._pid_is_running(pid):
            self._remove_pid_file()
            return

        log.warning(
            "Found stale app-owned MediaMTX process (pid %d); attempting cleanup.",
            pid,
        )
        try:
            os.kill(pid, 15)
        except OSError:
            self._remove_pid_file()
            return

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self._pid_is_running(pid):
                self._remove_pid_file()
                return
            time.sleep(0.1)

        try:
            os.kill(pid, 9)
        except OSError:
            pass
        self._remove_pid_file()

    def start(self) -> None:
        if self._process is not None:
            return

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_owned_instance()

        if self._port_is_listening():
            self._using_external = True
            log.info(
                "RTSP port %d is already in use; reusing existing local MediaMTX instance.",
                self._port,
            )
            return

        self._config_path.write_text(self._config_text(), encoding="utf-8")

        self._log_handle = open(self._log_path, "a", encoding="utf-8")

        try:
            self._process = subprocess.Popen(
                ["mediamtx", str(self._config_path)],
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            raise StreamingError(f"Failed to start MediaMTX: {exc}") from exc

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            time.sleep(0.1)

        rc = self._process.poll()
        if rc is not None:
            details = ""
            try:
                tail = self._log_path.read_text(encoding="utf-8")[-600:]
                details = f" Last log output: {tail.strip()}"
            except OSError:
                pass
            raise StreamingError(
                f"MediaMTX exited during startup (code {rc}) on port {self._port}.{details}"
            )

        self._using_external = False
        self._pid_path.write_text(f"{self._process.pid}\n", encoding="utf-8")

    def stop(self) -> None:
        if self._using_external:
            self._using_external = False
            return

        if self._process is None:
            return

        proc = self._process
        self._process = None
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        self._remove_pid_file()
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class StreamPublisher:
    """Keep publishing one camera to one RTSP path until shutdown."""

    def __init__(self, camera_config: dict, stream_url: str, stop_event: threading.Event) -> None:
        self._camera = camera_config
        self._stream_url = stream_url
        self._stop = stop_event
        self._name = camera_config["name"]

    def run(self) -> None:
        consecutive_failures = 0
        while not self._stop.is_set():
            ok = self._run_once_until_exit()
            if ok:
                consecutive_failures = 0
                continue

            consecutive_failures += 1
            delay = backoff_delay(consecutive_failures - 1)
            log.warning(
                "Camera %r: RTSP stream publisher failed; retrying in %.1f s.",
                self._name,
                delay,
            )
            self._stop.wait(delay)

    def _run_once_until_exit(self) -> bool:
        if self._camera["driver"] == "V4L2":
            return self._run_v4l2_once()
        if self._camera["driver"] == "CSI":
            return self._run_csi_once()
        log.error("Camera %r: unsupported driver %r for RTSP streaming.", self._name, self._camera["driver"])
        return False

    def _run_v4l2_once(self) -> bool:
        width = self._camera["resolution"]["width"]
        height = self._camera["resolution"]["height"]
        fps = self._camera["fps"]
        bitrate_kbps = self._camera["bitrate"]
        bitrate = f"{bitrate_kbps}k"
        gop = max(1, fps)
        vbv_buf_kbps = max(256, bitrate_kbps // 2)

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
            "-framerate",
            str(fps),
            "-video_size",
            f"{width}x{height}",
            "-i",
            self._camera["device_path"],
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            f"{vbv_buf_kbps}k",
            "-flush_packets",
            "1",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self._stream_url,
        ]

        return self._run_single_process(command)

    def _run_csi_once(self) -> bool:
        width = self._camera["resolution"]["width"]
        height = self._camera["resolution"]["height"]
        fps = self._camera["fps"]
        bitrate_kbps = self._camera["bitrate"]

        libcamera_cmd = [
            "libcamera-vid",
            "-n",
            "-t",
            "0",
            "--inline",
            "--intra",
            str(max(1, fps)),
            "--codec",
            "h264",
            "--width",
            str(width),
            "--height",
            str(height),
            "--framerate",
            str(fps),
            "--bitrate",
            str(bitrate_kbps * 1000),
            "-o",
            "-",
        ]
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "copy",
            "-flush_packets",
            "1",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self._stream_url,
        ]

        try:
            cam_proc = subprocess.Popen(
                libcamera_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=cam_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("Camera %r: could not start CSI RTSP pipeline: %s", self._name, exc)
            return False

        if cam_proc.stdout is not None:
            cam_proc.stdout.close()

        return self._wait_and_terminate_pipeline(cam_proc, ffmpeg_proc)

    def _run_single_process(self, command: list[str]) -> bool:
        with tempfile.NamedTemporaryFile(prefix="stream-publisher-", suffix=".log", delete=False) as log_file:
            log_path = log_file.name
        stderr_handle = None
        try:
            stderr_handle = open(log_path, "w", encoding="utf-8")
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
            )
        except OSError as exc:
            if stderr_handle is not None:
                stderr_handle.close()
            try:
                os.unlink(log_path)
            except OSError:
                pass
            log.warning("Camera %r: could not start RTSP publisher: %s", self._name, exc)
            return False

        try:
            while not self._stop.is_set():
                rc = proc.poll()
                if rc is None:
                    self._stop.wait(1)
                    continue
                if rc != 0:
                    details = ""
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            details = f.read()[-600:].strip()
                    except OSError:
                        pass
                    if details:
                        log.warning(
                            "Camera %r: RTSP publisher exited with code %d. ffmpeg output: %s",
                            self._name,
                            rc,
                            details,
                        )
                return rc == 0

            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            return True
        finally:
            if stderr_handle is not None:
                stderr_handle.close()
            try:
                os.unlink(log_path)
            except OSError:
                pass

    def _wait_and_terminate_pipeline(
        self,
        cam_proc: subprocess.Popen,
        ffmpeg_proc: subprocess.Popen,
    ) -> bool:
        while not self._stop.is_set():
            cam_rc = cam_proc.poll()
            ffmpeg_rc = ffmpeg_proc.poll()
            if cam_rc is None and ffmpeg_rc is None:
                self._stop.wait(1)
                continue
            return cam_rc == 0 and ffmpeg_rc == 0

        for proc in (ffmpeg_proc, cam_proc):
            proc.terminate()
        for proc in (ffmpeg_proc, cam_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        return True
