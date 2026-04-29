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

from prusaconnectcamera.api import PrusaConnectAPI
from prusaconnectcamera.capture import create_backend, validate_backends
from prusaconnectcamera.config import DEFAULT_CONFIG_PATH, generate_default_config, load_config
from prusaconnectcamera.fingerprint import load_or_create
from prusaconnectcamera.scheduler import CameraWorker

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("prusaconnectcamera")

# How often (seconds) the main thread polls the config file for changes.
_CONFIG_POLL_INTERVAL = 10


def _build_info_payload(camera: dict) -> dict:
    """Build the PUT /c/info request body from a camera config entry.

    Ref: https://connect.prusa3d.com/docs/cameras/openapi/
    """
    return {
        "config": {
            "path": camera["device_path"],
            "name": camera["name"],
            "driver": camera["driver"],
            "trigger_scheme": camera["trigger_scheme"],
            "resolution": {
                "width": camera["resolution"]["width"],
                "height": camera["resolution"]["height"],
            },
        },
        "capabilities": ["trigger_scheme", "resolution"],
    }


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

    state_dir = config["state_dir"]
    cameras = config["cameras"]

    # --------------------------------------------------------- backend binaries
    try:
        validate_backends(cameras)
    except RuntimeError as exc:
        log.error("Capture backend error: %s", exc)
        sys.exit(1)

    # ------------------------------------------------------- per-camera startup
    camera_state = []
    for cam in cameras:
        try:
            fp = load_or_create(cam["name"], state_dir)
        except RuntimeError as exc:
            log.error("Fingerprint error for camera %r: %s", cam["name"], exc)
            sys.exit(1)

        api = PrusaConnectAPI(cam["token"], fp)
        backend = create_backend(cam)
        camera_state.append({
            "config": cam,
            "api": api,
            "backend": backend,
        })

    # --------------------------------------------------- initial PUT /c/info
    for cs in camera_state:
        name = cs["config"]["name"]
        log.info("Sending initial camera info for %r.", name)
        payload = _build_info_payload(cs["config"])
        if not cs["api"].update_info(payload):
            log.warning("Initial info update for camera %r failed; will retry on next config change.", name)

    # --------------------------------------------------- graceful shutdown
    stop_event = threading.Event()

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
                payload = _build_info_payload(updated_cam)
                log.info("Re-sending camera info for %r.", name)
                cs["api"].update_info(payload)

    # ---------------------------------------------------- wait for workers
    for t in threads:
        t.join(timeout=5)

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
