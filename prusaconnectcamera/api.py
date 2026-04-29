"""Prusa Connect camera API transport layer.

Endpoints used:
  PUT /c/snapshot  - upload a JPEG still
  PUT /c/info      - update camera attributes

Ref: https://connect.prusa3d.com/docs/cameras/camera_communication/
     https://connect.prusa3d.com/docs/cameras/openapi/
"""

import logging

import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://connect.prusa3d.com"
_SNAPSHOT_URL = f"{_BASE_URL}/c/snapshot"
_INFO_URL = f"{_BASE_URL}/c/info"

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024  # 16 MB per API spec
_REQUEST_TIMEOUT = 30  # seconds


class PrusaConnectAPI:
    """Thin wrapper around the two runtime camera endpoints."""

    def __init__(self, token: str, fingerprint: str) -> None:
        # Headers held in an instance dict; never logged.
        self._auth_headers = {
            "Token": token,
            "Fingerprint": fingerprint,
        }

    def upload_snapshot(self, image_data: bytes) -> bool:
        """PUT /c/snapshot — upload a JPEG frame.

        Returns True on HTTP 200/204 (accepted), False on any failure.
        All failures are logged; the caller should apply retry logic.
        """
        if len(image_data) > MAX_SNAPSHOT_BYTES:
            log.error(
                "Snapshot size %d B exceeds the 16 MB API limit; upload skipped.",
                len(image_data),
            )
            return False

        try:
            response = requests.put(
                _SNAPSHOT_URL,
                data=image_data,
                headers={**self._auth_headers, "Content-Type": "image/jpg"},
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.warning("Snapshot upload network error: %s", exc)
            return False

        if response.status_code in (200, 204):
            log.debug("Snapshot accepted by Prusa Connect.")
            return True

        _log_error_response("Snapshot upload", response)
        return False

    def update_info(self, payload: dict) -> bool:
        """PUT /c/info — push camera attributes to Prusa Connect.

        Returns True on HTTP 200, False on any failure.
        """
        try:
            response = requests.put(
                _INFO_URL,
                json=payload,
                headers=self._auth_headers,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.warning("Camera info update network error: %s", exc)
            return False

        if response.status_code == 200:
            log.debug("Camera info accepted by Prusa Connect.")
            return True

        _log_error_response("Camera info update", response)
        return False


def _log_error_response(operation: str, response: requests.Response) -> None:
    """Emit an appropriate warning for a non-success API response."""
    code = response.status_code
    if code in (401, 403):
        log.warning(
            "%s failed (%d): verify the camera token is correct and the camera is "
            "registered in Prusa Connect.",
            operation, code,
        )
    elif code == 404:
        log.warning(
            "%s failed (404): camera may not be registered in Prusa Connect, "
            "or the printer UUID is wrong.",
            operation,
        )
    elif code == 503:
        log.warning("%s failed (503): Prusa Connect is temporarily unavailable.", operation)
    else:
        log.warning("%s failed with unexpected HTTP %d.", operation, code)
