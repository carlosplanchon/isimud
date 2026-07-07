"""Tests for ifpeek.

The OS/kernel boundary (pyroute2 IPRoute/IW) is mocked so these run
anywhere. A separate class (TestLoopbackSmoke) exercises the always-present `lo`
interface for real, to anchor the mocks to actual kernel/pyroute2 behavior.
"""

import re
from itertools import islice

import ifpeek
from ifpeek import ifpeek as mod  # the implementation module, for monkeypatching


# --- fakes that mimic pyroute2 messages BY ATTRIBUTE NAME ---------------------

class FakeMsg:
    """A pyroute2-like netlink message. ``get_attr(name)`` resolves attributes by
    name; header fields (``index``, ``family``, ...) are read via subscription.
    Subscripting ``["attrs"]`` raises, so any regression to the old
    ``["attrs"][20][1]`` positional-attr access fails loudly."""

    def __init__(self, fields=None, **attrs):
        self._fields = fields or {}
        self._attrs = attrs

    def get_attr(self, name):
        return self._attrs.get(name)

    def get(self, key, default=None):
        return self._fields.get(key, default)

    def __getitem__(self, key):
        if key == "attrs":  # pragma: no cover - only hit on a regression
            raise AssertionError(
                "positional ['attrs'][..] access used instead of get_attr()"
            )
        return self._fields[key]


class FakeIPRoute:
    def __init__(self, link=None, links=None, ifindex=None,
                 addrs=None, routes=None, up=(), down=(), unknown=()):
        self._link = link
        self._links = links
        self._ifindex = ifindex
        self._addrs = addrs or []
        self._routes = routes or []
        self._up, self._down, self._unknown = up, down, unknown

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_links(self, ifname=None):
        if self._links is not None:
            return self._links
        return [self._link]

    def link_lookup(self, ifname=None, operstate=None):
        if operstate == "UP":
            return list(self._up)
        if operstate == "DOWN":
            return list(self._down)
        if operstate == "UNKNOWN":
            return list(self._unknown)
        return [self._ifindex] if self._ifindex is not None else []

    def get_addr(self):
        return self._addrs

    def get_default_routes(self):
        return self._routes


class FakeIW:
    def __init__(self, *devices, stations=()):
        self._devices = devices
        self._stations = stations

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_dev(self):
        return iter(self._devices)

    def get_stations(self, ifindex):
        return iter(self._stations)


class FakeMonitor:
    """ A netlink monitor: bind() is a no-op; get() returns the canned batch. """
    def __init__(self, batch):
        self._batch = batch

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def bind(self, groups=0):
        self.groups = groups

    def get(self):
        return self._batch


