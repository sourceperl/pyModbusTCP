""" pyModbusTCP server: shared internals (logger, type aliases, client host key, log throttle). """

from __future__ import annotations

import ipaddress
import logging
import time
from threading import Lock
from typing import Any

# all the server modules log under the historical name "pyModbusTCP.server" (used by applications to tune it)
logger = logging.getLogger('pyModbusTCP.server')

# an IPv4 or IPv6 network (an address is a network with a full prefix)
_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# an IPv6 client is counted by /64 network by default: a host usually gets a whole /64 (SLAAC, temporary addresses),
# so one address per connection is easy; 128 (by address) only fits a network where a host can't choose its addresses
HOST_IPV6_PREFIX = 64


def client_ip(client_address: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the IP address of a client (from a socket address tuple), None if it can't be decoded.

    The zone id of a link-local IPv6 address (like "fe80::1%eth0") is dropped, and an IPv4 client on a dual-stack
    IPv6 socket (IPv4-mapped address ::ffff:a.b.c.d) is returned as an IPv4 address.
    """
    try:
        ip = ipaddress.ip_address(str(client_address[0]).split('%')[0])
    except (ValueError, IndexError, TypeError):
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_wildcard_address(address: str) -> bool:
    """Return True for the wildcard address of bind() (every interface): 0.0.0.0, ::, and ::ffff:0.0.0.0, the IPv4
    wildcard on a dual-stack IPv6 socket (every IPv4 interface on Linux).

    The IPv4-mapped form is tested as IPv4 here (see client_ip()): IPv6Address.is_unspecified is False for it in some
    Python releases (3.12.0 to 3.12.3 for example), True in the others.
    """
    ip = client_ip((address,))
    return ip is not None and ip.is_unspecified


def host_key(client_address: Any, ipv6_prefix: int = HOST_IPV6_PREFIX) -> str:
    """Return the key used to count the connections (or requests) of a client host.

    An IPv4 client is counted by address, an IPv6 client by network of ipv6_prefix bits (/64 by default, see
    HOST_IPV6_PREFIX; 128: by address).
    """
    ip = client_ip(client_address)
    if ip is None:
        return str(client_address[0]) if isinstance(client_address, (tuple, list)) and client_address else '?'
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((ip, ipv6_prefix), strict=False))
    return str(ip)


class LogThrottle:
    """Log a repeated event at most once per interval, with the number of events since the last message.

    The first event is logged at once. The message is a %-style logging message with named fields only:
    %(count)d for the number of events, at any position, and a keyword argument of event() for each other field,
    like event('%(count)d connection(s) rejected (limit %(limit)d, last from %(host)s)', limit=16, host=host).
    It is formatted lazily, by logging. Thread-safe.

    With exc_info=True (in an except block), the message has the traceback of the current exception: the first
    one, then the last one of each interval. A client that triggers the same error in a loop can't fill the disk.
    """

    def __init__(self, log: logging.Logger, level: int = logging.WARNING, interval: float = 10.0) -> None:
        self.log = log
        self.level = level
        self.interval = interval
        self._count = 0
        self._last_time: float | None = None
        self._lock = Lock()

    def event(self, msg: str, *, exc_info: bool = False, **fields: Any) -> None:
        """Count an event, log msg if the last message is old enough.

        :param msg: %-style logging message with named fields: %(count)d for the number of events since the last
            message, the other ones from fields
        :param exc_info: add the traceback of the current exception
        :param fields: the values of the other fields of msg
        :raises TypeError: if a field is named count (the number of events)
        """
        if 'count' in fields:
            raise TypeError("'count' is the number of events of a LogThrottle, not a field of event()")
        with self._lock:
            self._count += 1
            now = time.monotonic()
            if self._last_time is not None and now - self._last_time < self.interval:
                return
            count = self._count
            self._count = 0
            self._last_time = now
        # a mapping as the only argument: logging formats msg % mapping (named fields), lazily
        self.log.log(self.level, msg, {'count': count, **fields}, exc_info=exc_info)
