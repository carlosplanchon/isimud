# Isimud
*Package to get commonly used details of the network interface and access points you are using.*

## Installation
### Install with pip
```
pip3 install -U isimud
```

## Features

- Get loopback, ethernet and wifi interfaces.
- Get operstate, mac_address, recv and sent bytes of an interface.
- Get ESSID, signal percent and MAC address of an interface.

## Usage
```
In [3]: import isimud

In [4]: isimud.get_eth_interfaces()

Out[4]: ['enp9s0']
```
