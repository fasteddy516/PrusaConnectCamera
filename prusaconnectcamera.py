#!/usr/bin/env python3
"""PrusaConnectCamera — connects USB/CSI cameras on a Raspberry Pi to Prusa Connect.

Normal usage (service):
    /opt/prusaconnectcamera/.venv/bin/python /opt/prusaconnectcamera/prusaconnectcamera.py

Development / testing:
    python prusaconnectcamera.py --config /path/to/config.json
"""

import argparse
import hashlib
import logging
import os
import signal
import sys
import threading
import time

from prusaconnectcamera import __version__
from prusaconnectcamera.api import PrusaConnectAPI
from prusaconnectcamera.camera_info import build_info_payload
from prusaconnectcamera.capture import CaptureError, RTSPSnapshotBackend, create_backend, validate_backends
from prusaconnectcamera.config import DEFAULT_CONFIG_PATH, generate_default_config, load_config
from prusaconnectcamera.network_info import collect_network_info
from prusaconnectcamera.scheduler import CameraWorker
from prusaconnectcamera.streaming import (
    MediaMTXService,
    DEFAULT_RTSP_PORT,
    build_rtsp_url,
    StreamPublisher,
    stream_host_for_logs,
    stream_path_for_camera,
    validate_streaming_binaries,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("prusaconnectcamera")

# How often (seconds) the main thread polls the config file for changes.
_CONFIG_POLL_INTERVAL = 10
_STREAM_READY_TIMEOUT = 15.0


def _wait_for_stream_ready(camera_state: list[dict], stop_event: threading.Event) -> None:
    """Wait briefly for RTSP-backed snapshot sources to become readable."""
    for cs in camera_state:
        backend = cs["backend"]
        trigger = cs["config"]["trigger_scheme"]
        if trigger == "MANUAL" or not isinstance(backend, RTSPSnapshotBackend):
            continue

        name = cs["config"]["name"]
        deadline = time.monotonic() + _STREAM_READY_TIMEOUT
        ready = False
        while not stop_event.is_set() and time.monotonic() < deadline:
            try:
                backend.capture()
                ready = True
                break
            except CaptureError:
                stop_event.wait(0.5)

        if ready:
            log.info("Camera %r: RTSP stream is ready for snapshots.", name)
        else:
            log.warning(
                "Camera %r: RTSP stream not ready after %.0f s; continuing and relying on retry backoff.",
                name,
                _STREAM_READY_TIMEOUT,
            )


def _build_info_payload(camera: dict, camera_number: int, network_info: dict) -> dict:
    """Build the PUT /c/info request body from camera config and host state.

    Ref: https://connect.prusa3d.com/docs/cameras/openapi/
    """
    return build_info_payload(
        camera=camera,
        camera_number=camera_number,
        script_version=__version__,
        network_info=network_info,
    )


def _file_sha256(path: str) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main(config_path: str = DEFAULT_CONFIG_PATH) -> None:
    log.info("PrusaConnectCamera starting up.")

    # ------------------------------------------------------------------ config
    if not os.path.exists(config_path):
        log.info("No config file found at %s — generating default config.", config_path)
        try:
            generate_default_config(config_path)
        except RuntimeError as exc:
            log.error("Could not generate default config: %s", exc)
            sys.exit(1)
        log.error(
            "Default config written to %s. "
            "Edit it to add your printer UUIDs and camera tokens, then restart.",
            config_path,
        )
        sys.exit(0)

    try:
        config = load_config(config_path)
    except RuntimeError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)

    cameras = config["cameras"]
    rtsp_port = config["rtsp_port"]

    # --------------------------------------------------------- backend binaries
    try:
        validate_backends(cameras)
        validate_streaming_binaries(cameras)
    except RuntimeError as exc:
        log.error("Capture backend error: %s", exc)
        sys.exit(1)

    # ------------------------------------------------------- per-camera startup
    network_info = collect_network_info()
    if network_info:
        log.info("Detected network info keys for camera attributes: %s", ", ".join(sorted(network_info.keys())))
    else:
        log.info("No active default network route detected; omitting network_info from camera attributes.")

    streaming_host = stream_host_for_logs(network_info)
    stop_event = threading.Event()

    camera_state = []
    usb_stream_index = 0
    csi_stream_index = 0
    for index, cam in enumerate(cameras, start=1):
        api = PrusaConnectAPI(cam["token"], cam["fingerprint"])
        stream_path = None
        stream_url = None
        if cam.get("streaming", True):
            if cam["driver"] == "V4L2":
                usb_stream_index += 1
            elif cam["driver"] == "CSI":
                csi_stream_index += 1
            stream_path = stream_path_for_camera(cam["driver"], usb_stream_index, csi_stream_index)
            stream_url = build_rtsp_url("127.0.0.1", rtsp_port, stream_path)
            log.info(
                "Camera %r local RTSP URL: %s",
                cam["name"],
                build_rtsp_url(streaming_host, rtsp_port, stream_path),
            )

        backend = create_backend(cam)
        if stream_url:
            backend = RTSPSnapshotBackend(
                stream_url,
                cam["resolution"]["width"],
                cam["resolution"]["height"],
            )

        camera_state.append({
            "number": index,
            "config": cam,
            "api": api,
            "backend": backend,
            "stream_url": stream_url,
        })

    # --------------------------------------------------- local RTSP server / publishers
    media_service = None
    stream_threads: list[threading.Thread] = []
    if any(cs["stream_url"] for cs in camera_state):
        media_service = MediaMTXService(config["state_dir"], rtsp_port)
        try:
            media_service.start()
        except Exception as exc:
            log.error("RTSP server startup failed: %s", exc)
            sys.exit(1)

        for cs in camera_state:
            if not cs["stream_url"]:
                continue
            publisher = StreamPublisher(cs["config"], cs["stream_url"], stop_event)
            t = threading.Thread(
                target=publisher.run,
                name=f"stream-{cs['config']['name']}",
                daemon=True,
            )
            t.start()
            stream_threads.append(t)

    # --------------------------------------------------- initial PUT /c/info
    for cs in camera_state:
        name = cs["config"]["name"]
        log.info("Sending initial camera info for %r.", name)
        payload = _build_info_payload(cs["config"], cs["number"], network_info)
        if not cs["api"].update_info(payload):
            log.warning("Initial info update for camera %r failed; will retry on next config change.", name)

    _wait_for_stream_ready(camera_state, stop_event)

    # --------------------------------------------------- graceful shutdown
    def _on_signal(sig, _frame):
        log.info("Received signal %d — shutting down.", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # --------------------------------------------------- worker threads
    threads: list[threading.Thread] = []
    for cs in camera_state:
        worker = CameraWorker(
            cs["config"], cs["api"], cs["backend"], stop_event
        )
        t = threading.Thread(
            target=worker.run,
            name=cs["config"]["name"],
            daemon=True,
        )
        t.start()
        threads.append(t)

    # ----------------------------------------- config-change watcher (main thread)
    try:
        config_hash = _file_sha256(config_path)
    except OSError:
        config_hash = ""

    while not stop_event.is_set():
        stop_event.wait(_CONFIG_POLL_INTERVAL)
        if stop_event.is_set():
            break

        try:
            new_hash = _file_sha256(config_path)
        except OSError:
            continue

        if new_hash == config_hash:
            continue

        log.info("Config file changed — re-sending camera info for all cameras.")
        config_hash = new_hash

        # Validate the new config before acting on it.
        try:
            new_config = load_config(config_path)
        except RuntimeError as exc:
            log.error(
                "Reloaded config is invalid: %s — continuing with existing config.", exc
            )
            continue

        # Re-send PUT /c/info for each camera whose config entry is still present.
        new_cameras_by_name = {c["name"]: c for c in new_config["cameras"]}
        for cs in camera_state:
            name = cs["config"]["name"]
            if name in new_cameras_by_name:
                updated_cam = new_cameras_by_name[name]
                cs["config"] = updated_cam
                payload = _build_info_payload(updated_cam, cs["number"], network_info)
                log.info("Re-sending camera info for %r.", name)
                cs["api"].update_info(payload)

    # ---------------------------------------------------- wait for workers
    for t in threads:
        t.join(timeout=5)
    for t in stream_threads:
        t.join(timeout=5)

    if media_service is not None:
        media_service.stop()

    log.info("PrusaConnectCamera stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prusa Connect Camera Service",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        metavar="PATH",
        help="Path to the JSON configuration file",
    )
    args = parser.parse_args()
    main(args.config)
