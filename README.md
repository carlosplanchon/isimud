# Isimud

[![CI](https://github.com/carlosplanchon/isimud/actions/workflows/ci.yml/badge.svg)](https://github.com/carlosplanchon/isimud/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/isimud.svg)](https://pypi.org/project/isimud/)
[![Python versions](https://img.shields.io/pypi/pyversions/isimud.svg)](https://pypi.org/project/isimud/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

*Inspect Linux network interfaces, Wi-Fi state, routes, DNS, and network events from Python.*

> **Linux only.** Isimud relies on `pyroute2` (netlink), its single runtime dependency.

## Installation

Requires **Python 3.10+** on **Linux**. Install with [uv](https://docs.astral.sh/uv/):

```bash
uv add isimud            # core (only dependency: pyroute2)
uv add "isimud[scan]"    # + nearby Wi-Fi scanning (iwd / NetworkManager / wpa_supplicant)
```

## Features

All facts come from the kernel via `pyroute2` (netlink / nl80211): no external tools, no root.

**Interfaces**
- List loopback / ethernet / wifi interfaces (classified by kernel type, not by name).
- Per interface: type, operational state, MAC, MTU, carrier, IPv4 / IPv6 addresses, traffic counters (bytes / packets / errors / drops), and throughput rate.

**Access point (Wi-Fi)**
- ESSID, BSSID, signal (dBm + %), plus the frequency and current bitrate of the associated AP.

**Host network**
- The default interface, default gateway (IPv4 / IPv6), and the configured DNS servers.

**Nearby Wi-Fi scan** *(optional, requires `isimud[scan]` + a running Wi-Fi daemon)*
- List nearby access points (SSID, signal, security) via **iwd, NetworkManager, or wpa_supplicant** over D-Bus, without root.

**Events**
- `watch()`: a live stream of link / address / route changes (netlink, no root), to react instead of poll.

## Usage

```python
import isimud

# --- interfaces ---
isimud.get_wifi_interfaces()               # ['wlan0']
isimud.interface_type("docker0")           # 'bridge'
isimud.interface_operstate("wlan0")        # 'UP'
isimud.interface_mac_address("wlan0")      # 'a1:b2:c3:d4:e5:f6'
isimud.interface_mtu("wlan0")              # 1500
isimud.interface_ipv4_addresses("wlan0")   # ['192.168.1.42']
isimud.interface_stats("wlan0").rx_packets # 51702404
isimud.interface_rate("wlan0")             # InterfaceRate(rx_bytes_per_sec=…, tx_bytes_per_sec=…)

# --- access point (Wi-Fi) ---
isimud.access_point_essid("wlan0")         # 'MyNetwork'
isimud.access_point_mac_address("wlan0")   # 'aa:bb:cc:dd:ee:ff'  (BSSID)
isimud.access_point_signal_dbm("wlan0")    # -47
isimud.access_point_frequency("wlan0")     # 5280   (MHz)
isimud.access_point_bitrate("wlan0")       # 866.7  (Mbps)

# --- host network ---
isimud.default_interface()                 # 'wlan0'
isimud.default_gateway_ipv4()              # '192.168.1.1'
isimud.dns_servers()                       # ['192.168.1.1']
```

### Nearby Wi-Fi scan (optional)

Requires the `scan` extra and a running Wi-Fi daemon: [iwd](https://iwd.wiki.kernel.org/),
[NetworkManager](https://networkmanager.dev/), or [wpa_supplicant](https://w1.fi/wpa_supplicant/),
auto-detected. The caller must be allowed on the daemon's D-Bus (e.g. be in the `wheel` / `network` group):

```bash
uv add "isimud[scan]"
```

```python
import isimud

for ap in isimud.scan_access_points():          # strongest signal first
    print(ap.ssid, ap.bssid, ap.signal_percent, ap.security)
# AccessPoint(ssid='MyNetwork', bssid='aa:bb:cc:dd:ee:ff', frequency=5180,
#             signal_dbm=None, signal_percent=100, security='psk', connected=True)
```

`signal_percent` (0-100) is always present. `signal_dbm` is usually filled by iwd / wpa_supplicant
and `None` on NetworkManager (percentage-only); `bssid` and `frequency` are usually filled by
NetworkManager / wpa_supplicant and `None` on iwd (its scan is network-centric).

> **Backend status:** the **iwd** backend is verified against a live daemon. The **NetworkManager** and
> **wpa_supplicant** backends are implemented against their documented D-Bus APIs and unit-tested, but not
> yet exercised against a live daemon. Bug reports welcome.

### Watch for changes (event stream)

```python
import isimud

for event in isimud.watch():          # blocks; yields as things change
    print(event.kind, event.action, event.interface, event.detail)
# NetworkEvent(kind='link', action='new', interface='wlan0', index=4, detail={'operstate': 'DOWN'})
```
