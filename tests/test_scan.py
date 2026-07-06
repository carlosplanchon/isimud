"""Tests for Wi-Fi scanning (iwd and NetworkManager backends).

The D-Bus boundary is mocked at the module's seams, so these run anywhere jeepney is
installed. The NetworkManager backend is unit-tested against its documented D-Bus shapes
(no live NM here). A skip-guarded smoke test hits a live daemon if one is reachable.
"""

import pytest

pytest.importorskip("jeepney")  # the `scan` extra

import isimud
from isimud import scan

from jeepney import MessageType


class _Header:
    def __init__(self, message_type):
        self.message_type = message_type


class _Reply:
    def __init__(self, body, message_type=MessageType.method_return):
        self.body = body
        self.header = _Header(message_type)


class _Conn:
    def __init__(self, reply=None):
        self._reply = reply

    def send_and_get_reply(self, msg):
        return self._reply

    def close(self):
        pass


# --- backend dispatch / detection -------------------------------------------

class TestDispatch:
    def _detect(self, monkeypatch, iwd=False, nm=False, wpas=False):
        monkeypatch.setattr(scan, "open_dbus_connection", lambda bus=None: _Conn())
        owners = {scan._IWD: iwd, scan._NM: nm, scan._WPAS: wpas}
        monkeypatch.setattr(scan, "_name_has_owner", lambda conn, name: owners[name])

    def test_uses_iwd_when_present(self, monkeypatch):
        self._detect(monkeypatch, iwd=True)
        monkeypatch.setattr(scan, "_scan_iwd", lambda conn, iface: ["iwd-result"])
        assert isimud.scan_access_points() == ["iwd-result"]

    def test_uses_networkmanager_when_iwd_absent(self, monkeypatch):
        self._detect(monkeypatch, iwd=False, nm=True)
        monkeypatch.setattr(scan, "_scan_networkmanager", lambda conn, iface: ["nm-result"])
        assert isimud.scan_access_points() == ["nm-result"]

    def test_uses_wpa_supplicant_when_others_absent(self, monkeypatch):
        self._detect(monkeypatch, wpas=True)
        monkeypatch.setattr(scan, "_scan_wpa_supplicant", lambda conn, iface: ["wpas-result"])
        assert isimud.scan_access_points() == ["wpas-result"]

    def test_raises_when_no_daemon(self, monkeypatch):
        self._detect(monkeypatch, iwd=False, nm=False)
        with pytest.raises(RuntimeError, match="Wi-Fi daemon on D-Bus"):
            isimud.scan_access_points()


# --- iwd backend ------------------------------------------------------------

_IWD_OBJECTS = {
    "/iwd/0/4": {
        "net.connman.iwd.Station": {},
        "net.connman.iwd.Device": {"Name": ("s", "wlan0")},
    },
    "/iwd/0/4/rupia": {
        "net.connman.iwd.Network": {
            "Name": ("s", "Rupia-5GHz"), "Type": ("s", "psk"), "Connected": ("b", True),
        },
    },
    "/iwd/0/4/redmi": {
        "net.connman.iwd.Network": {
            "Name": ("s", "Redmi"), "Type": ("s", "psk"), "Connected": ("b", False),
        },
    },
}
_IWD_ORDERED = [("/iwd/0/4/rupia", -2300), ("/iwd/0/4/redmi", -6300)]


class TestIwdBackend:
    def _patch(self, monkeypatch, objects=_IWD_OBJECTS, ordered=_IWD_ORDERED):
        monkeypatch.setattr(scan, "_get_managed_objects", lambda conn: objects)
        monkeypatch.setattr(scan, "_get_ordered_networks", lambda conn, path: ordered)

    def test_parses_and_derives_percent(self, monkeypatch):
        self._patch(monkeypatch)
        assert scan._scan_iwd(None, None) == [
            scan.AccessPoint("Rupia-5GHz", None, None, -23, 100, "psk", True),
            scan.AccessPoint("Redmi", None, None, -63, 74, "psk", False),  # 2*(-63+100)
        ]

    def test_interface_filter(self, monkeypatch):
        self._patch(monkeypatch)
        assert len(scan._scan_iwd(None, "wlan0")) == 2
        assert scan._scan_iwd(None, "wlan9") == []

    def test_no_station(self, monkeypatch):
        self._patch(monkeypatch, objects={"/": {"org.freedesktop.DBus.ObjectManager": {}}})
        assert scan._scan_iwd(None, None) == []


# --- NetworkManager backend (documented D-Bus shapes; no live NM here) ------

