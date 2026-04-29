"""Tests for local RTMP streaming helpers."""

import socket
import tempfile
from pathlib import Path

from prusaconnectcamera.streaming import (
    MediaMTXService,
    build_stream_url,
    stream_host_for_logs,
    stream_path_for_camera,
)


def test_stream_path_for_v4l2_camera():
    assert stream_path_for_camera("V4L2", 1, 1) == "usb/1"


def test_stream_path_for_csi_camera():
    assert stream_path_for_camera("CSI", 1, 1) == "csi/1"


def test_build_stream_url_uses_standard_rtmp_syntax():
    assert build_stream_url("192.168.1.10", 554, "usb/1") == "rtmp://192.168.1.10:554/usb/1"


def test_stream_host_prefers_wifi_ipv4_then_lan_ipv4():
    assert stream_host_for_logs({"wifi_ipv4": "10.0.0.2", "lan_ipv4": "192.168.1.20"}) == "10.0.0.2"
    assert stream_host_for_logs({"lan_ipv4": "192.168.1.20"}) == "192.168.1.20"


def test_stream_host_falls_back_to_loopback():
    assert stream_host_for_logs({}) == "127.0.0.1"


def test_port_is_listening_false_when_port_closed():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = MediaMTXService(tmpdir, 19435)
        assert service._port_is_listening() is False


def test_port_is_listening_true_when_port_open():
    with tempfile.TemporaryDirectory() as tmpdir:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            service = MediaMTXService(tmpdir, port)
            assert service._port_is_listening() is True


def test_cleanup_stale_owned_instance_removes_dead_pidfile():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = MediaMTXService(tmpdir, 19435)
        pid_path = Path(tmpdir) / "mediamtx.pid"
        pid_path.write_text("999999\n", encoding="utf-8")

        service._cleanup_stale_owned_instance()

        assert not pid_path.exists()


def test_cleanup_stale_owned_instance_ignores_missing_pidfile():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = MediaMTXService(tmpdir, 19435)
        service._cleanup_stale_owned_instance()
