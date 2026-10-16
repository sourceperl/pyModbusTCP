""" pyModbusTCP server: checks of the ModbusServer constructor arguments. """

from __future__ import annotations

import ipaddress
import math
from collections.abc import Callable, Iterable

from ._common import _IPNetwork
from .data_bank import DataBank
from .data_handler import DataHandler
from .device_id import DeviceIdentification
from .session import SessionData


def validate_data_bank_and_handler(data_bank: DataBank | None, data_hdl: DataHandler | None) -> None:
    if data_bank and not isinstance(data_bank, DataBank):
        raise TypeError('data_bank is not a DataBank instance')
    if data_hdl and not isinstance(data_hdl, DataHandler):
        raise TypeError('data_hdl is not a DataHandler instance')
    if data_hdl and data_bank:
        raise ValueError('when data_hdl is set, you must define data_bank in it')


def validate_ext_engine(ext_engine: Callable[[SessionData], None] | None) -> None:
    if ext_engine and not callable(ext_engine):
        raise TypeError('ext_engine must be callable')


def validate_device_id(device_id: DeviceIdentification | None) -> DeviceIdentification | None:
    if device_id is None:
        return None
    if not isinstance(device_id, DeviceIdentification):
        raise TypeError(f'device_id must be a DeviceIdentification instance or None, '
                        f'got {type(device_id).__name__}')
    return device_id


def validate_timeout(value: float | None, name: str) -> float | None:
    """Check an optional timeout (None or a number of seconds > 0) and return it as a float (or None)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f'{name} must be a number or None')
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite number greater than 0 (or None to disable it)')
    return float(value)


def validate_max_connections(max_connections: int | None, name: str = 'max_connections') -> int | None:
    if max_connections is None:
        return None
    if isinstance(max_connections, bool) or not isinstance(max_connections, int):
        raise TypeError(f'{name} must be an int or None')
    if max_connections < 1:
        raise ValueError(f'{name} must be at least 1 (or None for no limit)')
    return max_connections


def validate_read_only(read_only: bool) -> bool:
    if not isinstance(read_only, bool):
        raise TypeError('read_only must be a bool')
    return read_only


def validate_allowed_hosts(allowed_hosts: Iterable[str] | None) -> tuple[_IPNetwork, ...] | None:
    """Check the whitelist (None or an iterable of IP addresses/networks) and return it as networks (or None)."""
    if allowed_hosts is None:
        return None
    # a lone string would be read char by char
    if isinstance(allowed_hosts, (str, bytes)):
        raise TypeError('allowed_hosts must be an iterable of str (or None), not a single str')
    try:
        hosts = list(allowed_hosts)
    except TypeError:
        raise TypeError('allowed_hosts must be an iterable of str or None') from None
    if not hosts:
        raise ValueError('allowed_hosts must not be empty (use None to allow every host)')
    networks: list[_IPNetwork] = []
    for host in hosts:
        if not isinstance(host, str):
            raise TypeError(f'allowed_hosts items must be str, got {type(host).__name__}')
        try:
            # strict=False: "192.168.1.5/24" is accepted as the network 192.168.1.0/24
            networks.append(ipaddress.ip_network(host.strip(), strict=False))
        except ValueError:
            raise ValueError(f'allowed_hosts item {host!r} is not an IP address or a network (hostnames are not '
                             'supported)') from None
    return tuple(networks)
