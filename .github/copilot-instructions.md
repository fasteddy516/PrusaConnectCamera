# Copilot Instructions for PrusaConnectCamera

## Project Purpose

This repository is for a Python service that connects USB webcams and/or a Raspberry Pi CSI camera to Prusa Connect using the Prusa Connect camera API. The service runs headlessly on a Raspberry Pi and supports up to four USB (V4L2) cameras and one CSI camera simultaneously, each independently associated with any printer in your Prusa Connect account.

## Reference Documentation

- Prusa Connect: https://connect.prusa3d.com/
- Prusa Connect Camera API docs: https://connect.prusa3d.com/docs/cameras/
- Camera registration flow: https://connect.prusa3d.com/docs/cameras/camera_registration/
- Camera communication flow: https://connect.prusa3d.com/docs/cameras/camera_communication/
- OpenAPI specification: https://connect.prusa3d.com/docs/cameras/openapi/

## Target Environment

- Target hardware is a headless Raspberry Pi.
- Target OS is the latest Raspberry Pi OS 64-bit Light release, currently Trixie.
- Assume no desktop environment and no GUI-dependent workflow.
- Prefer solutions that work reliably with USB webcams on Linux.
- Prefer standard Linux and Raspberry Pi tooling over desktop-specific approaches.

## Python and Dependency Management

- The application should run inside a Python virtual environment.
- Keep third-party dependencies explicit and easy to install from a `requirements.txt` file.
- When adding a dependency, also update `requirements.txt`.
- Prefer lightweight, well-maintained Python packages that install cleanly on Raspberry Pi OS.
- Avoid introducing unnecessary build complexity, heavyweight frameworks, or dependencies that are difficult to compile on ARM unless clearly justified.

## Runtime Expectations

- The script should be runnable directly for local testing and debugging.
- The normal production deployment model is a `systemd` service.
- Run all configured cameras in one application process managed by one `systemd` unit.
- Prefer running the service by executing the venv Python interpreter directly from `ExecStart` rather than using a shell wrapper script.
- Use project-relative defaults for development (`./config.json`, `./state`). The production `systemd` service passes the config path explicitly via `--config /opt/prusaconnectcamera/config.json`. Logs go to `journald` via stdout/stderr in service mode.
- Favor predictable startup behavior, clear logging, and clean shutdown handling suitable for a long-running service.
- Do not assume an interactive shell, attached monitor, or manual user intervention during normal operation.

## Implementation Guidance

- Keep the code simple, readable, and suitable for unattended operation.
- Use a JSON configuration file as the only runtime configuration source; do not rely on environment variables or interactive prompts.
- Validate JSON configuration strictly at startup and fail fast with clear errors for missing required keys, unsupported values, invalid types, or too many cameras.
- Treat configuration parse/validation errors as non-recoverable startup failures.
- Follow the Prusa Connect camera registration and communication model described in the official docs instead of inventing a custom workflow.
- Assume cameras are pre-registered in Prusa Connect and tokens are supplied in the JSON config.
- Treat API-based camera registration (`POST /app/printers/{printer_uuid}/camera`) as optional tooling, not part of the normal runtime path.
- Treat camera registration, fingerprint handling, snapshot upload, and camera attribute updates as separate responsibilities in the code.
- Prefer a design where the transport layer mirrors the documented API endpoints closely, with higher-level logic for capture scheduling, retry policy, and local device management layered on top.
- Respect the documented API contract for `POST /app/printers/{printer_uuid}/camera`, `PUT /c/snapshot`, and `PUT /c/info`, including expected authentication, payload structure, and status handling.
- Generate each camera fingerprint once, store it as a field in the camera's JSON config entry, and write it back atomically at load time so camera identity remains stable across restarts and reboots. Deleting a fingerprint value and restarting is the supported rotation mechanism.
- `state_dir` in the config is available for other persistent runtime state. Fingerprints are stored directly in the config JSON and do not use `state_dir`.
- Enforce strict permissions for the JSON configuration file because it contains tokens; fail fast at startup if file permissions are broader than expected for secrets.
- Redact sensitive values in logs (at minimum token and full fingerprint) and never emit them in plaintext.
- Keep snapshot uploads efficient and bounded; the API documents a maximum snapshot size of 16 MB.
- Since the documented camera API is camera-to-Connect push (`PUT /c/snapshot`) and does not define a Connect-to-camera snapshot request endpoint, support `MANUAL` plus time-based triggers (`TEN_SEC`, `THIRTY_SEC`, `SIXTY_SEC`, `TEN_MIN` deprecated) and do not emulate layer or gcode-trigger behavior.
- Treat unsupported trigger schemes as configuration errors unless an explicit compatibility mode is implemented.
- For `MANUAL`, do not run a periodic scheduler and provide no trigger entrypoint; the camera registers with Connect as manual-trigger but takes no automatic or on-demand snapshots. This is intentionally a no-op for now.
- For time-based triggers, run a local scheduler per camera using the configured interval mapping.
- Surface configuration for printer UUID, token, camera identity, trigger scheme, device path, and resolution explicitly in JSON rather than hard-coding them.
- Design for up to 4 USB cameras plus 1 CSI camera (reflecting the 4 USB ports and single CSI connector on a Raspberry Pi), where each camera can target a different printer UUID/token pair.
- Prefer stable Linux device addressing (for example UDEV rules with persistent symlinks) so camera-to-printer mapping remains deterministic even with identical webcam models.
- Prefer Linux-native webcam access patterns suitable for Raspberry Pi OS, such as V4L2-compatible capture tools or libraries.
- Prefer a lightweight capture approach orchestrated by Python with Linux-native tools: use `ffmpeg` for USB/V4L2 capture and support Raspberry Pi CSI cameras via `libcamera-still` (or equivalent libcamera tooling).
- Make capture backend selection explicit per camera in JSON (for example `v4l2` or `csi`) and validate required binaries at startup.
- Treat missing backend executables as non-recoverable startup errors for affected cameras.
- Handle transient network and camera errors defensively with useful log messages and sensible retry behavior where appropriate.
- Distinguish recoverable failures, such as temporary network or camera read errors, from non-recoverable configuration or authentication failures.
- On `401`, `403`, or `404` responses, continue retrying with clear warning/error logs rather than crashing silently.
- Implement bounded exponential backoff with jitter for recoverable failures, with retry state tracked per camera. Default values: initial delay 2 s, maximum delay 60 s, jitter ±20%.
- Keep service behavior resilient for unattended operation: retries should continue indefinitely unless explicitly disabled in config.
- Update camera metadata with `PUT /c/info` at startup and when local camera configuration changes.
- Define config change detection with a deterministic method (for example full-file hash comparison) and only refresh affected camera metadata.
- Collect host network info (`network_info`) once at startup — not on a polling loop — and include it in `PUT /c/info` payloads. When the default route is wireless, include `wifi_mac`, `wifi_ipv4`, and `wifi_ssid` (SSID via `iwgetid -r`; omit gracefully if unavailable). When wired, include `lan_mac` and `lan_ipv4`. Include IPv6 only when IPv4 is unavailable.
- For V4L2 (USB) captures, skip the first ~20 frames via ffmpeg to avoid dark/black warm-up frames from freshly opened webcams.
- Avoid hard-coding paths or assumptions that only work on a development workstation.
- If service files, setup steps, or deployment documentation are added, keep them compatible with `systemd` on Raspberry Pi OS.

