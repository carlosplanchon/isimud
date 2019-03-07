# Isimud
*Package to get commonly used details of the network interface and access points you are using.*

## installation
### install with pip
```
pip3 install -U isimud
```

## features

- Get loopback, ethernet and wifi interfaces.
- Get operstate, mac_address, recv and sent bytes of an interface.
- Get ESSID, signal percent and MAC address of an interface.

## usage
```
In [3]: import isimud

In [4]: isimud.get_eth_interfaces()

Out[4]: ['enp9s0']
```
