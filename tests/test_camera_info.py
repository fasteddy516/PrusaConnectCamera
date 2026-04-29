"""Tests for camera attribute payload generation."""

from prusaconnectcamera.camera_info import build_info_payload


def _camera_overrides(**overrides):
    camera = {
        "name": "USB Camera 1",
        "device_path": "/dev/video0",
        "driver": "V4L2",
        "trigger_scheme": "THIRTY_SEC",
        "resolution": {"width": 1280, "height": 720},
    }
    camera.update(overrides)
    return camera


def test_defaults_include_resolution_and_metadata():
    payload = build_info_payload(
        camera=_camera_overrides(),
        camera_number=1,
        script_version="0.1.0",
        network_info={"lan_mac": "aa:bb:cc:dd:ee:ff", "lan_ipv4": "192.168.1.20"},
    )

    config = payload["config"]
    assert config["resolution"] == {"width": 1280, "height": 720}
    assert config["firmware"] == "0.1.0"
    assert config["manufacturer"] == "fasteddy516"
    assert config["model"] == "PrusaConnectCamera #1 [V4L2] via Raspberry Pi"
    assert config["network_info"] == {
        "lan_mac": "aa:bb:cc:dd:ee:ff",
        "lan_ipv4": "192.168.1.20",
    }


def test_optional_overrides_are_used_from_config():
    payload = build_info_payload(
        camera=_camera_overrides(
            firmware="2.3.4",
            manufacturer="Logitech",
            model="C920",
        ),
        camera_number=3,
        script_version="0.1.0",
        network_info={},
    )

    config = payload["config"]
    assert config["firmware"] == "2.3.4"
    assert config["manufacturer"] == "Logitech"
    assert config["model"] == "C920"
    assert "network_info" not in config


def test_capabilities_include_resolution():
    payload = build_info_payload(
        camera=_camera_overrides(),
        camera_number=1,
        script_version="0.1.0",
        network_info={},
    )

    assert payload["capabilities"] == ["trigger_scheme", "resolution"]
