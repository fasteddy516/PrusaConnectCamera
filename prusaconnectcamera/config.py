"""Configuration loading, schema validation, and permission enforcement."""

import json
import logging
import os
import stat
from pathlib import Path

from prusaconnectcamera.fingerprint import generate_fingerprint, is_valid_fingerprint

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = str(_PROJECT_ROOT / "config.json")
DEFAULT_STATE_DIR = str(_PROJECT_ROOT / "state")

_VALID_DRIVERS = frozenset({"V4L2", "CSI"})
_VALID_TRIGGER_SCHEMES = frozenset({"MANUAL", "TEN_SEC", "THIRTY_SEC", "SIXTY_SEC", "TEN_MIN"})
_REQUIRED_CAMERA_KEYS = frozenset({
    "name", "printer_uuid", "token", "device_path", "driver", "trigger_scheme", "resolution"
})
_KNOWN_CAMERA_KEYS = _REQUIRED_CAMERA_KEYS | {
    "retry", "enabled", "fingerprint", "firmware", "manufacturer", "model"
}
_KNOWN_TOP_KEYS = frozenset({"cameras", "state_dir"})
_ALLOWED_CONFIG_MODES = frozenset({0o600, 0o400})

MAX_USB_CAMERAS = 4
MAX_CSI_CAMERAS = 1


def _persist_config(path: str, data: dict) -> None:
    """Rewrite *path* atomically with *data* while preserving restrictive mode."""
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise RuntimeError(f"Cannot update config file {path}: {exc}") from exc


def _ensure_camera_fingerprints(data: dict, path: str) -> dict:
    """Populate missing camera fingerprints in *data* and persist the config if needed."""
    cameras = data.get("cameras")
    if not isinstance(cameras, list):
        return data

    changed = False
    for index, cam in enumerate(cameras):
        if not isinstance(cam, dict):
            continue
        fingerprint = cam.get("fingerprint")
        if fingerprint is None:
            cam["fingerprint"] = generate_fingerprint()
            changed = True
            log.info(
                "Generated missing fingerprint for camera entry %d (%r).",
                index,
                cam.get("name", f"camera {index}"),
            )
            continue
        if not isinstance(fingerprint, str) or not is_valid_fingerprint(fingerprint):
            raise RuntimeError(
                f"Camera entry {index}: 'fingerprint' must be a valid UUID string."
            )

    if changed:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode == 0o400:
            raise RuntimeError(
                f"Config file {path} is read-only (0400) and is missing one or more camera fingerprints. "
                f"Make it writable with: chmod 600 {path}, then restart so fingerprints can be persisted."
            )
        _persist_config(path, data)

    return data


def check_permissions(path: str) -> None:
    """Raise RuntimeError if config file permissions allow group or world access."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise RuntimeError(f"Cannot stat config file {path}: {e}") from e
    mode = stat.S_IMODE(st.st_mode)
    if mode not in _ALLOWED_CONFIG_MODES:
        raise RuntimeError(
            f"Config file {path} has permissions {oct(mode)}, which are too broad. "
            f"Allowed modes are 0600 and 0400 (owner access only). "
            f"Fix with: chmod 600 {path}"
        )


def generate_default_config(path: str = DEFAULT_CONFIG_PATH) -> None:
    """Write a default config file with all five camera slots defined.

    Only the first USB camera (usb_camera_1) is enabled by default.  All
    other cameras are defined but disabled so the operator can enable them
    by editing a single field.

    The file is written with mode 0600 (owner read/write only).
    """
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create config directory {parent}: {exc}"
        ) from exc

    default = {
        "cameras": [
            {
                "name": "USB Camera 1",
                "enabled": True,
                "fingerprint": generate_fingerprint(),
                "printer_uuid": "REPLACE-WITH-PRINTER-UUID",
                "token": "REPLACE-WITH-CAMERA-TOKEN",
                "device_path": "/dev/video0",
                "driver": "V4L2",
                "trigger_scheme": "THIRTY_SEC",
                "resolution": {"width": 1280, "height": 720},
            },
            {
                "name": "USB Camera 2",
                "enabled": False,
                "fingerprint": generate_fingerprint(),
                "printer_uuid": "REPLACE-WITH-PRINTER-UUID",
                "token": "REPLACE-WITH-CAMERA-TOKEN",
                "device_path": "/dev/video1",
                "driver": "V4L2",
                "trigger_scheme": "THIRTY_SEC",
                "resolution": {"width": 1280, "height": 720},
            },
            {
                "name": "USB Camera 3",
                "enabled": False,
                "fingerprint": generate_fingerprint(),
                "printer_uuid": "REPLACE-WITH-PRINTER-UUID",
                "token": "REPLACE-WITH-CAMERA-TOKEN",
                "device_path": "/dev/video2",
                "driver": "V4L2",
                "trigger_scheme": "THIRTY_SEC",
                "resolution": {"width": 1280, "height": 720},
            },
            {
                "name": "USB Camera 4",
                "enabled": False,
                "fingerprint": generate_fingerprint(),
                "printer_uuid": "REPLACE-WITH-PRINTER-UUID",
                "token": "REPLACE-WITH-CAMERA-TOKEN",
                "device_path": "/dev/video3",
                "driver": "V4L2",
                "trigger_scheme": "THIRTY_SEC",
                "resolution": {"width": 1280, "height": 720},
            },
            {
                "name": "CSI Camera",
                "enabled": False,
                "fingerprint": generate_fingerprint(),
                "printer_uuid": "REPLACE-WITH-PRINTER-UUID",
                "token": "REPLACE-WITH-CAMERA-TOKEN",
                "device_path": "/dev/video0",
                "driver": "CSI",
                "trigger_scheme": "THIRTY_SEC",
                "resolution": {"width": 1280, "height": 720},
            },
        ]
    }

    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(default, f, indent=2)
            f.write("\n")
    except FileExistsError:
        raise RuntimeError(
            f"Config file already exists at {path}; not overwriting."
        ) from None
    except OSError as exc:
        raise RuntimeError(
            f"Cannot write default config to {path}: {exc}"
        ) from exc

    log.info("Default config written to %s — edit it before starting the service.", path)


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Check permissions, parse JSON, validate, and return the config dict."""
    check_permissions(path)
    try:
        with open(path) as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Config file {path} contains invalid JSON: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Cannot read config file {path}: {e}") from e
    raw = _ensure_camera_fingerprints(raw, path)
    return validate_config(raw)


