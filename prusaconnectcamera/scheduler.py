"""Per-camera capture-and-upload worker with exponential backoff.

Trigger scheme mapping (camera-push model only — Prusa Connect has no
endpoint to request a snapshot from a camera):

  MANUAL      — no-op; the camera registers as manual but sends nothing.
  TEN_SEC     — capture every 10 seconds
  THIRTY_SEC  — capture every 30 seconds
  SIXTY_SEC   — capture every 60 seconds
  TEN_MIN     — capture every 600 seconds (deprecated)

Ref: https://connect.prusa3d.com/docs/cameras/camera_communication/
"""

import logging
import random
import threading

from .api import PrusaConnectAPI
from .capture import CaptureBackend, CaptureError

log = logging.getLogger(__name__)

# Seconds between captures for each time-based scheme.
TRIGGER_INTERVALS: dict[str, int] = {
    "TEN_SEC": 10,
    "THIRTY_SEC": 30,
    "SIXTY_SEC": 60,
    "TEN_MIN": 600,
}

# Backoff defaults (per instructions: initial 2 s, max 60 s, jitter ±20 %).
_BACKOFF_INITIAL = 2.0
_BACKOFF_MAX = 60.0
_BACKOFF_JITTER = 0.20


def backoff_delay(attempt: int) -> float:
    """Return the number of seconds to wait after *attempt* consecutive failures.

    Uses bounded exponential backoff with ±20 % jitter so that multiple
    cameras do not all retry in lock-step.
    """
    base = min(_BACKOFF_INITIAL * (2 ** attempt), _BACKOFF_MAX)
    jitter = base * _BACKOFF_JITTER
    return base + random.uniform(-jitter, jitter)


class CameraWorker:
    """Runs the capture → upload loop for a single camera in its own thread."""

    def __init__(
        self,
        camera_config: dict,
        api_client: PrusaConnectAPI,
        backend: CaptureBackend,
        stop_event: threading.Event,
    ) -> None:
        self._config = camera_config
        self._api = api_client
        self._backend = backend
        self._stop = stop_event
        self._name = camera_config["name"]
        self._trigger = camera_config["trigger_scheme"]

    def run(self) -> None:
        """Entry point for the worker thread.  Returns when *stop_event* is set."""
        if self._trigger == "MANUAL":
            log.info(
                "Camera %r: trigger scheme is MANUAL — no automatic snapshots "
                "will be taken.  Waiting for shutdown signal.",
                self._name,
            )
            self._stop.wait()
            return

        interval = TRIGGER_INTERVALS[self._trigger]
        if self._trigger == "TEN_MIN":
            log.warning(
                "Camera %r: trigger scheme TEN_MIN is deprecated; "
                "continuing with a 600 s interval.",
                self._name,
            )

        log.info(
            "Camera %r: starting capture loop with a %d s interval.",
            self._name, interval,
        )

        consecutive_failures = 0
        while not self._stop.is_set():
            success = self._capture_and_upload()
            if success:
                consecutive_failures = 0
                self._stop.wait(interval)
            else:
                consecutive_failures += 1
                delay = backoff_delay(consecutive_failures - 1)
                log.info(
                    "Camera %r: %d consecutive failure(s); backing off for %.1f s.",
                    self._name, consecutive_failures, delay,
                )
                self._stop.wait(delay)

    def _capture_and_upload(self) -> bool:
        """Capture one frame and upload it.  Returns True only on full success."""
        try:
            image_data = self._backend.capture()
        except CaptureError as exc:
            log.warning("Camera %r: capture failed: %s", self._name, exc)
            return False

        ok = self._api.upload_snapshot(image_data)
        if not ok:
            log.warning("Camera %r: snapshot upload failed.", self._name)
        return ok
