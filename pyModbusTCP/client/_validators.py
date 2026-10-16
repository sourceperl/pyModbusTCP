""" pyModbusTCP client: checks of the ModbusClient settings and of the request arguments. """

import math

from ..utils import valid_host


def check_addr_nb(addr: int, nb: int, max_nb: int, addr_name: str, nb_name: str, action: str = 'read') -> None:
    """Check a modbus address and a number of items (both already converted to int).

    :raises ValueError: if address, number of items or the end of the area is out of range
    """
    if not 0 <= addr <= 0xffff:
        raise ValueError('%s out of range (valid from 0 to 65535)' % addr_name)
    if not 1 <= nb <= max_nb:
        raise ValueError('%s out of range (valid from 1 to %d)' % (nb_name, max_nb))
    if addr + nb > 0x10000:
        raise ValueError('%s after end of modbus address space' % action)


def check_regs_values(values: list[int], name: str) -> None:
    """Check that every register value fit in 16 bits (values is a non-empty list of int).

    :raises ValueError: if a value is out of the 0 to 65535 range
    """
    if min(values) < 0 or max(values) > 0xffff:
        raise ValueError('%s list contains out of range values' % name)


def validate_host(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError('host must be a str')
    if not valid_host(value):
        raise ValueError('host can\'t be set (not a valid IP address or hostname)')
    return value


def validate_port(value: object) -> int:
    # bool is an int subclass: port=True would silently be port 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('port must be an int')
    if not 0 < value < 65536:
        raise ValueError('port can\'t be set (valid if 0 < port < 65536)')
    return value


def validate_unit_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('unit_id must be an int')
    if not 0 <= value <= 255:
        raise ValueError('unit_id can\'t be set (valid from 0 to 255)')
    return value


def validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError('timeout must be a float')
    if not (math.isfinite(value) and 0.0 < value < 3600.0):
        raise ValueError('timeout can\'t be set (valid between 0 and 3600)')
    return float(value)
