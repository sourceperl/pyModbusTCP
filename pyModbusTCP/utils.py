""" pyModbusTCP utils functions """

import ipaddress
import operator
import re
import struct
from collections.abc import Iterable, Sequence
from typing import Any


###############
# bits function
###############
def get_bits_from_int(val_int: int, val_size: int = 16) -> list[bool]:
    """Get the list of bits of val_int integer (default size is 16 bits).

    Return bits list, the least significant bit first. Use list.reverse() for msb first.

    :param val_int: integer value
    :type val_int: int
    :param val_size: bit length of integer (word = 16, long = 32) (optional)
    :type val_size: int
    :returns: list of boolean "bits" (the least significant first)
    :rtype: list
    """
    bits = []
    # populate bits list with bool items of val_int
    for i in range(val_size):
        bits.append(bool((val_int >> i) & 0x01))
    # return bits list
    return bits


# short alias
int2bits = get_bits_from_int


def byte_length(bit_length: int) -> int:
    """Return the number of bytes needs to contain a bit_length structure.

    :param bit_length: the number of bits
    :type bit_length: int
    :returns: the number of bytes
    :rtype: int
    """
    return (bit_length + 7) // 8


def test_bit(value: int, offset: int) -> bool:
    """Test a bit at offset position.

    :param value: value of integer to test
    :type value: int
    :param offset: bit offset (0 is lsb)
    :type offset: int
    :returns: value of bit at offset position
    :rtype: bool
    """
    mask = 1 << offset
    return bool(value & mask)


def set_bit(value: int, offset: int) -> int:
    """Set a bit at offset position.

    :param value: value of integer where set the bit
    :type value: int
    :param offset: bit offset (0 is lsb)
    :type offset: int
    :returns: value of integer with bit set
    :rtype: int
    """
    mask = 1 << offset
    return int(value | mask)


def reset_bit(value: int, offset: int) -> int:
    """Reset a bit at offset position.

    :param value: value of integer where reset the bit
    :type value: int
    :param offset: bit offset (0 is lsb)
    :type offset: int
    :returns: value of integer with bit reset
    :rtype: int
    """
    mask = ~(1 << offset)
    return int(value & mask)


def toggle_bit(value: int, offset: int) -> int:
    """Return an integer with the bit at offset position inverted.

    :param value: value of integer where invert the bit
    :type value: int
    :param offset: bit offset (0 is lsb)
    :type offset: int
    :returns: value of integer with bit inverted
    :rtype: int
    """
    mask = 1 << offset
    return int(value ^ mask)


########################
# Word convert functions
########################
def word_list_to_long(val_list: list[int], big_endian: bool = True, long_long: bool = False) -> list[int]:
    """Word list (16 bits) to long (32 bits) or long long (64 bits) list.

    By default, word_list_to_long() use big endian order. For use little endian, set
    big_endian param to False. Output format could be long long with long_long.
    option set to True.

    :param val_list: list of 16 bits int value
    :type val_list: list
    :param big_endian: True for big endian/False for little (optional)
    :type big_endian: bool
    :param long_long: True for long long 64 bits, default is long 32 bits (optional)
    :type long_long: bool
    :returns: list of 32 bits int value
    :rtype: list
    """
    long_list = []
    block_size = 4 if long_long else 2
    # populate long_list (len is half or quarter of 16 bits val_list) with 32 or 64 bits value
    for index in range(int(len(val_list) / block_size)):
        start = block_size * index
        long = 0
        if big_endian:
            if long_long:
                long += (val_list[start] << 48) + (val_list[start + 1] << 32)
                long += (val_list[start + 2] << 16) + (val_list[start + 3])
            else:
                long += (val_list[start] << 16) + val_list[start + 1]
        else:
            if long_long:
                long += (val_list[start + 3] << 48) + (val_list[start + 2] << 32)
            long += (val_list[start + 1] << 16) + val_list[start]
        long_list.append(long)
    # return long list
    return long_list


# short alias
words2longs = word_list_to_long


def long_list_to_word(val_list: list[int], big_endian: bool = True, long_long: bool = False) -> list[int]:
    """Long (32 bits) or long long (64 bits) list to word (16 bits) list.

    By default long_list_to_word() use big endian order. For use little endian, set
    big_endian param to False. Input format could be long long with long_long
    param to True.

    :param val_list: list of 32 bits int value
    :type val_list: list
    :param big_endian: True for big endian/False for little (optional)
    :type big_endian: bool
    :param long_long: True for long long 64 bits, default is long 32 bits (optional)
    :type long_long: bool
    :returns: list of 16 bits int value
    :rtype: list
    """
    word_list = []
    # populate 16 bits word_list with 32 or 64 bits value of val_list
    for val in val_list:
        block_l = [val & 0xffff, (val >> 16) & 0xffff]
        if long_long:
            block_l.append((val >> 32) & 0xffff)
            block_l.append((val >> 48) & 0xffff)
        if big_endian:
            block_l.reverse()
        word_list.extend(block_l)
    # return long list
    return word_list