class TestNetworkManagerBackend:
    def _patch(self, monkeypatch, devices, dev_props, wireless_props, ap_lists, ap_props):
        monkeypatch.setattr(scan, "_nm_get_devices", lambda conn: devices)
        monkeypatch.setattr(scan, "_nm_get_access_points", lambda conn, dev: ap_lists.get(dev, []))

        def fake_get_all(conn, bus, path, iface):
            if iface == f"{scan._NM}.Device":
                return dev_props.get(path, {})
            if iface == f"{scan._NM}.Device.Wireless":
                return wireless_props.get(path, {})
            if iface == f"{scan._NM}.AccessPoint":
                return ap_props.get(path, {})
            return {}

        monkeypatch.setattr(scan, "_get_all_props", fake_get_all)

    def test_lists_orders_and_labels(self, monkeypatch):
        self._patch(
            monkeypatch,
            devices=["/dev/wlan0", "/dev/eth0"],
            dev_props={
                "/dev/wlan0": {"DeviceType": ("u", 2), "Interface": ("s", "wlan0")},
                "/dev/eth0": {"DeviceType": ("u", 1), "Interface": ("s", "eth0")},
            },
            wireless_props={"/dev/wlan0": {"ActiveAccessPoint": ("o", "/ap/strong")}},
            ap_lists={"/dev/wlan0": ["/ap/weak", "/ap/strong"]},
            ap_props={
                "/ap/strong": {"Ssid": ("ay", b"MyNet"), "Strength": ("y", 80),
                               "HwAddress": ("s", "aa:bb:cc:dd:ee:ff"), "Frequency": ("u", 5200),
                               "WpaFlags": ("u", 0), "RsnFlags": ("u", 0x100)},
                "/ap/weak": {"Ssid": ("ay", b"Far"), "Strength": ("y", 20),
                             "HwAddress": ("s", "11:22:33:44:55:66"), "Frequency": ("u", 2412),
                             "WpaFlags": ("u", 0), "RsnFlags": ("u", 0)},
            },
        )
        # eth0 skipped (not wifi); ordered by percent desc; strong is the active AP.
        assert scan._scan_networkmanager(None, None) == [
            scan.AccessPoint("MyNet", "aa:bb:cc:dd:ee:ff", 5200, None, 80, "psk", True),
            scan.AccessPoint("Far", "11:22:33:44:55:66", 2412, None, 20, "open", False),
        ]

    def test_interface_filter_excludes_non_matching(self, monkeypatch):
        self._patch(
            monkeypatch,
            devices=["/dev/wlan0"],
            dev_props={"/dev/wlan0": {"DeviceType": ("u", 2), "Interface": ("s", "wlan0")}},
            wireless_props={"/dev/wlan0": {"ActiveAccessPoint": ("o", "/x")}},
            ap_lists={"/dev/wlan0": ["/ap/a"]},
            ap_props={"/ap/a": {"Ssid": ("ay", b"A"), "Strength": ("y", 50),
                                "WpaFlags": ("u", 0), "RsnFlags": ("u", 0)}},
        )
        assert scan._scan_networkmanager(None, "wlan9") == []


# --- wpa_supplicant backend (documented D-Bus shapes; no live wpa_supplicant here) --

class TestWpaSupplicantBackend:
    def _patch(self, monkeypatch, root, iface_props, bss_props):
        def fake_get_all(conn, bus, path, iface):
            if iface == scan._WPAS:
                return root
            if iface == f"{scan._WPAS}.Interface":
                return iface_props.get(path, {})
            if iface == f"{scan._WPAS}.BSS":
                return bss_props.get(path, {})
            return {}

        monkeypatch.setattr(scan, "_get_all_props", fake_get_all)

    def test_lists_orders_and_labels(self, monkeypatch):
        self._patch(
            monkeypatch,
            root={"Interfaces": ("ao", ["/iface/0"])},
            iface_props={"/iface/0": {
                "Ifname": ("s", "wlan0"),
                "CurrentBSS": ("o", "/bss/strong"),
                "BSSs": ("ao", ["/bss/weak", "/bss/strong"]),
            }},
            bss_props={
                "/bss/strong": {"SSID": ("ay", b"MyNet"), "Signal": ("n", -40),
                                "BSSID": ("ay", b"\xaa\xbb\xcc\xdd\xee\xff"), "Frequency": ("q", 5200),
                                "RSN": ("a{sv}", {"KeyMgmt": ("as", ["wpa-psk"])}),
                                "WPA": ("a{sv}", {}), "Privacy": ("b", True)},
                "/bss/weak": {"SSID": ("ay", b"Open"), "Signal": ("n", -80),
                              "BSSID": ("ay", b"\x11\x22\x33\x44\x55\x66"), "Frequency": ("q", 2412),
                              "RSN": ("a{sv}", {}), "WPA": ("a{sv}", {}), "Privacy": ("b", False)},
            },
        )
        # Signal is dBm (like iwd): -40 -> 100%, -80 -> 40%. Strong is CurrentBSS.
        assert scan._scan_wpa_supplicant(None, None) == [
            scan.AccessPoint("MyNet", "aa:bb:cc:dd:ee:ff", 5200, -40, 100, "psk", True),
            scan.AccessPoint("Open", "11:22:33:44:55:66", 2412, -80, 40, "open", False),
        ]

    def test_interface_filter_and_eap(self, monkeypatch):
        self._patch(
            monkeypatch,
            root={"Interfaces": ("ao", ["/iface/0"])},
            iface_props={"/iface/0": {
                "Ifname": ("s", "wlan0"),
                "CurrentBSS": ("o", "/"),  # disconnected
                "BSSs": ("ao", ["/bss/corp"]),
            }},
            bss_props={"/bss/corp": {"SSID": ("ay", b"Corp"), "Signal": ("n", -55),
                                     "RSN": ("a{sv}", {"KeyMgmt": ("as", ["wpa-eap"])}),
                                     "WPA": ("a{sv}", {}), "Privacy": ("b", True)}},
        )
        assert scan._scan_wpa_supplicant(None, "wlan9") == []  # no matching interface
        assert scan._scan_wpa_supplicant(None, "wlan0") == [
            scan.AccessPoint("Corp", None, None, -55, 90, "8021x", False),
        ]


