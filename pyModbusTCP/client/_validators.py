""" pyModbusTCP client: checks of the ModbusClient settings and of the request arguments. """

import math

from ..utils import link_local_without_zone, valid_host


def check_addr_nb(addr: int, nb: int, max_nb: int, addr_name: str, nb_name: str, action: str = 'read') -> None:
    """Check a modbus address and a number of items (both already converted to int).

    :raises ValueError: if address, number of items or the end of the area is out of range
    """
    if not 0 <= addr <= 0xffff:
        raise ValueError(f'{addr_name} out of range (valid from 0 to 65535)')
    if not 1 <= nb <= max_nb:
        raise ValueError(f'{nb_name} out of range (valid from 1 to {max_nb})')
    if addr + nb > 0x10000:
        raise ValueError(f'{action} after end of modbus address space')


def check_regs_values(values: list[int], name: str) -> None:
    """Check that every register value fit in 16 bits (values is a non-empty list of int).

    :raises ValueError: if a value is out of the 0 to 65535 range
    """
    if min(values) < 0 or max(values) > 0xffff:
        raise ValueError(f'{name} list contains out of range values')


def validate_host(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError('host must be a str')
    if not valid_host(value):
        if link_local_without_zone(value):
            raise ValueError(f'host {value!r} is a link-local IPv6 address: give its interface, like '
                             f'\'{value}%eth0\' (\'{value}%2\' with an interface index on Windows)')
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


# max interval between two broadcast requests (s)
MAX_BROADCAST_INTERVAL = 10.0


def validate_broadcast_interval(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError('broadcast_interval must be a float')
    if not (math.isfinite(value) and 0.0 <= value <= MAX_BROADCAST_INTERVAL):
        raise ValueError(f'broadcast_interval can\'t be set (valid from 0 to {MAX_BROADCAST_INTERVAL:g})')
    return float(value)


# max number of connect retries and max delay before a retry (s)
MAX_CONNECT_RETRIES = 1000
MAX_CONNECT_RETRY_DELAY = 60.0


def validate_connect_retries(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError('connect_retries must be an int')
    if not 0 <= value <= MAX_CONNECT_RETRIES:
        raise ValueError(f'connect_retries can\'t be set (valid from 0 to {MAX_CONNECT_RETRIES})')
    return value


def validate_connect_retry_delay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TypeError('connect_retry_delay must be a float')
    if not (math.isfinite(value) and 0.0 <= value <= MAX_CONNECT_RETRY_DELAY):
        raise ValueError(f'connect_retry_delay can\'t be set (valid from 0 to {MAX_CONNECT_RETRY_DELAY:g})')
    return float(value)