## JSON Configuration Contract

- Define and document a strict schema for the JSON config file.
- Require a top-level camera list with 1 to 4 USB (`V4L2`) cameras plus up to 1 CSI camera, reflecting the 4 USB ports and single CSI connector on a Raspberry Pi.
- Require per-camera fields at minimum: `name`, `printer_uuid`, `token`, `device_path`, `driver`, `trigger_scheme`, and `resolution`.
- Optional per-camera fields: `enabled` (bool, default `true`), `fingerprint` (UUID4 string, auto-generated and written back if absent), `firmware` (string, defaults to script version), `manufacturer` (string, defaults to `"fasteddy516"`), `model` (string, defaults to a generated label).
- Restrict `trigger_scheme` to `MANUAL`, `TEN_SEC`, `THIRTY_SEC`, `SIXTY_SEC`, `TEN_MIN` (deprecated compatibility).
- Require explicit backend/driver selection compatible with device type (`V4L2` for USB webcams, `CSI` for Raspberry Pi CSI camera input).
- Allow unknown keys in the config file but emit a warning for each unrecognised key at startup; do not fail.

## Security Requirements

- Assume the JSON config contains secrets and require restrictive file ownership and mode.
- Require config file mode to be `0600` (owner read/write) or `0400` (owner read-only); refuse any mode that grants group or world access.
- Refuse startup when config permissions are too broad.
- Ensure logs never include token values or other raw secret material.

## Retry and Error Handling

- Use per-camera retry loops so one failing camera does not block others.
- Classify failures clearly in logs: capture failure, upload failure, metadata update failure, config/state failure.
- Apply exponential backoff with jitter for recoverable errors.
- Keep retry behavior active for long-running service mode, including `401`, `403`, and `404`, with clear operator-facing warnings.

## Capture Backends

- Support USB webcams via V4L2 capture (using `ffmpeg`).
- Support Raspberry Pi CSI cameras via libcamera tooling (`libcamera-still` or equivalent).
- Validate backend command availability at startup and produce actionable errors.
- Keep backend invocation isolated behind a capture interface so future backend swaps do not affect API transport logic.

## Metadata Refresh Rules

- Send `PUT /c/info` for each configured camera at startup after configuration is validated.
- Re-send `PUT /c/info` only when camera-relevant config values change.
- Avoid periodic metadata refresh unless explicitly configured in the future.

## Testing and Validation

- Prefer tests and validation steps that can run on a headless Linux system.
- If hardware access is required, structure code so logic can still be tested without a physical camera where practical.
- When suggesting verification steps, include direct script execution for development and `systemd` service execution for normal deployment.
- Include tests for config schema validation and permission checks.
- Include tests for fingerprint persistence behavior across restarts.
- Include tests for retry/backoff behavior and error classification.
- Include API transport tests with mocked responses for `204`, `200`, `401`, `403`, `404`, and `503` paths.
- Include scheduler tests that verify `MANUAL` does not auto-schedule and time-based triggers do schedule correctly.

## Documentation Expectations

- Keep the README and setup instructions aligned with the actual runtime requirements.
- Document any required OS packages, Python dependencies, JSON configuration schema, and service setup steps.
- If adding new operational behavior, include concise instructions for headless Raspberry Pi deployment and troubleshooting.