"""Network info helpers for camera attribute updates."""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import fcntl


_SIOCGIFADDR = 0x8915


def _get_default_interface() -> str | None:
    """Return the interface name for the default route, if available."""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as f:
            next(f, None)
            for line in f:
                fields = line.strip().split()
                if len(fields) < 4:
                    continue
                iface, destination, _gateway, flags_hex = fields[0], fields[1], fields[2], fields[3]
                flags = int(flags_hex, 16)
                if destination == "00000000" and (flags & 0x2):
                    return iface
    except OSError:
        return None
    return None


def _is_wireless(iface: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{iface}/wireless")


def _get_ssid(iface: str) -> str | None:
    """Return the SSID of the wireless interface, or None if unavailable."""
    try:
        result = subprocess.run(
            ["iwgetid", "-r", iface],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            ssid = result.stdout.decode("utf-8", errors="replace").strip()
            return ssid if ssid else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _get_mac(iface: str) -> str | None:
    try:
        with open(f"/sys/class/net/{iface}/address", "r", encoding="utf-8") as f:
            value = f.read().strip().lower()
            return value if value else None
    except OSError:
        return None


def _get_ipv4(iface: str) -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ifreq = struct.pack("256s", iface.encode("utf-8")[:15])
        result = fcntl.ioctl(s.fileno(), _SIOCGIFADDR, ifreq)
        return socket.inet_ntoa(result[20:24])
    except OSError:
        return None
    finally:
        s.close()


def _get_ipv6(iface: str) -> str | None:
    """Return a global IPv6 address for *iface* if available."""
    try:
        with open("/proc/net/if_inet6", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 6:
                    continue
                raw_addr, _idx, _plen, scope, _flags, name = parts
                if name != iface:
                    continue
                if scope != "00":
                    continue
                hextets = [raw_addr[i:i + 4] for i in range(0, 32, 4)]
                addr = ":".join(hextets)
                return socket.inet_ntop(socket.AF_INET6, socket.inet_pton(socket.AF_INET6, addr))
    except OSError:
        return None
    return None


def collect_network_info() -> dict:
    """Build a network_info object for Prusa Connect camera attributes.

    Uses wifi_* keys when the default route is via a wireless interface,
    otherwise uses lan_* keys. Includes IPv6 only when IPv4 is unavailable.
    """
    iface = _get_default_interface()
    if not iface:
        return {}

    prefix = "wifi" if _is_wireless(iface) else "lan"
    info: dict[str, str] = {}

    mac = _get_mac(iface)
    if mac:
        info[f"{prefix}_mac"] = mac

    ipv4 = _get_ipv4(iface)
    if ipv4:
        info[f"{prefix}_ipv4"] = ipv4
    else:
        ipv6 = _get_ipv6(iface)
        if ipv6:
            info[f"{prefix}_ipv6"] = ipv6

    if prefix == "wifi":
        ssid = _get_ssid(iface)
        if ssid:
            info["wifi_ssid"] = ssid

    return info
