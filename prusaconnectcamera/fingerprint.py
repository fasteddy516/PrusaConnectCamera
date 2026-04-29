"""Camera fingerprint generation and persistence.

Each camera gets a UUID-based fingerprint that is generated once and stored on
disk so that Prusa Connect sees a stable device identity across restarts and
reboots.
"""

import logging
import os
import re
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """Return a filesystem-safe version of a camera name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()


def load_or_create(camera_name: str, state_dir: str) -> str:
    """Return the persisted fingerprint for *camera_name*, creating it if absent.

    The fingerprint file is stored at ``{state_dir}/{slug}.fingerprint`` with
    mode 0600.  Raises RuntimeError if the state directory cannot be created or
    the fingerprint cannot be written.
    """
    state_path = Path(state_dir)
    try:
        state_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create state directory {state_dir}: {exc}"
        ) from exc

    fp_file = state_path / f"{_slug(camera_name)}.fingerprint"

    if fp_file.exists():
        try:
            fingerprint = fp_file.read_text().strip()
            if fingerprint:
                log.info("Loaded existing fingerprint for camera %r.", camera_name)
                return fingerprint
            log.warning(
                "Fingerprint file %s is empty; regenerating.", fp_file
            )
        except OSError as exc:
            log.warning(
                "Cannot read fingerprint file %s: %s — regenerating.", fp_file, exc
            )

    fingerprint = str(uuid.uuid4())
    try:
        fp_file.write_text(fingerprint)
        os.chmod(fp_file, 0o600)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot persist fingerprint for camera {camera_name!r} to {fp_file}: {exc}"
        ) from exc

    log.info("Generated new fingerprint for camera %r.", camera_name)
    return fingerprint
