# PrusaConnectCamera

A Python service that connects USB webcams and a Raspberry Pi CSI camera to
[Prusa Connect](https://connect.prusa3d.com/) via the
[Prusa Connect Camera API](https://connect.prusa3d.com/docs/cameras/).

Designed to run headlessly on a Raspberry Pi as a `systemd` service. Supports
up to four USB (V4L2) cameras and one CSI camera, each independently associated
with any printer registered in your Prusa Connect account.

Please note that this project should be considered experimental, as it has not
been thoroughly tested. The code itself was written entirely by GitHub Copilot,
with guidance from @fasteddy516.

---

## Requirements

### Hardware

- Raspberry Pi (any model with USB ports; CSI connector required for Pi Camera)
- One or more USB webcams and/or a Raspberry Pi Camera Module

### Operating System

- Raspberry Pi OS 64-bit Light, **Trixie** or later (headless, no desktop)

---

## Production Installation (Step by Step)

Follow these steps in order to install and run a production instance with
`systemd`.

### 1. Install required OS packages

```bash
sudo apt update
sudo apt install -y ffmpeg libcamera-tools wireless-tools
```

- `ffmpeg` is required for USB/V4L2 capture and RTSP snapshot handling.
- `libcamera-tools` (provides `libcamera-still` and `libcamera-vid`) is
  required for CSI capture/streaming.
- `wireless-tools` (provides `iwgetid`) enables `wifi_ssid` reporting when the
  Pi is on Wi-Fi.

### 2. Install MediaMTX

MediaMTX is required for local RTSP streaming. Install it once; the Python
service auto-generates MediaMTX config and starts/stops it as needed.

```bash
cd /tmp
curl -fsSL https://github.com/bluenviron/mediamtx/releases/download/v1.18.0/mediamtx_v1.18.0_linux_arm64.tar.gz -o mediamtx_linux_arm64.tar.gz
tar -xzf mediamtx_linux_arm64.tar.gz
sudo install -m 0755 mediamtx /usr/local/bin/mediamtx
mediamtx --version
```

**Note:** This application has been tested with MediaMTX v1.18.0. Newer
versions may work but have not been tested. Use the appropriate MediaMTX
artifact for your Pi architecture if you are not on arm64.

### 3. Create a dedicated service user

```bash
sudo useradd --system --no-create-home prusaconnectcamera
sudo usermod -aG video prusaconnectcamera
```

### 4. Install the application in `/opt/prusaconnectcamera`

```bash
cd /tmp
git clone https://github.com/fasteddy516/PrusaConnectCamera.git
sudo rm -rf /opt/prusaconnectcamera
sudo mkdir -p /opt/prusaconnectcamera
sudo cp -r /tmp/PrusaConnectCamera/. /opt/prusaconnectcamera
sudo chown -R prusaconnectcamera:prusaconnectcamera /opt/prusaconnectcamera
```

### 5. Create venv and install Python dependencies

```bash
sudo -u prusaconnectcamera python3 -m venv /opt/prusaconnectcamera/.venv
sudo -u prusaconnectcamera /opt/prusaconnectcamera/.venv/bin/pip install -r /opt/prusaconnectcamera/requirements.txt
```

### 6. Register cameras in Prusa Connect

Before configuring this service, register each camera in
[Prusa Connect](https://connect.prusa3d.com/):

1. Open your printer in Prusa Connect.
2. Go to **Camera** -> **Add new camera** -> choose **Other**.
3. Note the generated **token**.
4. Note the **Printer UUID** (visible in the URL or printer settings).

### 7. Generate and edit the production config file

If the config file does not exist, the app generates a template and exits.

```bash
sudo -u prusaconnectcamera /opt/prusaconnectcamera/.venv/bin/python /opt/prusaconnectcamera/prusaconnectcamera.py --config /opt/prusaconnectcamera/config.json
sudo -u prusaconnectcamera nano /opt/prusaconnectcamera/config.json
```

The config file must be readable only by its owner (`0600` or `0400`). The
generated file is created as `0600` automatically.

### 8. Configure stable device paths (recommended for multiple cameras)

If you have more than one camera, especially identical models, use UDEV rules
to assign persistent symlinks so `device_path` stays deterministic.

Create `/etc/udev/rules.d/99-cameras.rules`:

```
# Replace ATTRS{serial} with values from: udevadm info /dev/videoN
SUBSYSTEM=="video4linux", ATTRS{serial}=="ABC123", SYMLINK+="camera_front"
SUBSYSTEM=="video4linux", ATTRS{serial}=="XYZ789", SYMLINK+="camera_side"
```

Then use `/dev/camera_front` and `/dev/camera_side` in `device_path`.

Reload and apply the new rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux
sudo udevadm settle
```

Verify the links:

```bash
ls -l /dev/camera_*
```

If links do not appear, unplug/replug the camera and verify match keys with:

```bash
udevadm info -a -n /dev/video0 | grep -E 'serial|idVendor|idProduct' -m 10
```

Some webcams do not expose a usable `ATTRS{serial}`. In that case, prefer
stable built-in paths under `/dev/v4l/by-id/` (or match on other attributes
such as `idVendor` + `idProduct` plus a unique physical port path).

### 9. Install and start the systemd service

```bash
sudo cp /opt/prusaconnectcamera/prusaconnectcamera.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prusaconnectcamera
```

### 10. Verify service health

```bash
sudo systemctl status prusaconnectcamera
sudo journalctl -u prusaconnectcamera -f
```

Common operational command:

```bash
sudo systemctl restart prusaconnectcamera
```

---

## Configuration Reference

The JSON config file has the following structure. All five camera slots are
generated by default; enable only the ones you are using.

```jsonc
{
  "state_dir": "./state",   // optional, default shown
  "rtsp_port": 8554,         // optional, default shown
  "cameras": [
    {
      "name": "USB Camera 1",
      "enabled": true,
      "streaming": true,
      "fps": 30,
      "bitrate": 2500,
      "fingerprint": "6f4a9788-f446-4f56-8f96-345d7d7a2d16",
      "printer_uuid": "cfed5dce-86f4-4d7c-a198-9a81b176369f",
      "token": "T0nSPU2v05v0pJeKYFYV",
      "device_path": "/dev/video0",
      "driver": "V4L2",
      "trigger_scheme": "THIRTY_SEC",
      "resolution": { "width": 1280, "height": 720 }
    },
    {
      "name": "CSI Camera",
      "enabled": false,
      "streaming": true,
      "fps": 30,
      "bitrate": 2500,
      "printer_uuid": "cfed5dce-86f4-4d7c-a198-9a81b176369f",
      "token": "AnotherToken1234567",
      "device_path": "/dev/video0",
      "driver": "CSI",
      "trigger_scheme": "THIRTY_SEC",
      "resolution": { "width": 1920, "height": 1080 }
    }
  ]
}
```

### Per-camera fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Human-readable label shown in logs |
| `enabled` | no | `true` (default) or `false`; disabled cameras are ignored at startup |
| `streaming` | no | `true` (default) enables local RTSP streaming for this camera |
| `fps` | no | Streaming frame rate in FPS; default `30` |
| `bitrate` | no | Streaming target bitrate in kbps; default `2500` |
| `fingerprint` | no | Stable camera identity sent to Prusa Connect; auto-generated on first load and written back into config |
| `printer_uuid` | yes | UUID of the printer this camera is associated with |
| `token` | yes | Camera token from Prusa Connect camera registration |
| `device_path` | yes | V4L2: path to video device; CSI: ignored but must be present |
| `driver` | yes | `"V4L2"` for USB webcams, `"CSI"` for Raspberry Pi camera |
| `trigger_scheme` | yes | See table below |
| `resolution` | yes | Object with `width` and `height` in pixels |
| `firmware` | no | Camera firmware string sent to Connect; defaults to script version |
| `manufacturer` | no | Camera manufacturer string sent to Connect; defaults to `fasteddy516` |
| `model` | no | Camera model string sent to Connect; defaults to `PrusaConnectCamera #<camera number> [<driver>] via Raspberry Pi` |

If a camera entry is missing `fingerprint`, the application generates one and
writes it back into the config file. Deleting a fingerprint and restarting is
the supported way to rotate it.

Network info (`network_info`) is collected once at startup from Pi network
state and is not a config key. When the default route is wireless, the service
reports `wifi_mac`, `wifi_ipv4`, and `wifi_ssid` (SSID is omitted if `iwgetid`
is unavailable). When wired, it reports `lan_mac` and `lan_ipv4`. IPv6 is
included only when IPv4 is unavailable.

### Trigger schemes

| Value | Interval | Notes |
|---|---|---|
| `MANUAL` | - | No automatic snapshots (no-op for now) |
| `TEN_SEC` | 10 s | |
| `THIRTY_SEC` | 30 s | Default |
| `SIXTY_SEC` | 60 s | |
| `TEN_MIN` | 600 s | Deprecated by Prusa; use `SIXTY_SEC` instead |

### Local RTSP streaming

When `streaming` is enabled for a camera, the app starts local MediaMTX
automatically and publishes video-only H.264 streams over RTSP.

Snapshot uploads for streaming-enabled cameras are read from the local RTSP
stream instead of reopening the camera device directly. This avoids device
contention between the live publisher and snapshot worker.

When `streaming` is disabled for a camera, snapshots are still captured and
uploaded by reading directly from the camera backend (V4L2 or CSI).

The default streaming pipeline is tuned for lower latency (short GOP/keyframe
interval and reduced mux/encoder buffering) to keep video closer to real time
on Raspberry Pi hardware.

- RTSP URL syntax: `rtsp://<pi-ip>:<rtsp_port>/<path>`
- USB camera paths: `usb/1` through `usb/4`
- CSI camera path: `csi/1`
- Default RTSP port: `8554`

If you choose a privileged port (below 1024, for example 554), the process
must run with permission to bind that port (typically root or
CAP_NET_BIND_SERVICE).

Examples:

- `rtsp://192.168.1.50:8554/usb/1`
- `rtsp://192.168.1.50:8554/csi/1`

The service logs stream URLs on startup for each enabled camera with streaming
enabled.

MediaMTX runtime files are written under `state_dir`:

- `mediamtx.yml` (auto-generated RTSP-only server config)
- `mediamtx.log` (MediaMTX stdout/stderr)
- `mediamtx.pid` (PID of app-owned MediaMTX instance)

If another MediaMTX instance is already listening on `rtsp_port`, the app
reuses it instead of starting its own copy.

The RTSP endpoint may not be readable immediately at process start. The service
waits for the stream to become readable before starting scheduled snapshots and
logs `RTSP stream is ready for snapshots` when that handoff is complete.

Client players can still add their own buffering. For lowest latency, select
any low-latency/live mode in your player and reduce client-side network cache.

---

## Development

Development and testing workflow has moved to [DEVELOPMENT.md](DEVELOPMENT.md).

---

## License

See [LICENSE](LICENSE).
