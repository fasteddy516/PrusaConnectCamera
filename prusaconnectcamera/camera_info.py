"""Helpers for building Prusa Connect camera attribute payloads."""

from __future__ import annotations

DEFAULT_MANUFACTURER = "fasteddy516"


def build_info_payload(
    camera: dict,
    camera_number: int,
    script_version: str,
    network_info: dict,
) -> dict:
    """Build the PUT /c/info request body from camera config and runtime context."""
    firmware = camera.get("firmware") or script_version
    manufacturer = camera.get("manufacturer") or DEFAULT_MANUFACTURER
    model = camera.get("model") or (
        f"PrusaConnectCamera #{camera_number} [{camera['driver']}] via Raspberry Pi"
    )

    config = {
        "path": camera["device_path"],
        "name": camera["name"],
        "driver": camera["driver"],
        "trigger_scheme": camera["trigger_scheme"],
        "resolution": {
            "width": camera["resolution"]["width"],
            "height": camera["resolution"]["height"],
        },
        "firmware": firmware,
        "manufacturer": manufacturer,
        "model": model,
    }

    if network_info:
        config["network_info"] = network_info

    return {
        "config": config,
        "capabilities": ["trigger_scheme", "resolution"],
    }
