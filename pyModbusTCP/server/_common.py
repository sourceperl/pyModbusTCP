""" pyModbusTCP server: shared internals (logger, type aliases, client host key, log throttle). """

from __future__ import annotations

import ipaddress
import logging
import time
from threading import Lock
from typing import Any, Union

# all the server modules log under the historical name "pyModbusTCP.server" (used by applications to tune it)
logger = logging.getLogger('pyModbusTCP.server')

# an IPv4 or IPv6 network (an address is a network with a full prefix)
_IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

# an IPv6 client is counted by /64 network: a host usually gets a whole /64, so one address per connection is easy
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


def host_key(client_address: Any) -> str:
    """Return the key used to count the connections (or requests) of a client host.

    An IPv4 client is counted by address, an IPv6 client by /64 network (see HOST_IPV6_PREFIX).
    """
    ip = client_ip(client_address)
    if ip is None:
        return str(client_address[0]) if isinstance(client_address, (tuple, list)) and client_address else '?'
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((ip, HOST_IPV6_PREFIX), strict=False))
    return str(ip)


class LogThrottle:
    """Log a repeated event at most once per interval, with the number of events since the last message.

    The first event is logged at once. The message format must start with a %d for the number of events, like
    'Connection refused: %d connection(s) refused (last from %r)'. Thread-safe.
    """

    def __init__(self, log: logging.Logger, level: int = logging.WARNING, interval: float = 10.0) -> None:
        self.log = log
        self.level = level
        self.interval = interval
        self._count = 0
        self._last_time: float | None = None
        self._lock = Lock()

    def event(self, msg: str, *args: Any) -> None:
        """Count an event, log msg (with the count of events, then args) if the last message is old enough."""
        with self._lock:
            self._count += 1
            now = time.monotonic()
            if self._last_time is not None and now - self._last_time < self.interval:
                return
            count = self._count
            self._count = 0
            self._last_time = now
        self.log.log(self.level, msg, count, *args)
