#!/usr/bin/env python3

"""
Nearby Wi-Fi access-point scanning via a system Wi-Fi daemon over D-Bus.

Optional feature: needs the ``scan`` extra (``uv add "isimud[scan]"``, which pulls
in ``jeepney``). It auto-detects and uses whichever Wi-Fi daemon is running: **iwd**
(``net.connman.iwd``) or **NetworkManager** (``org.freedesktop.NetworkManager``).

Unlike the rest of isimud it does not use pyroute2: a fresh scan is a privileged
operation, so it is delegated to the daemon, which scans and returns parsed results
without requiring root (the caller must be allowed on the daemon's D-Bus, e.g. be in
the ``wheel``/``network`` group). Results reflect the daemon's current view.
"""

from typing import List, NamedTuple, Optional

try:
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    _HAVE_JEEPNEY = True
except ImportError:  # pragma: no cover - only without the scan extra
    _HAVE_JEEPNEY = False


_IWD = "net.connman.iwd"
_NM = "org.freedesktop.NetworkManager"
_NM_PATH = "/org/freedesktop/NetworkManager"
_NM_DEVICE_TYPE_WIFI = 2
# NM80211ApSecurityFlags bits used to label a network's security.
_NM_SEC_PSK = 0x100      # NM_802_11_AP_SEC_KEY_MGMT_PSK
_NM_SEC_8021X = 0x200    # NM_802_11_AP_SEC_KEY_MGMT_802_1X

_WPAS = "fi.w1.wpa_supplicant1"
_WPAS_PATH = "/fi/w1/wpa_supplicant1"


class AccessPoint(NamedTuple):
    """A nearby Wi-Fi network.

    ``signal_dbm`` is None on backends that only report a percentage
    (NetworkManager); ``signal_percent`` (0-100) is always present. ``bssid`` and
    ``frequency`` (MHz) are None on iwd, whose scan is network-centric; use
    ``isimud.access_point_mac_address`` / ``access_point_frequency`` for the AP you
    are connected to.
    """
    ssid: str
    bssid: Optional[str]
    frequency: Optional[int]
    signal_dbm: Optional[int]
    signal_percent: int
    security: str
    connected: bool


def scan_access_points(interface: Optional[str] = None) -> List[AccessPoint]:
    """
    Return the nearby Wi-Fi access points, strongest signal first, from the running
    Wi-Fi daemon (iwd or NetworkManager, auto-detected).

    Reflects the daemon's current view; it does not trigger a fresh scan. Requires the
    ``scan`` extra and a running iwd or NetworkManager daemon.

    :param interface: limit to this wireless interface; None uses the first one.

    """
    if not _HAVE_JEEPNEY:  # pragma: no cover - only without the scan extra
        raise RuntimeError(
            "scan_access_points() needs the 'scan' extra: uv add 'isimud[scan]'"
        )

    conn = open_dbus_connection(bus="SYSTEM")
    try:
        if _name_has_owner(conn, _IWD):
            return _scan_iwd(conn, interface)
        if _name_has_owner(conn, _NM):
            return _scan_networkmanager(conn, interface)
        if _name_has_owner(conn, _WPAS):
            return _scan_wpa_supplicant(conn, interface)
        raise RuntimeError(
            "scan_access_points() needs a Wi-Fi daemon on D-Bus, but none of iwd "
            "(net.connman.iwd), NetworkManager (org.freedesktop.NetworkManager), or "
            "wpa_supplicant (fi.w1.wpa_supplicant1) is running."
        )
    finally:
        conn.close()


# --- D-Bus plumbing ---------------------------------------------------------

def _call(conn, msg):
    """ Send `msg` and return its reply body, raising on a D-Bus error reply
    (jeepney returns an error *message* rather than raising, so we check it). """
    reply = conn.send_and_get_reply(msg)
    if reply.header.message_type == MessageType.error:
        detail = reply.body[0] if reply.body else "unknown D-Bus error"
        raise RuntimeError(f"D-Bus call failed: {detail}")
    return reply.body


def _name_has_owner(conn, name):
    """ True if `name` currently owns a D-Bus name (i.e. the daemon is running). """
    dbus = DBusAddress(
        "/org/freedesktop/DBus",
        bus_name="org.freedesktop.DBus",
        interface="org.freedesktop.DBus",
    )
    return _call(conn, new_method_call(dbus, "NameHasOwner", "s", (name,)))[0]


def _get_all_props(conn, bus_name, path, interface):
    """ org.freedesktop.DBus.Properties.GetAll -> {name: (signature, value)}. """
    props = DBusAddress(
        path, bus_name=bus_name, interface="org.freedesktop.DBus.Properties"
    )
    return _call(conn, new_method_call(props, "GetAll", "s", (interface,)))[0]


def _prop(props, name, default=None):
    """ Read a value from a jeepney {name: (signature, value)} property dict. """
    value = props.get(name)
    return value[1] if value is not None else default


def _dbm_to_percent(dbm):
    """ Rough dBm -> 0-100 %: -100 dBm -> 0, -50 dBm (or stronger) -> 100. """
    return min(max(2 * (dbm + 100), 0), 100)


# --- iwd backend ------------------------------------------------------------

def _scan_iwd(conn, interface):
    objects = _get_managed_objects(conn)
    station_path = _select_iwd_station(objects, interface)
    if station_path is None:
        return []
    ordered = _get_ordered_networks(conn, station_path)
    return _iwd_access_points(ordered, objects)


def _get_managed_objects(conn):
    manager = DBusAddress(
        "/", bus_name=_IWD, interface="org.freedesktop.DBus.ObjectManager"
    )
    return _call(conn, new_method_call(manager, "GetManagedObjects"))[0]