class FakeIWError:
    """An IW whose station dump raises (e.g. a non-wireless interface)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_stations(self, ifindex):
        raise OSError("not a wireless interface")


# --- interface discovery -----------------------------------------------------

class TestDiscovery:
    def _patch(self, monkeypatch, types):
        links = [FakeMsg(IFLA_IFNAME=n) for n in types]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=links))
        monkeypatch.setattr(mod, "interface_type", lambda n: types[n])

    def test_loopback(self, monkeypatch):
        self._patch(monkeypatch, {"lo": "loopback", "enp0s31f6": "ethernet", "wlan0": "wifi"})
        assert ifpeek.get_loopback_interface() == ["lo"]

    def test_ethernet_excludes_virtual(self, monkeypatch):
        # docker0 (bridge) must not count as ethernet: the point of type-based discovery.
        self._patch(monkeypatch, {"lo": "loopback", "enp0s31f6": "ethernet",
                                  "wlan0": "wifi", "docker0": "bridge"})
        assert ifpeek.get_eth_interfaces() == ["enp0s31f6"]

    def test_wifi(self, monkeypatch):
        self._patch(monkeypatch, {"wlan0": "wifi", "wlan1": "wifi", "eth0": "ethernet"})
        assert ifpeek.get_wifi_interfaces() == ["wlan0", "wlan1"]

    def test_absent_class_is_empty_list(self, monkeypatch):
        self._patch(monkeypatch, {"lo": "loopback"})
        assert ifpeek.get_eth_interfaces() == []
        assert ifpeek.get_wifi_interfaces() == []


# --- attributes resolved BY NAME (regression for the positional-index bug) ---

class TestInterfaceAttributes:
    def test_mac_address_via_get_attr(self, monkeypatch):
        link = FakeMsg(IFLA_ADDRESS="de:ad:be:ef:00:01")
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=link))
        assert ifpeek.interface_mac_address("eth0") == "de:ad:be:ef:00:01"

    def test_recv_and_sent_bytes_from_stats64(self, monkeypatch):
        link = FakeMsg(IFLA_STATS64={"rx_bytes": 123, "tx_bytes": 456})
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=link))
        assert ifpeek.interface_recv_bytes("eth0") == 123
        assert ifpeek.interface_sent_bytes("eth0") == 456

    def test_stats_fall_back_to_ifla_stats(self, monkeypatch):
        # Older kernels expose IFLA_STATS (32-bit) but not IFLA_STATS64.
        link = FakeMsg(IFLA_STATS={"rx_bytes": 7, "tx_bytes": 8})
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=link))
        assert ifpeek.interface_recv_bytes("eth0") == 7
        assert ifpeek.interface_sent_bytes("eth0") == 8

    def test_attribute_order_is_irrelevant(self, monkeypatch):
        # FakeMsg raises on positional access, so this passing proves the code
        # never indexes attrs by position (the original bug).
        link = FakeMsg(
            IFLA_ADDRESS="00:11:22:33:44:55",
            IFLA_STATS64={"rx_bytes": 1, "tx_bytes": 2},
        )
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=link))
        assert ifpeek.interface_mac_address("eth0") == "00:11:22:33:44:55"
        assert ifpeek.interface_sent_bytes("eth0") == 2

    def test_unknown_interface_returns_none(self, monkeypatch):
        # Consistent with the rest of the library: unknown interface -> None (not IndexError).
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[]))
        assert ifpeek.interface_mac_address("nope0") is None
        assert ifpeek.interface_recv_bytes("nope0") is None
        assert ifpeek.interface_sent_bytes("nope0") is None


class TestOperstate:
    def test_up(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=5, up=(5,)))
        assert ifpeek.interface_operstate("eth0") == "UP"

    def test_down(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=5, down=(5,)))
        assert ifpeek.interface_operstate("eth0") == "DOWN"

    def test_unknown_interface_returns_none(self, monkeypatch):
        # link_lookup(ifname=...) -> [] means the interface does not exist.
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=None))
        assert ifpeek.interface_operstate("nope0") is None


# --- interface typing --------------------------------------------------------

class TestInterfaceType:
    def _link(self, ifi_type, kind=None):
        attrs = {}
        if kind is not None:
            attrs["IFLA_LINKINFO"] = FakeMsg(IFLA_INFO_KIND=kind)
        return FakeMsg(fields={"ifi_type": ifi_type}, **attrs)

    def _patch(self, monkeypatch, link, wireless=False):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[link]))
        monkeypatch.setattr(mod, "__is_wireless", lambda i: wireless)

    def test_loopback(self, monkeypatch):
        self._patch(monkeypatch, self._link(772))
        assert ifpeek.interface_type("lo") == "loopback"

    def test_ethernet(self, monkeypatch):
        self._patch(monkeypatch, self._link(1), wireless=False)
        assert ifpeek.interface_type("eth0") == "ethernet"

    def test_wifi(self, monkeypatch):
        self._patch(monkeypatch, self._link(1), wireless=True)
        assert ifpeek.interface_type("wlan0") == "wifi"

    def test_virtual_kind_wins(self, monkeypatch):
        self._patch(monkeypatch, self._link(1, kind="bridge"))
        assert ifpeek.interface_type("docker0") == "bridge"

    def test_unknown_hardware_type(self, monkeypatch):
        self._patch(monkeypatch, self._link(99))
        assert ifpeek.interface_type("weird0") == "unknown"

    def test_unknown_interface(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[]))
        assert ifpeek.interface_type("nope0") == "unknown"

    def test_is_wireless_helper(self):
        # getattr avoids name-mangling of `mod.__is_wireless` inside this class.
        is_wireless = getattr(mod, "__is_wireless")
        assert is_wireless("lo") is False  # lo is never wireless (real sysfs)


class TestInterfaceExtras:
    def test_mtu(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=FakeMsg(IFLA_MTU=1500)))
        assert ifpeek.interface_mtu("eth0") == 1500

    def test_mtu_unknown_interface(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[]))
        assert ifpeek.interface_mtu("nope0") is None

    def test_has_carrier(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=FakeMsg(IFLA_CARRIER=1)))
        assert ifpeek.interface_has_carrier("eth0") is True
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=FakeMsg(IFLA_CARRIER=0)))
        assert ifpeek.interface_has_carrier("eth0") is False

    def test_has_carrier_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=FakeMsg()))
        assert ifpeek.interface_has_carrier("eth0") is None
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[]))
        assert ifpeek.interface_has_carrier("nope0") is None

    def test_stats(self, monkeypatch):
        link = FakeMsg(IFLA_STATS64={
            "rx_bytes": 1, "tx_bytes": 2, "rx_packets": 3, "tx_packets": 4,
            "rx_errors": 5, "tx_errors": 6, "rx_dropped": 7, "tx_dropped": 8})
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=link))
        assert ifpeek.interface_stats("eth0") == ifpeek.InterfaceStats(1, 2, 3, 4, 5, 6, 7, 8)

    def test_stats_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(link=FakeMsg()))
        assert ifpeek.interface_stats("eth0") is None

    def test_stats_unknown_interface(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(links=[]))
        assert ifpeek.interface_stats("nope0") is None


class TestInterfaceRate:
    def _stats(self, rx, tx):
        return ifpeek.InterfaceStats(rx, tx, 0, 0, 0, 0, 0, 0)

    def test_rate(self, monkeypatch):
        samples = iter([self._stats(1000, 2000), self._stats(3000, 2500)])
        monkeypatch.setattr(mod, "interface_stats", lambda i: next(samples))
        monkeypatch.setattr(mod, "monotonic", iter([10.0, 12.0]).__next__)  # elapsed 2s
        monkeypatch.setattr(mod, "sleep", lambda s: None)
        assert ifpeek.interface_rate("eth0", 2.0) == ifpeek.InterfaceRate(1000.0, 250.0)

    def test_none_when_first_sample_fails(self, monkeypatch):
        monkeypatch.setattr(mod, "interface_stats", lambda i: None)
        monkeypatch.setattr(mod, "monotonic", lambda: 0.0)
        monkeypatch.setattr(mod, "sleep", lambda s: None)
        assert ifpeek.interface_rate("eth0") is None

    def test_none_when_second_sample_fails(self, monkeypatch):
        samples = iter([self._stats(1, 1), None])
        monkeypatch.setattr(mod, "interface_stats", lambda i: next(samples))
        monkeypatch.setattr(mod, "monotonic", iter([0.0, 1.0]).__next__)
        monkeypatch.setattr(mod, "sleep", lambda s: None)
        assert ifpeek.interface_rate("eth0") is None

    def test_none_when_no_elapsed(self, monkeypatch):
        samples = iter([self._stats(1, 1), self._stats(2, 2)])
        monkeypatch.setattr(mod, "interface_stats", lambda i: next(samples))
        monkeypatch.setattr(mod, "monotonic", iter([5.0, 5.0]).__next__)  # elapsed 0
        monkeypatch.setattr(mod, "sleep", lambda s: None)
        assert ifpeek.interface_rate("eth0") is None


# --- access point: ESSID (nl80211 interface info) ---------------------------

class TestEssid:
    def test_essid(self, monkeypatch):
        dev = FakeMsg(NL80211_ATTR_IFNAME="wlan0", NL80211_ATTR_SSID="MyNet")
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(dev))
        assert ifpeek.access_point_essid("wlan0") == "MyNet"

    def test_essid_not_associated(self, monkeypatch):
        dev = FakeMsg(NL80211_ATTR_IFNAME="wlan0")  # no SSID attr
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(dev))
        assert ifpeek.access_point_essid("wlan0") is None

    def test_essid_interface_absent(self, monkeypatch):
        monkeypatch.setattr(mod, "IW", lambda: FakeIW())
        assert ifpeek.access_point_essid("wlan0") is None


# --- access point: BSSID and signal from the associated nl80211 station ------

class TestAccessPointStation:
    """In managed mode the single station is the AP: its MAC is the BSSID and
    its signal is the AP signal. No external tools, no root."""

    def _patch(self, monkeypatch, station=None, raises=False):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4))
        if raises:
            monkeypatch.setattr(mod, "IW", lambda: FakeIWError())
        else:
            stations = [station] if station is not None else []
            monkeypatch.setattr(mod, "IW", lambda: FakeIW(stations=stations))

    def test_bssid(self, monkeypatch):
        self._patch(monkeypatch, FakeMsg(NL80211_ATTR_MAC="FC:40:09:C3:4D:01"))
        assert ifpeek.access_point_mac_address("wlan0") == "fc:40:09:c3:4d:01"
        # station has no STA_INFO -> signal is None (covers that branch)
        assert ifpeek.access_point_signal_dbm("wlan0") is None

    def test_signal_dbm(self, monkeypatch):
        station = FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(NL80211_STA_INFO_SIGNAL=-31))
        self._patch(monkeypatch, station)
        assert ifpeek.access_point_signal_dbm("wlan0") == -31
        # station has no MAC -> bssid is None (covers that branch)
        assert ifpeek.access_point_mac_address("wlan0") is None

    def test_signal_percent_derived_from_dbm(self, monkeypatch):
        station = FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(NL80211_STA_INFO_SIGNAL=-60))
        self._patch(monkeypatch, station)
        assert ifpeek.access_point_signal_percent("wlan0") == 80  # 2 * (-60 + 100)

    def test_signal_percent_is_clamped(self, monkeypatch):
        station = FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(NL80211_STA_INFO_SIGNAL=-20))
        self._patch(monkeypatch, station)
        assert ifpeek.access_point_signal_percent("wlan0") == 100  # clamped at 100

    def test_not_associated_returns_none(self, monkeypatch):
        self._patch(monkeypatch, station=None)  # no stations
        assert ifpeek.access_point_mac_address("wlan0") is None
        assert ifpeek.access_point_signal_dbm("wlan0") is None
        assert ifpeek.access_point_signal_percent("wlan0") is None

    def test_non_wireless_interface_returns_none(self, monkeypatch):
        self._patch(monkeypatch, raises=True)  # get_stations raises
        assert ifpeek.access_point_mac_address("eth0") is None
        assert ifpeek.access_point_signal_dbm("eth0") is None

    def test_unknown_interface_returns_none(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=None))
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(stations=[]))
        assert ifpeek.access_point_mac_address("nope0") is None


class TestAccessPointRadio:
    def test_frequency(self, monkeypatch):
        dev = FakeMsg(NL80211_ATTR_IFNAME="wlan0", NL80211_ATTR_WIPHY_FREQ=5280)
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(dev))
        assert ifpeek.access_point_frequency("wlan0") == 5280

    def test_frequency_none_when_not_associated(self, monkeypatch):
        monkeypatch.setattr(mod, "IW", lambda: FakeIW())  # no device
        assert ifpeek.access_point_frequency("wlan0") is None

    def test_bitrate_prefers_32bit(self, monkeypatch):
        station = FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(
            NL80211_STA_INFO_TX_BITRATE=FakeMsg(NL80211_RATE_INFO_BITRATE32=8667)))
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4))
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(stations=[station]))
        assert ifpeek.access_point_bitrate("wlan0") == 866.7  # 8667 * 100kbps -> Mbps

    def test_bitrate_falls_back_to_16bit(self, monkeypatch):
        station = FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(
            NL80211_STA_INFO_TX_BITRATE=FakeMsg(NL80211_RATE_INFO_BITRATE=1300)))
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4))
        monkeypatch.setattr(mod, "IW", lambda: FakeIW(stations=[station]))
        assert ifpeek.access_point_bitrate("wlan0") == 130.0

    def test_bitrate_none_cases(self, monkeypatch):
        def patch(station):
            monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4))
            monkeypatch.setattr(mod, "IW", lambda: FakeIW(stations=[station] if station else []))

        patch(None)                                                  # no station
        assert ifpeek.access_point_bitrate("wlan0") is None
        patch(FakeMsg())                                             # station, no STA_INFO
        assert ifpeek.access_point_bitrate("wlan0") is None
        patch(FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg()))              # STA_INFO, no TX_BITRATE
        assert ifpeek.access_point_bitrate("wlan0") is None
        patch(FakeMsg(NL80211_ATTR_STA_INFO=FakeMsg(NL80211_STA_INFO_TX_BITRATE=FakeMsg())))  # no rate
        assert ifpeek.access_point_bitrate("wlan0") is None


# --- IP addresses, default gateway and DNS ----------------------------------

class TestInterfaceIpAddresses:
    def test_ipv4(self, monkeypatch):
        addrs = [
            FakeMsg(fields={"index": 4, "family": 2}, IFA_ADDRESS="192.168.1.16"),
            FakeMsg(fields={"index": 1, "family": 2}, IFA_ADDRESS="127.0.0.1"),
            FakeMsg(fields={"index": 4, "family": 10}, IFA_ADDRESS="fe80::1"),
        ]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4, addrs=addrs))
        assert ifpeek.interface_ipv4_addresses("wlan0") == ["192.168.1.16"]

    def test_ipv6(self, monkeypatch):
        addrs = [
            FakeMsg(fields={"index": 4, "family": 10}, IFA_ADDRESS="2800::1"),
            FakeMsg(fields={"index": 4, "family": 10}, IFA_ADDRESS="fe80::1"),
            FakeMsg(fields={"index": 4, "family": 2}, IFA_ADDRESS="192.168.1.16"),
        ]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=4, addrs=addrs))
        assert ifpeek.interface_ipv6_addresses("wlan0") == ["2800::1", "fe80::1"]

    def test_unknown_interface_is_empty(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(ifindex=None))
        assert ifpeek.interface_ipv4_addresses("nope0") == []


class TestDefaultGateway:
    def test_ipv4(self, monkeypatch):
        routes = [
            FakeMsg(fields={"family": 10}, RTA_GATEWAY="fe80::1"),
            FakeMsg(fields={"family": 2}, RTA_GATEWAY="192.168.1.1"),
        ]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(routes=routes))
        assert ifpeek.default_gateway_ipv4() == "192.168.1.1"

    def test_ipv6(self, monkeypatch):
        routes = [FakeMsg(fields={"family": 10}, RTA_GATEWAY="fe80::1")]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(routes=routes))
        assert ifpeek.default_gateway_ipv6() == "fe80::1"

    def test_none_when_no_default_route(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(routes=[]))
        assert ifpeek.default_gateway_ipv4() is None


class TestDnsServers:
    def test_prefers_systemd_resolved(self, monkeypatch, tmp_path):
        run = tmp_path / "run-resolv.conf"
        run.write_text("# comment\nnameserver 192.168.1.1\nnameserver fe80::1%4\nsearch .\n")
        etc = tmp_path / "etc-resolv.conf"
        etc.write_text("nameserver 127.0.0.53\n")
        monkeypatch.setattr(mod, "_RESOLV_CONF_PATHS", (str(run), str(etc)))
        assert ifpeek.dns_servers() == ["192.168.1.1", "fe80::1%4"]

    def test_falls_back_to_etc(self, monkeypatch, tmp_path):
        missing = tmp_path / "absent.conf"
        etc = tmp_path / "etc-resolv.conf"
        etc.write_text("nameserver 8.8.8.8\n")
        monkeypatch.setattr(mod, "_RESOLV_CONF_PATHS", (str(missing), str(etc)))
        assert ifpeek.dns_servers() == ["8.8.8.8"]

    def test_empty_when_no_nameservers(self, monkeypatch, tmp_path):
        empty = tmp_path / "empty.conf"
        empty.write_text("# nothing here\nsearch example.com\n")
        monkeypatch.setattr(mod, "_RESOLV_CONF_PATHS", (str(empty),))
        assert ifpeek.dns_servers() == []


class TestDefaultInterface:
    def test_returns_default_route_interface(self, monkeypatch):
        routes = [FakeMsg(fields={"family": 2}, RTA_OIF=4)]
        monkeypatch.setattr(
            mod, "IPRoute",
            lambda: FakeIPRoute(routes=routes, link=FakeMsg(IFLA_IFNAME="wlan0")),
        )
        assert ifpeek.default_interface() == "wlan0"

    def test_prefers_ipv4_over_ipv6(self, monkeypatch):
        routes = [FakeMsg(fields={"family": 10}, RTA_OIF=9),
                  FakeMsg(fields={"family": 2}, RTA_OIF=4)]
        monkeypatch.setattr(
            mod, "IPRoute",
            lambda: FakeIPRoute(routes=routes, link=FakeMsg(IFLA_IFNAME="wlan0")),
        )
        assert ifpeek.default_interface() == "wlan0"

    def test_none_when_no_default_route(self, monkeypatch):
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(routes=[]))
        assert ifpeek.default_interface() is None

    def test_none_when_default_route_has_no_oif(self, monkeypatch):
        routes = [FakeMsg(fields={"family": 2})]  # family matches but no RTA_OIF
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeIPRoute(routes=routes))
        assert ifpeek.default_interface() is None


# --- network event stream (watch) -------------------------------------------

class TestNetworkEventTranslation:
    def _translate(self, msg):
        return getattr(mod, "__to_network_event")(msg)  # getattr avoids name mangling

    def test_link_new(self):
        msg = FakeMsg(fields={"event": "RTM_NEWLINK", "index": 4},
                      IFLA_IFNAME="wlan0", IFLA_OPERSTATE="UP")
        assert self._translate(msg) == ifpeek.NetworkEvent(
            "link", "new", "wlan0", 4, {"operstate": "UP"})

    def test_link_del(self):
        msg = FakeMsg(fields={"event": "RTM_DELLINK", "index": 5}, IFLA_IFNAME="eth0")
        e = self._translate(msg)
        assert (e.kind, e.action, e.interface) == ("link", "del", "eth0")

    def test_address_new_v4(self):
        msg = FakeMsg(fields={"event": "RTM_NEWADDR", "index": 4, "family": 2},
                      IFA_LABEL="wlan0", IFA_ADDRESS="192.168.1.16")
        assert self._translate(msg) == ifpeek.NetworkEvent(
            "address", "new", "wlan0", 4, {"address": "192.168.1.16", "family": "inet"})

    def test_address_del_v6_without_label(self):
        msg = FakeMsg(fields={"event": "RTM_DELADDR", "index": 4, "family": 10},
                      IFA_ADDRESS="fe80::1")
        e = self._translate(msg)
        assert (e.kind, e.action, e.interface) == ("address", "del", None)
        assert e.detail == {"address": "fe80::1", "family": "inet6"}

    def test_route_new(self):
        msg = FakeMsg(fields={"event": "RTM_NEWROUTE", "family": 2, "dst_len": 0},
                      RTA_OIF=4, RTA_GATEWAY="192.168.1.1")
        assert self._translate(msg) == ifpeek.NetworkEvent(
            "route", "new", None, 4,
            {"gateway": "192.168.1.1", "dst_len": 0, "family": "inet"})

    def test_route_del_without_oif(self):
        e = self._translate(FakeMsg(fields={"event": "RTM_DELROUTE", "family": 10}))
        assert (e.kind, e.action, e.index) == ("route", "del", 0)

    def test_unknown_event_is_skipped(self):
        assert self._translate(FakeMsg(fields={"event": "RTM_NEWNEIGH"})) is None


class TestWatch:
    def test_yields_events_skipping_noise(self, monkeypatch):
        batch = [
            FakeMsg(fields={"event": "RTM_NEWLINK", "index": 4},
                    IFLA_IFNAME="wlan0", IFLA_OPERSTATE="UP"),
            FakeMsg(fields={"event": "RTM_NEWNEIGH"}),  # noise -> skipped
            FakeMsg(fields={"event": "RTM_NEWADDR", "index": 4, "family": 2},
                    IFA_LABEL="wlan0", IFA_ADDRESS="192.168.1.16"),
        ]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeMonitor(batch))
        assert [e.kind for e in islice(ifpeek.watch(), 2)] == ["link", "address"]

    def test_interface_filter(self, monkeypatch):
        batch = [
            FakeMsg(fields={"event": "RTM_NEWLINK", "index": 4},
                    IFLA_IFNAME="wlan0", IFLA_OPERSTATE="UP"),
            FakeMsg(fields={"event": "RTM_NEWLINK", "index": 5},
                    IFLA_IFNAME="eth0", IFLA_OPERSTATE="DOWN"),  # filtered out
            FakeMsg(fields={"event": "RTM_NEWLINK", "index": 4},
                    IFLA_IFNAME="wlan0", IFLA_OPERSTATE="DOWN"),
        ]
        monkeypatch.setattr(mod, "IPRoute", lambda: FakeMonitor(batch))
        events = list(islice(ifpeek.watch("wlan0"), 2))
        assert len(events) == 2 and all(e.interface == "wlan0" for e in events)


# --- real, unmocked calls against the loopback interface --------------------

class TestLoopbackSmoke:
    """Anchors the mocked tests to real kernel/pyroute2 behavior. `lo` always
    exists on Linux (including CI runners)."""

    def test_lo_is_discovered(self):
        assert "lo" in ifpeek.get_loopback_interface()

    def test_lo_operstate(self):
        assert ifpeek.interface_operstate("lo") in {"UP", "DOWN", "UNKNOWN"}

    def test_lo_mac_is_a_mac_string(self):
        mac = ifpeek.interface_mac_address("lo")
        assert isinstance(mac, str)
        assert re.fullmatch(r"[0-9a-fA-F:]{17}", mac)

    def test_lo_counters_are_non_negative_ints(self):
        assert isinstance(ifpeek.interface_recv_bytes("lo"), int)
        assert isinstance(ifpeek.interface_sent_bytes("lo"), int)
        assert ifpeek.interface_recv_bytes("lo") >= 0

    def test_lo_has_loopback_ipv4(self):
        assert "127.0.0.1" in ifpeek.interface_ipv4_addresses("lo")

    def test_dns_servers_is_a_list(self):
        assert isinstance(ifpeek.dns_servers(), list)

    def test_default_gateway_ipv4_is_str_or_none(self):
        gw = ifpeek.default_gateway_ipv4()
        assert gw is None or isinstance(gw, str)

    def test_lo_type_is_loopback(self):
        assert ifpeek.interface_type("lo") == "loopback"

    def test_lo_mtu_is_int(self):
        assert isinstance(ifpeek.interface_mtu("lo"), int)

    def test_lo_stats(self):
        st = ifpeek.interface_stats("lo")
        assert st is None or isinstance(st.rx_packets, int)

    def test_default_interface_is_str_or_none(self):
        di = ifpeek.default_interface()
        assert di is None or isinstance(di, str)

    def test_lo_rate(self):
        r = ifpeek.interface_rate("lo", 0.01)
        assert r is None or isinstance(r.rx_bytes_per_sec, float)
