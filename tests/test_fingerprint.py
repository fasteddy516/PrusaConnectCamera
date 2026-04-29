"""Tests for fingerprint helpers."""

from prusaconnectcamera.fingerprint import generate_fingerprint, is_valid_fingerprint


class TestGenerateFingerprint:
    def test_creates_uuid_string(self):
        fingerprint = generate_fingerprint()
        assert len(fingerprint) == 36
        assert fingerprint.count("-") == 4
        assert is_valid_fingerprint(fingerprint) is True

    def test_generates_unique_values(self):
        first = generate_fingerprint()
        second = generate_fingerprint()
        assert first != second


class TestIsValidFingerprint:
    def test_accepts_valid_uuid(self):
        assert is_valid_fingerprint("a1b2c3d4-e5f6-7890-abcd-ef1234567890") is True

    def test_rejects_invalid_value(self):
        assert is_valid_fingerprint("not-a-uuid") is False

    def test_rejects_non_string(self):
        assert is_valid_fingerprint(None) is False
