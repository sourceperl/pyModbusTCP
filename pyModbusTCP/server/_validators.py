""" pyModbusTCP server: checks of the ModbusServer constructor arguments. """

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..utils import valid_host
from ._common import _IPNetwork
from .device_id import DeviceIdentification

if TYPE_CHECKING:
    from .store import DataStore


# the zone id of a link-local IPv6 address (like "eth0" in "fe80::1%eth0"): an interface name or index
_RE_ZONE_ID = re.compile('[a-z0-9_.-]{1,64}', re.IGNORECASE | re.ASCII)


def validate_host(host: str) -> str:
    """Check the address to listen on: an IPv4 address in its standard form, an IPv6 address (with a zone id for a
    link-local one, like "fe80::1%eth0") or a hostname (resolved by start()).

    A legacy IPv4 form is refused: bind() would read it as another address, "0", "00" or "0x0" as 0.0.0.0 (every
    interface), "192.168.010.1" as 192.168.8.1 (octal), "127.1" as 127.0.0.1 (see utils.valid_host()).
    """
    if not isinstance(host, str):
        raise TypeError('host must be a str')
    # an empty host is the wildcard address for bind(): every interface, never by mistake (like an empty setting)
    if not host.strip():
        raise ValueError('host must not be empty: use \'0.0.0.0\' (or \'::\' with ipv6=True) to listen on every '
                         'interface')
    address, sep, zone_id = host.partition('%')
    if sep:
        # a link-local IPv6 address with its interface: the address of an interface, bind() takes it
        # (ipaddress, strict on every OS, like utils.valid_host(); not socket.inet_pton())
        try:
            ipaddress.IPv6Address(address)
        except ValueError:
            valid = False
        else:
            valid = bool(_RE_ZONE_ID.fullmatch(zone_id))
    else:
        valid = valid_host(host)
    if not valid:
        raise ValueError(f'host {host!r} is not an IP address in its standard form or a hostname (a legacy IPv4 form '
                         'like \'127.1\', \'0\' or \'192.168.010.1\' is refused: write \'127.0.0.1\', \'0.0.0.0\' '
                         'or \'192.168.10.1\')')
    return host


def validate_port(port: int) -> int:
    # bool is an int subclass: port=True would silently be port 1
    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError('port must be an int')
    if not 0 <= port <= 65535:
        raise ValueError('port must be between 0 and 65535 (0: a free port chosen by the system)')
    return port


def validate_store(store: Any) -> dict[int | None, DataStore]:
    """Check the store argument: a DataStore, a mapping {unit id: DataStore} or None (a MemoryStore is created).

    :returns: unit id -> store, the key None for the store of every unit id
    """
    from .store import DataStore, MemoryStore
    if store is None:
        return {None: MemoryStore()}
    if isinstance(store, DataStore):
        return {None: store}
    if isinstance(store, Mapping):
        if not store:
            raise ValueError('store mapping must not be empty')
        stores: dict[int | None, DataStore] = {}
        for unit_id, unit_store in store.items():
            if isinstance(unit_id, bool) or not isinstance(unit_id, int) or not 0 <= unit_id <= 255:
                raise ValueError(f'unit id {unit_id!r} must be an int between 0 and 255')
            if not isinstance(unit_store, DataStore):
                raise TypeError(f'store of unit id {unit_id} is not a DataStore')
            stores[unit_id] = unit_store
        return stores
    raise TypeError('store must be a DataStore, a mapping {unit id: DataStore} or None')


def validate_function_code(code: Any) -> int:
    if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= 0x7F:
        raise ValueError(f'function code {code!r} must be an int between 1 and 127')
    return code


def validate_functions(functions: Mapping[int, Callable[..., bytes]] | None) -> dict[int, Callable[..., bytes]]:
    """Check the function handlers (None: the standard functions) and return them as a new dict."""
    from .functions import STANDARD_FUNCTIONS
    if functions is None:
        return dict(STANDARD_FUNCTIONS)
    if not isinstance(functions, Mapping):
        raise TypeError('functions must be a mapping {function code: handler} or None')
    checked: dict[int, Callable[..., bytes]] = {}
    for code, handler in functions.items():
        if not callable(handler):
            raise TypeError(f'handler of function {code!r} is not callable')
        checked[validate_function_code(code)] = handler
    return checked


def validate_fallback(fallback: Callable[..., bytes] | None) -> Callable[..., bytes] | None:
    if fallback is not None and not callable(fallback):
        raise TypeError('fallback must be callable or None')
    return fallback


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


def validate_ipv6_host_prefix(prefix: int) -> int:
    """Check the prefix length that groups the IPv6 clients as one host (1 to 128)."""
    if isinstance(prefix, bool) or not isinstance(prefix, int):
        raise TypeError('ipv6_host_prefix must be an int')
    if not 1 <= prefix <= 128:
        raise ValueError('ipv6_host_prefix must be between 1 and 128 (64: by /64 network, 128: by address)')
    return prefix


def validate_read_only(read_only: bool) -> bool:
    if not isinstance(read_only, bool):
        raise TypeError('read_only must be a bool')
    return read_only


def parse_network(text: str) -> _IPNetwork:
    """An IP address or a network in CIDR notation, like "192.168.1.10" or "192.168.1.0/24".

    A network with host bits set, like "192.168.1.5/24", is refused: it is often a typo for one address, and it
    would allow the whole network.

    :raises ValueError: with a message for the user
    """
    try:
        return ipaddress.ip_network(text.strip())
    except ValueError:
        pass
    try:
        network = ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        raise ValueError(f'{text!r} is not an IP address or a network (hostnames are not supported)') from None
    address = text.strip().split('/')[0]
    raise ValueError(f'{text!r} has host bits set: use {str(network)!r} for the network, or {address!r} for the '
                     'address only')


def validate_allowed_hosts(allowed_hosts: Iterable[str] | None,
                           name: str = 'allowed_hosts') -> tuple[_IPNetwork, ...] | None:
    """Check an allow list and return it as networks.

    The convention of every allow list: None for no restriction (every host), an empty list for no host at all,
    else IP addresses or networks.
    """
    if allowed_hosts is None:
        return None
    # a lone string would be read char by char
    if isinstance(allowed_hosts, (str, bytes)):
        raise TypeError(f'{name} must be an iterable of str (or None), not a single str')
    try:
        hosts = list(allowed_hosts)
    except TypeError:
        raise TypeError(f'{name} must be an iterable of str or None') from None
    networks: list[_IPNetwork] = []
    for host in hosts:
        if not isinstance(host, str):
            raise TypeError(f'{name} items must be str, got {type(host).__name__}')
        try:
            networks.append(parse_network(host))
        except ValueError as e:
            raise ValueError(f'{name} item {e}') from None
    return tuple(networks)