def _get_ordered_networks(conn, station_path):
    station = DBusAddress(station_path, bus_name=_IWD, interface=f"{_IWD}.Station")
    return _call(conn, new_method_call(station, "GetOrderedNetworks"))[0]


def _select_iwd_station(objects, interface):
    for path, interfaces in objects.items():
        if f"{_IWD}.Station" not in interfaces:
            continue
        if interface is None:
            return path
        if _prop(interfaces.get(f"{_IWD}.Device", {}), "Name") == interface:
            return path
    return None


def _iwd_access_points(ordered, objects):
    access_points = []
    for net_path, signal in ordered:
        net = objects.get(net_path, {}).get(f"{_IWD}.Network", {})
        dbm = round(signal / 100)  # iwd reports 100 * dBm
        access_points.append(AccessPoint(
            ssid=_prop(net, "Name", ""),
            bssid=None,       # iwd's scan is network-centric (no single BSSID/freq)
            frequency=None,
            signal_dbm=dbm,
            signal_percent=_dbm_to_percent(dbm),
            security=_prop(net, "Type", ""),
            connected=_prop(net, "Connected", False),
        ))
    return access_points


# --- NetworkManager backend -------------------------------------------------

def _scan_networkmanager(conn, interface):
    access_points = []
    for dev_path in _nm_get_devices(conn):
        dev = _get_all_props(conn, _NM, dev_path, f"{_NM}.Device")
        if _prop(dev, "DeviceType") != _NM_DEVICE_TYPE_WIFI:
            continue
        if interface is not None and _prop(dev, "Interface") != interface:
            continue
        wireless = _get_all_props(conn, _NM, dev_path, f"{_NM}.Device.Wireless")
        active = _prop(wireless, "ActiveAccessPoint")
        for ap_path in _nm_get_access_points(conn, dev_path):
            props = _get_all_props(conn, _NM, ap_path, f"{_NM}.AccessPoint")
            access_points.append(_nm_access_point(props, ap_path == active))
    access_points.sort(key=lambda ap: ap.signal_percent, reverse=True)
    return access_points


def _nm_get_devices(conn):
    nm = DBusAddress(_NM_PATH, bus_name=_NM, interface=_NM)
    return _call(conn, new_method_call(nm, "GetDevices"))[0]


def _nm_get_access_points(conn, dev_path):
    wireless = DBusAddress(dev_path, bus_name=_NM, interface=f"{_NM}.Device.Wireless")
    return _call(conn, new_method_call(wireless, "GetAllAccessPoints"))[0]


def _nm_access_point(props, connected):
    """ Build an AccessPoint from NetworkManager AccessPoint properties. """
    return AccessPoint(
        ssid=_decode_ssid(_prop(props, "Ssid", b"")),
        bssid=_prop(props, "HwAddress"),
        frequency=_prop(props, "Frequency"),
        signal_dbm=None,  # NM only exposes a percentage
        signal_percent=int(_prop(props, "Strength", 0)),
        security=_nm_security(_prop(props, "WpaFlags", 0), _prop(props, "RsnFlags", 0)),
        connected=connected,
    )


def _decode_ssid(raw):
    """ NM's Ssid is a byte array (`ay`); decode to text. """
    return bytes(raw).decode("utf-8", "replace")


def _format_mac(raw):
    """ Format a 6-byte MAC (jeepney `ay`) as a lowercase colon-separated string. """
    return ":".join(f"{b:02x}" for b in bytes(raw))


def _nm_security(wpa_flags, rsn_flags):
    flags = wpa_flags | rsn_flags
    if flags == 0:
        return "open"
    if flags & _NM_SEC_8021X:
        return "8021x"
    if flags & _NM_SEC_PSK:
        return "psk"
    return "secured"


# --- wpa_supplicant backend -------------------------------------------------

def _scan_wpa_supplicant(conn, interface):
    root = _get_all_props(conn, _WPAS, _WPAS_PATH, _WPAS)
    access_points = []
    for iface_path in _prop(root, "Interfaces", []):
        iface = _get_all_props(conn, _WPAS, iface_path, f"{_WPAS}.Interface")
        if interface is not None and _prop(iface, "Ifname") != interface:
            continue
        current = _prop(iface, "CurrentBSS")
        for bss_path in _prop(iface, "BSSs", []):
            bss = _get_all_props(conn, _WPAS, bss_path, f"{_WPAS}.BSS")
            access_points.append(_wpas_access_point(bss, bss_path == current))
    access_points.sort(key=lambda ap: ap.signal_percent, reverse=True)
    return access_points


def _wpas_access_point(bss, connected):
    """ Build an AccessPoint from wpa_supplicant BSS properties (Signal is dBm). """
    dbm = _prop(bss, "Signal")
    dbm = int(dbm) if dbm is not None else None
    bssid = _prop(bss, "BSSID")
    return AccessPoint(
        ssid=_decode_ssid(_prop(bss, "SSID", b"")),
        bssid=_format_mac(bssid) if bssid is not None else None,
        frequency=_prop(bss, "Frequency"),
        signal_dbm=dbm,
        signal_percent=_dbm_to_percent(dbm) if dbm is not None else 0,
        security=_wpas_security(bss),
        connected=connected,
    )


def _wpas_security(bss):
    """ open / wep / psk / 8021x from the BSS's RSN/WPA KeyMgmt and Privacy. """
    key_mgmt = list(_prop(_prop(bss, "RSN", {}), "KeyMgmt", []))
    key_mgmt += list(_prop(_prop(bss, "WPA", {}), "KeyMgmt", []))
    joined = " ".join(key_mgmt).lower()
    if not key_mgmt:
        return "wep" if _prop(bss, "Privacy", False) else "open"
    if "eap" in joined:
        return "8021x"
    if "psk" in joined or "sae" in joined:
        return "psk"
    return "secured"
