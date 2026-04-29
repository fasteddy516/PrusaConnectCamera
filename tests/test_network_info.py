"""Tests for network_info collection rules."""

from prusaconnectcamera import network_info


def test_wifi_keys_with_ipv4_and_ssid(monkeypatch):
    monkeypatch.setattr(network_info, "_get_default_interface", lambda: "wlan0")
    monkeypatch.setattr(network_info, "_is_wireless", lambda iface: True)
    monkeypatch.setattr(network_info, "_get_mac", lambda iface: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(network_info, "_get_ipv4", lambda iface: "192.168.10.5")
    monkeypatch.setattr(network_info, "_get_ipv6", lambda iface: "2001:db8::1")
    monkeypatch.setattr(network_info, "_get_ssid", lambda iface: "MyNetwork")

    info = network_info.collect_network_info()

    assert info == {
        "wifi_mac": "aa:bb:cc:dd:ee:ff",
        "wifi_ipv4": "192.168.10.5",
        "wifi_ssid": "MyNetwork",
    }


def test_wifi_keys_without_ssid_when_iwgetid_unavailable(monkeypatch):
    monkeypatch.setattr(network_info, "_get_default_interface", lambda: "wlan0")
    monkeypatch.setattr(network_info, "_is_wireless", lambda iface: True)
    monkeypatch.setattr(network_info, "_get_mac", lambda iface: "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(network_info, "_get_ipv4", lambda iface: "192.168.10.5")
    monkeypatch.setattr(network_info, "_get_ipv6", lambda iface: None)
    monkeypatch.setattr(network_info, "_get_ssid", lambda iface: None)

    info = network_info.collect_network_info()

    assert info == {
        "wifi_mac": "aa:bb:cc:dd:ee:ff",
        "wifi_ipv4": "192.168.10.5",
    }


def test_lan_keys_do_not_include_ssid(monkeypatch):
    monkeypatch.setattr(network_info, "_get_default_interface", lambda: "eth0")
    monkeypatch.setattr(network_info, "_is_wireless", lambda iface: False)
    monkeypatch.setattr(network_info, "_get_mac", lambda iface: "00:11:22:33:44:55")
    monkeypatch.setattr(network_info, "_get_ipv4", lambda iface: "10.0.0.1")
    monkeypatch.setattr(network_info, "_get_ipv6", lambda iface: None)

    info = network_info.collect_network_info()

    assert "wifi_ssid" not in info
    assert info == {"lan_mac": "00:11:22:33:44:55", "lan_ipv4": "10.0.0.1"}


def test_lan_keys_with_ipv6_only_when_no_ipv4(monkeypatch):
    monkeypatch.setattr(network_info, "_get_default_interface", lambda: "eth0")
    monkeypatch.setattr(network_info, "_is_wireless", lambda iface: False)
    monkeypatch.setattr(network_info, "_get_mac", lambda iface: "00:11:22:33:44:55")
    monkeypatch.setattr(network_info, "_get_ipv4", lambda iface: None)
    monkeypatch.setattr(network_info, "_get_ipv6", lambda iface: "2001:db8::1234")

    info = network_info.collect_network_info()

    assert info == {
        "lan_mac": "00:11:22:33:44:55",
        "lan_ipv6": "2001:db8::1234",
    }


def test_no_default_interface_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(network_info, "_get_default_interface", lambda: None)

    assert network_info.collect_network_info() == {}
