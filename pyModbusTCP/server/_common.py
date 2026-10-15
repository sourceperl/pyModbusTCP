""" pyModbusTCP server: shared internals (logger, type aliases). """

from __future__ import annotations

import ipaddress
import logging
from typing import Union

# all the server modules log under the historical name "pyModbusTCP.server" (used by applications to tune it)
logger = logging.getLogger('pyModbusTCP.server')

# an IPv4 or IPv6 network (an address is a network with a full prefix)
_IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