# short alias
longs2words = long_list_to_word


##########################
# 2's complement functions
##########################
def get_2comp(val_int: int, val_size: int = 16) -> int:
    """Get the 2's complement of Python int val_int.

    :param val_int: int value to apply 2's complement
    :type val_int: int
    :param val_size: bit size of int value (word = 16, long = 32) (optional)
    :type val_size: int
    :returns: 2's complement result
    :rtype: int
    :raises ValueError: if mismatch between val_int and val_size
    """
    # avoid overflow
    if not (-1 << val_size - 1) <= val_int < (1 << val_size):
        err_msg = 'could not compute two\'s complement for %i on %i bits'
        err_msg %= (val_int, val_size)
        raise ValueError(err_msg)
    # test negative int
    if val_int < 0:
        val_int += 1 << val_size
    # test MSB (do two's comp if set)
    elif val_int & (1 << (val_size - 1)):
        val_int -= 1 << val_size
    return val_int


# short alias
twos_c = get_2comp


def get_list_2comp(val_list: list[int], val_size: int = 16) -> list[int]:
    """Get the 2's complement of Python list val_list.

    :param val_list: list of int value to apply 2's complement
    :type val_list: list
    :param val_size: bit size of int value (word = 16, long = 32) (optional)
    :type val_size: int
    :returns: 2's complement result
    :rtype: list
    """
    return [get_2comp(val, val_size) for val in val_list]


# short alias
twos_c_l = get_list_2comp


###############################
# IEEE floating-point functions
###############################
def decode_ieee(val_int: int, double: bool = False) -> float:
    """Decode Python int (32 bits integer) as an IEEE single or double precision format.

    Support NaN.

    :param val_int: a 32 or 64 bits integer as an int Python value
    :type val_int: int
    :param double: set to decode as a 64 bits double precision,
                   default is 32 bits single (optional)
    :type double: bool
    :returns: float result
    :rtype: float
    """
    if double:
        return float(struct.unpack("d", struct.pack("Q", val_int))[0])
    else:
        return float(struct.unpack("f", struct.pack("I", val_int))[0])


def encode_ieee(val_float: float, double: bool = False) -> int:
    """Encode Python float to int (32 bits integer) as an IEEE single or double precision format.

    Support NaN.

    :param val_float: float value to convert
    :type val_float: float
    :param double: set to encode as a 64 bits double precision,
                   default is 32 bits single (optional)
    :type double: bool
    :returns: IEEE 32 bits (single precision) as Python int
    :rtype: int
    """
    if double:
        return int(struct.unpack("Q", struct.pack("d", val_float))[0])
    else:
        return int(struct.unpack("I", struct.pack("f", val_float))[0])


################
# misc functions
################
def crc16(frame: bytes) -> int:
    """Compute CRC16.

    :param frame: frame
    :type frame: bytes
    :returns: CRC16
    :rtype: int
    """
    crc = 0xFFFF
    for item in frame:
        next_byte = item
        crc ^= next_byte
        for _ in range(8):
            lsb = crc & 1
            crc >>= 1
            if lsb:
                crc ^= 0xA001
    return crc


# one label of a hostname (1 to 63 chars: letters, digits, "-" or "_", no leading/trailing "-")
# re.ASCII: with re.IGNORECASE alone, [a-z] also matches some non-ASCII chars (like "K", the Kelvin sign, or
# "İ"), that the IDNA codec of the resolver may expand beyond 63 chars (UnicodeError at connect time)
_RE_HOST_PART_OK = re.compile('(?!-)[a-z0-9_-]{1,63}(?<!-)', re.IGNORECASE | re.ASCII)
# the last label of a hostname that is a number (decimal, octal or hexadecimal): the name is not a name, the resolver
# reads it as a legacy IPv4 address (inet_aton(): "192.168.1" is 192.168.0.1, "192.168.010.1" is 192.168.8.1,
# "2130706433" or "0x7f.1" is 127.0.0.1); a real top-level domain is never numeric (RFC 3696, section 2)
_RE_NUMERIC_LABEL = re.compile('[0-9]+|0x[0-9a-f]*', re.IGNORECASE | re.ASCII)
# max length of a hostname, without its final dot (RFC 1035: 255 bytes on the wire, 253 chars as text)
_HOSTNAME_MAX_LEN = 253


