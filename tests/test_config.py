"""Tests for configuration loading, schema validation, and permission checks."""

import json
import logging
import os
import tempfile

import pytest

from prusaconnectcamera.config import (
    check_permissions,
    generate_default_config,
    validate_config,
    load_config,
    DEFAULT_STATE_DIR,
)
from prusaconnectcamera.fingerprint import is_valid_fingerprint

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_MINIMAL_CAMERA = {
    "name": "Front Camera",
    "printer_uuid": "cfed5dce-86f4-4d7c-a198-9a81b176369f",
    "token": "T0nSPU2v05v0pJeKYFYV",
    "device_path": "/dev/video0",
    "driver": "V4L2",
    "trigger_scheme": "THIRTY_SEC",
    "resolution": {"width": 1280, "height": 720},
}

_MINIMAL_CONFIG = {"cameras": [_MINIMAL_CAMERA]}


def _write_config(data: dict, mode: int = 0o600) -> str:
    """Write *data* to a temp JSON file with *mode* and return its path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        path = f.name
    os.chmod(path, mode)
    return path


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------


class TestCheckPermissions:
    def test_0600_accepted(self):
        path = _write_config(_MINIMAL_CONFIG, 0o600)
        try:
            check_permissions(path)  # must not raise
        finally:
            os.unlink(path)

    def test_0400_accepted(self):
        path = _write_config(_MINIMAL_CONFIG, 0o400)
        try:
            check_permissions(path)  # must not raise
        finally:
            os.chmod(path, 0o600)
            os.unlink(path)

    def test_0644_rejected(self):
        path = _write_config(_MINIMAL_CONFIG, 0o644)
        try:
            with pytest.raises(RuntimeError, match="too broad"):
                check_permissions(path)
        finally:
            os.unlink(path)

    def test_0640_rejected(self):
        path = _write_config(_MINIMAL_CONFIG, 0o640)
        try:
            with pytest.raises(RuntimeError, match="too broad"):
                check_permissions(path)
        finally:
            os.unlink(path)

    def test_0666_rejected(self):
        path = _write_config(_MINIMAL_CONFIG, 0o666)
        try:
            with pytest.raises(RuntimeError, match="too broad"):
                check_permissions(path)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# validate_config — top-level structure
# ---------------------------------------------------------------------------


class TestValidateConfigStructure:
    def test_valid_minimal_config(self):
        result = validate_config(_MINIMAL_CONFIG)
        assert len(result["cameras"]) == 1

    def test_default_state_dir(self):
        result = validate_config(_MINIMAL_CONFIG)
        assert result["state_dir"] == DEFAULT_STATE_DIR

    def test_state_dir_override(self):
        data = {**_MINIMAL_CONFIG, "state_dir": "/tmp/test_state"}
        result = validate_config(data)
        assert result["state_dir"] == "/tmp/test_state"

    def test_default_rtmp_port(self):
        result = validate_config(_MINIMAL_CONFIG)
        assert result["rtmp_port"] == 1935

    def test_rtmp_port_override(self):
        result = validate_config({**_MINIMAL_CONFIG, "rtmp_port": 8554})
        assert result["rtmp_port"] == 8554

    def test_invalid_rtmp_port_fails(self):
        with pytest.raises(RuntimeError, match="rtmp_port"):
            validate_config({**_MINIMAL_CONFIG, "rtmp_port": 70000})

    def test_missing_cameras_key_fails(self):
        with pytest.raises(RuntimeError, match="cameras"):
            validate_config({})

    def test_empty_cameras_list_fails(self):
        with pytest.raises(RuntimeError):
            validate_config({"cameras": []})

    def test_too_many_v4l2_cameras_fails(self):
        cameras = [{**_MINIMAL_CAMERA, "name": f"Cam {i}", "enabled": True} for i in range(5)]
        with pytest.raises(RuntimeError, match="V4L2"):
            validate_config({"cameras": cameras})

    def test_too_many_csi_cameras_fails(self):
        csi = {**_MINIMAL_CAMERA, "driver": "CSI", "enabled": True}
        with pytest.raises(RuntimeError, match="CSI"):
            validate_config({"cameras": [csi, {**csi, "name": "CSI 2"}]})

    def test_unknown_top_key_warns(self, caplog):
        data = {**_MINIMAL_CONFIG, "bogus_key": "value"}
        with caplog.at_level(logging.WARNING, logger="prusaconnectcamera.config"):
            validate_config(data)
        assert "bogus_key" in caplog.text


# ---------------------------------------------------------------------------
# validate_config — per-camera validation
# ---------------------------------------------------------------------------


class TestValidateCamera:
    def test_missing_required_key_fails(self):
        cam = dict(_MINIMAL_CAMERA)
        del cam["token"]
        with pytest.raises(RuntimeError, match="token"):
            validate_config({"cameras": [cam]})

    def test_empty_string_field_fails(self):
        cam = {**_MINIMAL_CAMERA, "name": "  "}
        with pytest.raises(RuntimeError, match="name"):
            validate_config({"cameras": [cam]})

    def test_invalid_driver_fails(self):
        cam = {**_MINIMAL_CAMERA, "driver": "BOGUS"}
        with pytest.raises(RuntimeError, match="driver"):
            validate_config({"cameras": [cam]})

    def test_unsupported_trigger_scheme_fails(self):
        for scheme in ("EACH_LAYER", "FIFTH_LAYER", "GCODE"):
            cam = {**_MINIMAL_CAMERA, "trigger_scheme": scheme}
            with pytest.raises(RuntimeError, match="trigger_scheme"):
                validate_config({"cameras": [cam]})

    def test_deprecated_ten_min_warns(self, caplog):
        cam = {**_MINIMAL_CAMERA, "trigger_scheme": "TEN_MIN"}
        with caplog.at_level(logging.WARNING, logger="prusaconnectcamera.config"):
            validate_config({"cameras": [cam]})
        assert "TEN_MIN" in caplog.text
        assert "deprecated" in caplog.text.lower()

    def test_resolution_missing_key_fails(self):
        cam = {**_MINIMAL_CAMERA, "resolution": {"width": 1280}}
        with pytest.raises(RuntimeError, match="height"):
            validate_config({"cameras": [cam]})

    def test_resolution_non_positive_fails(self):
        cam = {**_MINIMAL_CAMERA, "resolution": {"width": 0, "height": 720}}
        with pytest.raises(RuntimeError, match="width"):
            validate_config({"cameras": [cam]})

    def test_unknown_camera_key_warns(self, caplog):
        cam = {**_MINIMAL_CAMERA, "undocumented_option": True}
        with caplog.at_level(logging.WARNING, logger="prusaconnectcamera.config"):
            validate_config({"cameras": [cam]})
        assert "undocumented_option" in caplog.text

    def test_csi_and_v4l2_cameras_together(self):
        csi = {**_MINIMAL_CAMERA, "name": "Pi Camera", "driver": "CSI"}
        result = validate_config({"cameras": [_MINIMAL_CAMERA, csi]})
        assert len(result["cameras"]) == 2

    def test_disabled_camera_excluded_from_result(self):
        cam_on = {**_MINIMAL_CAMERA, "name": "On", "enabled": True}
        cam_off = {**_MINIMAL_CAMERA, "name": "Off", "enabled": False}
        result = validate_config({"cameras": [cam_on, cam_off]})
        assert len(result["cameras"]) == 1
        assert result["cameras"][0]["name"] == "On"

    def test_camera_enabled_defaults_to_true(self):
        # A camera with no 'enabled' key should be treated as enabled.
        result = validate_config({"cameras": [_MINIMAL_CAMERA]})
        assert len(result["cameras"]) == 1

    def test_streaming_defaults_to_true(self):
        result = validate_config({"cameras": [_MINIMAL_CAMERA]})
        assert result["cameras"][0]["streaming"] is True

    def test_fps_defaults_to_30(self):
        result = validate_config({"cameras": [_MINIMAL_CAMERA]})
        assert result["cameras"][0]["fps"] == 30

    def test_bitrate_defaults_to_2500(self):
        result = validate_config({"cameras": [_MINIMAL_CAMERA]})
        assert result["cameras"][0]["bitrate"] == 2500

    def test_all_disabled_cameras_fails(self):
        cam = {**_MINIMAL_CAMERA, "enabled": False}
        with pytest.raises(RuntimeError, match="No cameras are enabled"):
            validate_config({"cameras": [cam]})

    def test_enabled_non_bool_fails(self):
        cam = {**_MINIMAL_CAMERA, "enabled": "yes"}
        with pytest.raises(RuntimeError, match="enabled"):
            validate_config({"cameras": [cam]})

    def test_streaming_non_bool_fails(self):
        cam = {**_MINIMAL_CAMERA, "streaming": "yes"}
        with pytest.raises(RuntimeError, match="streaming"):
            validate_config({"cameras": [cam]})

    def test_fps_non_positive_fails(self):
        cam = {**_MINIMAL_CAMERA, "fps": 0}
        with pytest.raises(RuntimeError, match="fps"):
            validate_config({"cameras": [cam]})

    def test_bitrate_non_positive_fails(self):
        cam = {**_MINIMAL_CAMERA, "bitrate": 0}
        with pytest.raises(RuntimeError, match="bitrate"):
            validate_config({"cameras": [cam]})

    def test_disabled_cameras_do_not_count_toward_v4l2_limit(self):
        # 4 enabled + 1 disabled V4L2 = should pass (4 is the limit)
        enabled = [{**_MINIMAL_CAMERA, "name": f"Cam {i}", "enabled": True} for i in range(4)]
        disabled = [{**_MINIMAL_CAMERA, "name": "Cam disabled", "enabled": False}]
        result = validate_config({"cameras": enabled + disabled})
        assert len(result["cameras"]) == 4

    def test_invalid_fingerprint_fails(self):
        cam = {**_MINIMAL_CAMERA, "fingerprint": "not-a-uuid"}
        with pytest.raises(RuntimeError, match="fingerprint"):
            validate_config({"cameras": [cam]})

    def test_optional_metadata_fields_are_allowed(self):
        cam = {
            **_MINIMAL_CAMERA,
            "firmware": "1.2.3",
            "manufacturer": "MyCamCo",
            "model": "Model X",
        }
        result = validate_config({"cameras": [cam]})
        assert result["cameras"][0]["firmware"] == "1.2.3"
        assert result["cameras"][0]["manufacturer"] == "MyCamCo"
        assert result["cameras"][0]["model"] == "Model X"

    def test_optional_metadata_fields_must_be_non_empty_strings(self):
        for key in ("firmware", "manufacturer", "model"):
            cam = {**_MINIMAL_CAMERA, key: ""}
            with pytest.raises(RuntimeError, match=key):
                validate_config({"cameras": [cam]})


# ---------------------------------------------------------------------------
# generate_default_config
# ---------------------------------------------------------------------------


class TestGenerateDefaultConfig:
    def test_creates_file_with_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600

    def test_creates_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            assert "cameras" in data
            assert len(data["cameras"]) == 5
            assert data["rtmp_port"] == 1935

    def test_only_first_camera_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            enabled = [c for c in data["cameras"] if c.get("enabled")]
            assert len(enabled) == 1
            assert enabled[0]["driver"] == "V4L2"

    def test_all_drivers_present(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            drivers = [c["driver"] for c in data["cameras"]]
            assert drivers.count("V4L2") == 4
            assert drivers.count("CSI") == 1

    def test_generated_default_config_contains_fingerprints(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            for camera in data["cameras"]:
                assert is_valid_fingerprint(camera["fingerprint"])

    def test_generated_default_config_omits_optional_metadata_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            for camera in data["cameras"]:
                assert "firmware" not in camera
                assert "manufacturer" not in camera
                assert "model" not in camera

    def test_generated_default_config_includes_streaming_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with open(path) as f:
                data = json.load(f)
            for camera in data["cameras"]:
                assert camera["streaming"] is True
                assert camera["fps"] == 30
                assert camera["bitrate"] == 2500

    def test_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            generate_default_config(path)
            with pytest.raises(RuntimeError, match="already exists"):
                generate_default_config(path)

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "dir", "config.json")
            generate_default_config(path)
            assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# load_config integration
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_loads_valid_file(self):
        path = _write_config(_MINIMAL_CONFIG, 0o600)
        try:
            result = load_config(path)
            assert result["cameras"][0]["name"] == "Front Camera"
            assert is_valid_fingerprint(result["cameras"][0]["fingerprint"])
        finally:
            os.unlink(path)

    def test_missing_fingerprint_is_generated_and_persisted(self):
        path = _write_config(_MINIMAL_CONFIG, 0o600)
        try:
            result = load_config(path)
            fingerprint = result["cameras"][0]["fingerprint"]
            assert is_valid_fingerprint(fingerprint)
            with open(path) as f:
                persisted = json.load(f)
            assert persisted["cameras"][0]["fingerprint"] == fingerprint
        finally:
            os.unlink(path)

    def test_existing_fingerprint_is_preserved(self):
        fingerprint = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        path = _write_config({"cameras": [{**_MINIMAL_CAMERA, "fingerprint": fingerprint}]}, 0o600)
        try:
            result = load_config(path)
            assert result["cameras"][0]["fingerprint"] == fingerprint
            with open(path) as f:
                persisted = json.load(f)
            assert persisted["cameras"][0]["fingerprint"] == fingerprint
        finally:
            os.unlink(path)

    def test_missing_fingerprint_in_read_only_config_fails(self):
        path = _write_config(_MINIMAL_CONFIG, 0o400)
        try:
            with pytest.raises(RuntimeError, match="missing one or more camera fingerprints"):
                load_config(path)
        finally:
            os.chmod(path, 0o600)
            os.unlink(path)

    def test_rejects_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{ not valid json }")
            path = f.name
        os.chmod(path, 0o600)
        try:
            with pytest.raises(RuntimeError, match="invalid JSON"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_broad_permissions(self):
        path = _write_config(_MINIMAL_CONFIG, 0o644)
        try:
            with pytest.raises(RuntimeError, match="too broad"):
                load_config(path)
        finally:
            os.unlink(path)
