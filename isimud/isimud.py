#!/usr/bin/env python3

"""
Package to get commonly used details of the network interface
and access point you are using.
"""

from typing import Any, List, Optional

from netifaces import interfaces

from pyroute2 import IPRoute
from pyroute2.iwutil import IW

from subprocess import DEVNULL, PIPE, run

from re import findall, search, I


def get_loopback_interface() -> List[str]:
    """ Return a list of loopback interfaces (ideally one). """
    return [
        interface for interface in interfaces(
            ) if interface.startswith("lo")
        ]


def get_eth_interfaces() -> List[str]:
    """ Return a list of ethernet interfaces (ideally one). """
    return [
        interface for interface in interfaces(
            ) if interface.startswith("en")
        ]


def get_wifi_interfaces() -> List[str]:
    """ Return list of wifi interfaces (ideally one). """
    return [
        interface for interface in interfaces(
            ) if interface.startswith("wl")
        ]


def __get_iwconfig_output(interface: str) -> str:
    """ Return iwconfig {interface} output. """
    return run(
            f"iwconfig {interface}",
            shell=True,
            stdout=PIPE,
            stderr=DEVNULL
        ).stdout.decode()


def __winterface_name_to_device_dict(interface: str) -> Any:
    """ Return a dict containing device details (from pyroute2.IW). """
    with IW() as iw:
        list_dev_dict = iw.list_dev()
        for device_dict in list_dev_dict:
            if device_dict["attrs"][1][1] == interface:
                return device_dict
    return None


def __get_interface_number(interface: str) -> Optional[int]:
    """ Return the interface number (from pyroute2.IPRoute). """
    with IPRoute() as ipr:
        interface_number_list = ipr.link_lookup(ifname=interface)
        if len(interface_number_list) > 0:
            return interface_number_list[0]
    return None


def __get_interface_stats(interface: str) -> Any:
    """ Return the interface stats dict (from pyroute2.IPRoute). """
    with IPRoute() as ipr:
        return ipr.get_links(ifname=interface)[0]["attrs"][20][1]
    return None


def interface_operstate(interface: str) -> Optional[str]:
    """

    :param interface: str: Network interface.

    """
    interface_number = __get_interface_number(interface)
    if interface_number:
        with IPRoute() as ipr:
            if interface_number in ipr.link_lookup(operstate="UP"):
                return "UP"
            elif interface_number in ipr.link_lookup(operstate="DOWN"):
                return "DOWN"
            elif interface_number in ipr.link_lookup(operstate="UNKNOWN"):
                return "UNKNOWN"
    return None


def interface_mac_address(interface: str) -> str:
    """

    :param interface: str: Network interface.

    """
    with IPRoute() as ipr:
        return ipr.get_links(ifname=interface)[0]["attrs"][18][1]


def interface_recv_bytes(interface: str) -> int:
    """

    :param interface: str: Network interface.

    """
    return __get_interface_stats(interface)["rx_bytes"]


def interface_sent_bytes(interface: str) -> int:
    """

    :param interface: str: Network interface.

    """
    return __get_interface_stats(interface)["tx_bytes"]


def access_point_essid(interface: str) -> Optional[str]:
    """

    :param interface: str: Network interface.

    """
    device_dict = __winterface_name_to_device_dict(interface)
    if device_dict:
        if len(device_dict["attrs"]) >= 13:
            if device_dict["attrs"][12][0] == "NL80211_ATTR_SSID":
                return device_dict["attrs"][12][1]
    return None


def access_point_signal_percent(interface: str) -> Optional[int]:
    """

    :param interface: str: Network interface.

    """
    iwconfig_output = __get_iwconfig_output(interface)
    res = findall(r"Link Quality=(\d*/\d*)", iwconfig_output)
    if res != []:
        signal = res[0].split("/")
        return round(100 / int(signal[1]) * int(signal[0]))
    return None


def access_point_mac_address(interface: str) -> Optional[str]:
    """

    :param interface: str: Network interface.

    """
    iwconfig_output = __get_iwconfig_output(interface)
    res = search(r'([0-9A-F]{2}[:-]){5}([0-9A-F]{2})', iwconfig_output, I)
    if res:
        return res.group().lower()
    return None