def valid_host(host_str: str) -> bool:
    """Validate a host string.

    Can be an IPv4/6 address or a valid hostname. An IPv4 address is in its standard form only ("192.168.1.10"):
    a legacy form that the resolver would read as another address is refused, like a missing byte ("192.168.1" is
    192.168.0.1), a leading zero (octal: "192.168.010.1" is 192.168.8.1), a number or a hexadecimal value
    ("2130706433" and "0x7f.1" are 127.0.0.1). So is any name whose last label is a number. A hostname is at most
    253 chars (a final dot excluded) and ASCII only: an internationalized domain name
    (IDN) is valid in its ASCII form (punycode, like ``'xn--caf-dma.fr'``), not in its Unicode form (``'café.fr'``).
    The conversion is left to the application: the codec of the standard library (``str.encode('idna')``) follows
    IDNA 2003, which differs from the current IDNA 2008 for some names (``'faß.de'`` would become ``'fass.de'``, another
    domain), the idna package of PyPI follows IDNA 2008.

    :param host_str: the host string to test
    :type host_str: str
    :returns: True if host_str is valid
    :rtype: bool
    """
    # an empty host or a NUL char (rejected by the system resolver functions) can't be valid
    if not host_str or '\x00' in host_str:
        return False
    # IPv4 or IPv6 valid address ?
    # the ipaddress module, not socket.inet_pton(): its result depends on the OS (the inet_pton() of macOS takes
    # "192.168.010.1", with a leading zero, that the resolver may read in octal), ipaddress is strict everywhere
    # (no leading zero since Python 3.9.5); a zone id ("fe80::1%eth0", accepted by IPv6Address) is not a host here
    if '%' not in host_str:
        for address_class in (ipaddress.IPv4Address, ipaddress.IPv6Address):
            try:
                address_class(host_str)
                return True
            except ValueError:
                pass
    # valid hostname ?
    # strip final dot, if present
    if host_str[-1] == '.':
        host_str = host_str[:-1]
    if not 0 < len(host_str) <= _HOSTNAME_MAX_LEN:
        return False
    # validate each part of the hostname (part_1.part_2.part_3)
    # fullmatch: a "$" anchor would accept a trailing newline (like "example.com\n")
    parts = host_str.split('.')
    if not all(_RE_HOST_PART_OK.fullmatch(part) for part in parts):
        return False
    # a numeric last label: a legacy form of IPv4 address for the resolver (another address than it looks like)
    return not _RE_NUMERIC_LABEL.fullmatch(parts[-1])


####################
# internal functions
####################

# a byte -> its 8 bits as bool (LSB first)
_BYTE_TO_BITS = tuple(tuple(bool(byte >> bit & 1) for bit in range(8)) for byte in range(256))
# translation table for _pack_bits: '\x00'/'\x01' bytes -> '0'/'1' ascii digits
_BITS_TO_DIGITS = bytes.maketrans(b'\x00\x01', b'01')


def _pack_bits(bits: Sequence[Any]) -> bytes:
    """Pack a sequence of bits in bytes, with the modbus order (LSB first in each byte).

    Each item is evaluated as a bool (truthiness). Same result as a loop with set_bit(), but about 5x faster.

    :param bits: bits to pack
    :type bits: sequence
    :returns: bits packed in bytes (the last byte is padded with zeros)
    :rtype: bytes
    """
    try:
        bits_rev = reversed(bits)
    except TypeError:
        # a sized iterable that isn't a sequence (like a set)
        bits_rev = reversed(list(bits))
    # bit i of the integer is bits[i]: build its binary string from the last bit to the first one
    digits = bytes(map(bool, bits_rev)).translate(_BITS_TO_DIGITS)
    return int(digits, 2).to_bytes(byte_length(len(digits)), 'little') if digits else b''


def _unpack_bits(data: bytes, bit_nb: int) -> list[bool]:
    """Unpack the first bit_nb bits of bytes (modbus order: LSB first in each byte) as a list of bool.

    :param data: bytes with the bits (can be longer than needed)
    :type data: bytes
    :param bit_nb: number of bits to unpack
    :type bit_nb: int
    :returns: list of bit_nb bool
    :rtype: list
    """
    bits: list[bool] = []
    for byte in data[:byte_length(bit_nb)]:
        bits.extend(_BYTE_TO_BITS[byte])
    return bits[:bit_nb]


def _to_bool(value: Any, name: str) -> bool:
    """Return value as bool from a boolean-like integer (bool, int or any object with __index__).

    :raises TypeError: if value cannot be interpreted as a boolean
    """
    try:
        return bool(operator.index(value))
    except TypeError:
        raise TypeError(f"{name} must be a bool or an integer") from None


def _to_int(value: Any, name: str) -> int:
    """Return value as an int, accept only int-like objects (int, bool or any object with __index__).

    :raises TypeError: if value can't be used as an integer (str, float...)
    """
    try:
        return operator.index(value)
    except TypeError:
        raise TypeError(f"{name} must be an int") from None


def _to_bool_list(values: Iterable[Any], name: str) -> list[bool]:
    """Return values as a list of bool from boolean-like integers (bool, int or any object with __index__).

    :raises TypeError: if an item cannot be interpreted as a boolean
    """
    try:
        # Python bools (the usual case) are taken as is, without a function call
        return [val if type(val) is bool else bool(operator.index(val)) for val in values]
    except TypeError:
        raise TypeError(f"{name} items must be a bool or an integer") from None


def _to_int_list(values: Iterable[Any], name: str) -> list[int]:
    """Return values as a list of int, accept only int-like items (int, bool or any object with __index__).

    :raises TypeError: if an item can't be used as an integer (str, float...)
    """
    try:
        return list(map(operator.index, values))
    except TypeError:
        raise TypeError(f"{name} items must be an int") from None