def validate_config(data: dict) -> dict:
    """Validate the parsed config dict. Returns a normalised config dict on success."""
    if not isinstance(data, dict):
        raise RuntimeError("Config root must be a JSON object.")

    for key in data:
        if key not in _KNOWN_TOP_KEYS:
            log.warning("Unknown top-level config key %r will be ignored.", key)

    if "cameras" not in data:
        raise RuntimeError("Config is missing required key 'cameras'.")
    cameras = data["cameras"]
    if not isinstance(cameras, list) or len(cameras) == 0:
        raise RuntimeError("'cameras' must be a non-empty list.")

    for i, cam in enumerate(cameras):
        _validate_camera(cam, i)

    state_dir = data.get("state_dir", DEFAULT_STATE_DIR)
    if not isinstance(state_dir, str) or not state_dir.strip():
        raise RuntimeError("'state_dir' must be a non-empty string path.")

    # Validate all cameras, then filter to only enabled ones.
    active = [c for c in cameras if c.get("enabled", True)]
    if len(active) == 0:
        raise RuntimeError(
            "No cameras are enabled. Set 'enabled': true for at least one camera."
        )

    # Re-check driver counts against only the active set.
    active_usb = sum(1 for c in active if c["driver"] == "V4L2")
    active_csi = sum(1 for c in active if c["driver"] == "CSI")
    if active_usb > MAX_USB_CAMERAS:
        raise RuntimeError(
            f"Too many enabled V4L2 cameras ({active_usb}); maximum is {MAX_USB_CAMERAS}."
        )
    if active_csi > MAX_CSI_CAMERAS:
        raise RuntimeError(
            f"Too many enabled CSI cameras ({active_csi}); maximum is {MAX_CSI_CAMERAS}."
        )

    return {"cameras": active, "state_dir": state_dir}


def _validate_camera(cam: dict, index: int) -> None:
    """Validate a single camera entry. Raises RuntimeError on any problem."""
    if not isinstance(cam, dict):
        raise RuntimeError(f"Camera entry {index} must be a JSON object.")

    for key in cam:
        if key not in _KNOWN_CAMERA_KEYS:
            log.warning("Unknown key %r in camera entry %d will be ignored.", key, index)

    missing = _REQUIRED_CAMERA_KEYS - cam.keys()
    if missing:
        raise RuntimeError(
            f"Camera entry {index} is missing required keys: {', '.join(sorted(missing))}."
        )

    if "enabled" in cam and not isinstance(cam["enabled"], bool):
        raise RuntimeError(
            f"Camera entry {index}: 'enabled' must be a boolean (true or false)."
        )

    if "fingerprint" in cam and (
        not isinstance(cam["fingerprint"], str)
        or not is_valid_fingerprint(cam["fingerprint"])
    ):
        raise RuntimeError(
            f"Camera entry {index}: 'fingerprint' must be a valid UUID string."
        )

    for key in ("firmware", "manufacturer", "model"):
        if key in cam and (not isinstance(cam[key], str) or not cam[key].strip()):
            raise RuntimeError(
                f"Camera entry {index}: '{key}' must be a non-empty string when provided."
            )

    for key in ("name", "printer_uuid", "token", "device_path"):
        if not isinstance(cam[key], str) or not cam[key].strip():
            raise RuntimeError(
                f"Camera entry {index}: '{key}' must be a non-empty string."
            )

    if cam["driver"] not in _VALID_DRIVERS:
        raise RuntimeError(
            f"Camera entry {index}: 'driver' must be one of "
            f"{sorted(_VALID_DRIVERS)}, got {cam['driver']!r}."
        )

    if cam["trigger_scheme"] not in _VALID_TRIGGER_SCHEMES:
        raise RuntimeError(
            f"Camera entry {index}: 'trigger_scheme' must be one of "
            f"{sorted(_VALID_TRIGGER_SCHEMES)}, got {cam['trigger_scheme']!r}. "
            f"Note: EACH_LAYER, FIFTH_LAYER, and GCODE are not supported; "
            f"they require a PrusaLink camera connection."
        )

    if cam["trigger_scheme"] == "TEN_MIN":
        log.warning(
            "Camera entry %d (%r): trigger_scheme 'TEN_MIN' is deprecated; "
            "consider switching to 'SIXTY_SEC'.",
            index, cam["name"],
        )

    res = cam["resolution"]
    if not isinstance(res, dict):
        raise RuntimeError(f"Camera entry {index}: 'resolution' must be a JSON object.")
    for dim in ("width", "height"):
        if dim not in res:
            raise RuntimeError(
                f"Camera entry {index}: 'resolution' is missing required key '{dim}'."
            )
        if not isinstance(res[dim], int) or res[dim] <= 0:
            raise RuntimeError(
                f"Camera entry {index}: 'resolution.{dim}' must be a positive integer."
            )
