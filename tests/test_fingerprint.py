"""Tests for fingerprint generation and persistence."""

import os
import tempfile

from prusaconnectcamera.fingerprint import _slug, load_or_create


class TestSlug:
    def test_lowercase(self):
        assert _slug("MyCamera") == "mycamera"

    def test_spaces_replaced(self):
        assert _slug("Front Camera") == "front_camera"

    def test_special_chars_replaced(self):
        assert _slug("Cam/1:2") == "cam_1_2"

    def test_hyphens_preserved(self):
        assert _slug("pi-cam") == "pi-cam"


class TestLoadOrCreate:
    def test_creates_new_fingerprint(self):
        with tempfile.TemporaryDirectory() as state_dir:
            fp = load_or_create("Test Camera", state_dir)
            assert fp
            # UUID4 format: 8-4-4-4-12
            assert len(fp) == 36
            assert fp.count("-") == 4

    def test_persists_on_second_call(self):
        with tempfile.TemporaryDirectory() as state_dir:
            fp1 = load_or_create("Test Camera", state_dir)
            fp2 = load_or_create("Test Camera", state_dir)
            assert fp1 == fp2

    def test_different_names_get_different_fingerprints(self):
        with tempfile.TemporaryDirectory() as state_dir:
            fp_a = load_or_create("Camera A", state_dir)
            fp_b = load_or_create("Camera B", state_dir)
            assert fp_a != fp_b

    def test_fingerprint_file_has_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as state_dir:
            load_or_create("Test Camera", state_dir)
            fp_file = os.path.join(state_dir, "test_camera.fingerprint")
            mode = stat_mode(fp_file)
            assert mode == 0o600

    def test_state_dir_created_if_absent(self):
        with tempfile.TemporaryDirectory() as base:
            state_dir = os.path.join(base, "nested", "state")
            fp = load_or_create("Test Camera", state_dir)
            assert fp
            assert os.path.isdir(state_dir)

    def test_stable_across_simulated_restarts(self):
        """Calling load_or_create after process restart should return same value."""
        with tempfile.TemporaryDirectory() as state_dir:
            first_run = load_or_create("Stable Camera", state_dir)
            # Simulate restart by calling again (same file, different call stack)
            second_run = load_or_create("Stable Camera", state_dir)
            third_run = load_or_create("Stable Camera", state_dir)
            assert first_run == second_run == third_run

    def test_empty_file_triggers_regeneration(self):
        with tempfile.TemporaryDirectory() as state_dir:
            # Pre-create an empty fingerprint file
            fp_file = os.path.join(state_dir, "test_camera.fingerprint")
            with open(fp_file, "w"):
                pass
            os.chmod(fp_file, 0o600)
            fp = load_or_create("Test Camera", state_dir)
            # Should have generated a new UUID
            assert len(fp) == 36


def stat_mode(path: str) -> int:
    """Return the permission bits for *path*."""
    return os.stat(path).st_mode & 0o777