# --- pure helpers & thin D-Bus adapters -------------------------------------

class TestHelpers:
    def test_nm_security(self):
        assert scan._nm_security(0, 0) == "open"
        assert scan._nm_security(0, 0x100) == "psk"
        assert scan._nm_security(0x200, 0) == "8021x"
        assert scan._nm_security(0x1, 0) == "secured"

    def test_wpas_security(self):
        def bss(rsn=None, wpa=None, privacy=False):
            d = {"Privacy": ("b", privacy)}
            if rsn is not None:
                d["RSN"] = ("a{sv}", {"KeyMgmt": ("as", rsn)})
            if wpa is not None:
                d["WPA"] = ("a{sv}", {"KeyMgmt": ("as", wpa)})
            return d

        assert scan._wpas_security(bss()) == "open"
        assert scan._wpas_security(bss(privacy=True)) == "wep"
        assert scan._wpas_security(bss(rsn=["wpa-psk"])) == "psk"
        assert scan._wpas_security(bss(rsn=["sae"])) == "psk"
        assert scan._wpas_security(bss(rsn=["wpa-eap"])) == "8021x"
        assert scan._wpas_security(bss(rsn=["unknown-x"])) == "secured"

    def test_wpas_access_point_without_signal(self):
        bss = {"SSID": ("ay", b"X"), "RSN": ("a{sv}", {}), "WPA": ("a{sv}", {}), "Privacy": ("b", False)}
        ap = scan._wpas_access_point(bss, False)
        assert ap.signal_dbm is None and ap.signal_percent == 0
        assert ap.bssid is None and ap.frequency is None

    def test_format_mac(self):
        assert scan._format_mac(b"\xaa\xbb\xcc\xdd\xee\xff") == "aa:bb:cc:dd:ee:ff"
        assert scan._format_mac([1, 2, 3, 4, 5, 6]) == "01:02:03:04:05:06"

    def test_decode_ssid(self):
        assert scan._decode_ssid(b"MyNet") == "MyNet"
        assert scan._decode_ssid([104, 105]) == "hi"

    def test_dbm_to_percent(self):
        assert scan._dbm_to_percent(-50) == 100
        assert scan._dbm_to_percent(-100) == 0
        assert scan._dbm_to_percent(-75) == 50

    def test_call_raises_on_dbus_error(self):
        conn = _Conn(_Reply(("boom",), MessageType.error))
        with pytest.raises(RuntimeError, match="D-Bus call failed"):
            scan._call(conn, None)

    def test_name_has_owner(self):
        assert scan._name_has_owner(_Conn(_Reply((True,))), scan._IWD) is True

    def test_get_all_props(self):
        conn = _Conn(_Reply(({"Strength": ("y", 80)},)))
        assert scan._get_all_props(conn, "org.x", "/p", "iface") == {"Strength": ("y", 80)}

    def test_iwd_and_nm_thin_getters(self):
        assert scan._get_managed_objects(_Conn(_Reply(({"/p": {}},)))) == {"/p": {}}
        assert scan._get_ordered_networks(_Conn(_Reply(([("/n", -2300)],))), "/s") == [("/n", -2300)]
        assert scan._nm_get_devices(_Conn(_Reply((["/d"],)))) == ["/d"]
        assert scan._nm_get_access_points(_Conn(_Reply((["/a"],))), "/d") == ["/a"]


# --- real, skip-guarded smoke -----------------------------------------------

class TestScanRealSmoke:
    def test_scan_against_live_daemon(self):
        try:
            aps = isimud.scan_access_points()
        except Exception:
            pytest.skip("no reachable Wi-Fi daemon")
        assert isinstance(aps, list)
        for ap in aps:
            assert isinstance(ap.ssid, str)
            assert isinstance(ap.signal_percent, int)
            assert ap.signal_dbm is None or isinstance(ap.signal_dbm, int)
