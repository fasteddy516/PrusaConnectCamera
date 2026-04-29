"""Tests for the per-camera scheduler, worker loop, and backoff logic."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from prusaconnectcamera.capture import CaptureError
from prusaconnectcamera.scheduler import (
    CameraWorker,
    TRIGGER_INTERVALS,
    _BACKOFF_INITIAL,
    _BACKOFF_MAX,
    _BACKOFF_JITTER,
    backoff_delay,
)

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(
    trigger_scheme: str,
    api: MagicMock | None = None,
    backend: MagicMock | None = None,
    stop_event: threading.Event | None = None,
) -> CameraWorker:
    config = {
        "name": "Test Camera",
        "trigger_scheme": trigger_scheme,
        "resolution": {"width": 1280, "height": 720},
    }
    api = api or MagicMock()
    backend = backend or MagicMock()
    backend.capture.return_value = _FAKE_JPEG
    stop_event = stop_event or threading.Event()
    return CameraWorker(config, api, backend, stop_event)


# ---------------------------------------------------------------------------
# backoff_delay
# ---------------------------------------------------------------------------


class TestBackoffDelay:
    def test_attempt_0_within_jitter_range(self):
        for _ in range(20):
            d = backoff_delay(0)
            assert _BACKOFF_INITIAL * (1 - _BACKOFF_JITTER) <= d <= _BACKOFF_INITIAL * (1 + _BACKOFF_JITTER)

    def test_delay_grows_with_attempt(self):
        # At attempt 0 max is 2s*1.2=2.4; at attempt 10 min is 60*0.8=48 > 2.4
        low = backoff_delay(0)
        high = backoff_delay(10)
        assert high > low

    def test_capped_at_maximum_with_jitter(self):
        for _ in range(20):
            d = backoff_delay(100)
            assert d <= _BACKOFF_MAX * (1 + _BACKOFF_JITTER)

    def test_minimum_never_below_zero(self):
        for attempt in range(10):
            assert backoff_delay(attempt) > 0


# ---------------------------------------------------------------------------
# MANUAL trigger — no-op
# ---------------------------------------------------------------------------


class TestManualTrigger:
    def test_manual_does_not_call_capture(self):
        api = MagicMock()
        backend = MagicMock()
        stop_event = threading.Event()
        worker = _make_worker("MANUAL", api=api, backend=backend, stop_event=stop_event)

        stop_event.set()  # signal stop before run() blocks
        worker.run()

        backend.capture.assert_not_called()

    def test_manual_does_not_call_upload(self):
        api = MagicMock()
        backend = MagicMock()
        stop_event = threading.Event()
        worker = _make_worker("MANUAL", api=api, backend=backend, stop_event=stop_event)

        stop_event.set()
        worker.run()

        api.upload_snapshot.assert_not_called()

    def test_manual_returns_when_stop_set(self):
        stop_event = threading.Event()
        worker = _make_worker("MANUAL", stop_event=stop_event)

        stop_event.set()
        # Should return promptly without hanging
        worker.run()


# ---------------------------------------------------------------------------
# Time-based triggers — interval mapping
# ---------------------------------------------------------------------------


class TestTriggerIntervals:
    def test_all_time_based_schemes_defined(self):
        for scheme in ("TEN_SEC", "THIRTY_SEC", "SIXTY_SEC", "TEN_MIN"):
            assert scheme in TRIGGER_INTERVALS

    def test_interval_values(self):
        assert TRIGGER_INTERVALS["TEN_SEC"] == 10
        assert TRIGGER_INTERVALS["THIRTY_SEC"] == 30
        assert TRIGGER_INTERVALS["SIXTY_SEC"] == 60
        assert TRIGGER_INTERVALS["TEN_MIN"] == 600


# ---------------------------------------------------------------------------
# Worker loop — success path
# ---------------------------------------------------------------------------


class TestWorkerSuccessPath:
    def test_worker_calls_capture_and_upload(self):
        api = MagicMock()
        backend = MagicMock()
        backend.capture.return_value = _FAKE_JPEG
        stop_event = threading.Event()

        # upload_snapshot returns True, then the stop event fires
        def _upload(data):
            stop_event.set()
            return True

        api.upload_snapshot.side_effect = _upload
        worker = _make_worker("TEN_SEC", api=api, backend=backend, stop_event=stop_event)

        # Patch stop_event.wait so we don't sleep during test
        with patch.object(stop_event, "wait", side_effect=lambda *a: stop_event.is_set()):
            worker.run()

        backend.capture.assert_called_once()
        api.upload_snapshot.assert_called_once_with(_FAKE_JPEG)


# ---------------------------------------------------------------------------
# Worker loop — failure / backoff path
# ---------------------------------------------------------------------------


class TestWorkerBackoff:
    def test_backoff_called_on_upload_failure(self):
        api = MagicMock()
        backend = MagicMock()
        backend.capture.return_value = _FAKE_JPEG
        stop_event = threading.Event()

        upload_calls = []

        def _fail_then_stop(data):
            upload_calls.append(data)
            stop_event.set()
            return False

        api.upload_snapshot.side_effect = _fail_then_stop
        worker = _make_worker("TEN_SEC", api=api, backend=backend, stop_event=stop_event)

        with patch("prusaconnectcamera.scheduler.backoff_delay", return_value=0.0) as mock_backoff:
            with patch.object(stop_event, "wait", side_effect=lambda *a: stop_event.is_set()):
                worker.run()

        mock_backoff.assert_called_once_with(0)

    def test_capture_error_triggers_backoff(self):
        api = MagicMock()
        backend = MagicMock()
        backend.capture.side_effect = CaptureError("device busy")
        stop_event = threading.Event()

        call_count = 0

        def _capture_fail():
            nonlocal call_count
            call_count += 1
            stop_event.set()
            raise CaptureError("device busy")

        backend.capture.side_effect = _capture_fail
        worker = _make_worker("THIRTY_SEC", api=api, backend=backend, stop_event=stop_event)

        with patch("prusaconnectcamera.scheduler.backoff_delay", return_value=0.0) as mock_backoff:
            with patch.object(stop_event, "wait", side_effect=lambda *a: stop_event.is_set()):
                worker.run()

        mock_backoff.assert_called_once()
        api.upload_snapshot.assert_not_called()
