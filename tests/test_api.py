"""Tests for the Prusa Connect API transport layer."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from prusaconnectcamera.api import PrusaConnectAPI, MAX_SNAPSHOT_BYTES

_TOKEN = "T0nSPU2v05v0pJeKYFYV"
_FINGERPRINT = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SMALL_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # minimal fake JPEG header


@pytest.fixture
def api() -> PrusaConnectAPI:
    return PrusaConnectAPI(_TOKEN, _FINGERPRINT)


def _mock_response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# upload_snapshot
# ---------------------------------------------------------------------------


class TestUploadSnapshot:
    def test_204_returns_true(self, api):
        with patch("requests.put", return_value=_mock_response(204)):
            assert api.upload_snapshot(_SMALL_IMAGE) is True

    def test_400_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(400)):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_401_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(401)):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_403_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(403)):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_404_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(404)):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_503_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(503)):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_network_error_returns_false(self, api):
        with patch("requests.put", side_effect=requests.RequestException("timeout")):
            assert api.upload_snapshot(_SMALL_IMAGE) is False

    def test_oversized_snapshot_returns_false_without_request(self, api):
        oversized = b"\x00" * (MAX_SNAPSHOT_BYTES + 1)
        with patch("requests.put") as mock_put:
            result = api.upload_snapshot(oversized)
        assert result is False
        mock_put.assert_not_called()

    def test_exact_max_size_is_allowed(self, api):
        at_limit = b"\x00" * MAX_SNAPSHOT_BYTES
        with patch("requests.put", return_value=_mock_response(204)):
            assert api.upload_snapshot(at_limit) is True

    def test_token_never_appears_in_logs(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(401)):
                api.upload_snapshot(_SMALL_IMAGE)
        assert _TOKEN not in caplog.text

    def test_fingerprint_never_appears_in_logs(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(401)):
                api.upload_snapshot(_SMALL_IMAGE)
        assert _FINGERPRINT not in caplog.text

    def test_401_logs_warning(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(401)):
                api.upload_snapshot(_SMALL_IMAGE)
        assert "401" in caplog.text

    def test_404_logs_warning(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(404)):
                api.upload_snapshot(_SMALL_IMAGE)
        assert "404" in caplog.text

    def test_503_logs_warning(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(503)):
                api.upload_snapshot(_SMALL_IMAGE)
        assert "503" in caplog.text


# ---------------------------------------------------------------------------
# update_info
# ---------------------------------------------------------------------------


class TestUpdateInfo:
    _PAYLOAD = {
        "config": {"name": "Test", "driver": "V4L2", "trigger_scheme": "THIRTY_SEC"},
        "capabilities": ["trigger_scheme", "resolution"],
    }

    def test_200_returns_true(self, api):
        with patch("requests.put", return_value=_mock_response(200)):
            assert api.update_info(self._PAYLOAD) is True

    def test_400_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(400)):
            assert api.update_info(self._PAYLOAD) is False

    def test_401_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(401)):
            assert api.update_info(self._PAYLOAD) is False

    def test_403_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(403)):
            assert api.update_info(self._PAYLOAD) is False

    def test_503_returns_false(self, api):
        with patch("requests.put", return_value=_mock_response(503)):
            assert api.update_info(self._PAYLOAD) is False

    def test_network_error_returns_false(self, api):
        with patch("requests.put", side_effect=requests.RequestException("refused")):
            assert api.update_info(self._PAYLOAD) is False

    def test_token_never_appears_in_logs(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            with patch("requests.put", return_value=_mock_response(403)):
                api.update_info(self._PAYLOAD)
        assert _TOKEN not in caplog.text
