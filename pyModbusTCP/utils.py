""" pyModbusTCP utils functions """

import re
import socket
import struct


###############
# bits function
###############
def get_bits_from_int(val_int, val_size=16):
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


def byte_length(bit_length):
    """Return the number of bytes needs to contain a bit_length structure.

    :param bit_length: the number of bits
    :type bit_length: int
    :returns: the number of bytes
    :rtype: int
    """
    return (bit_length + 7) // 8


def test_bit(value, offset):
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


def set_bit(value, offset):
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


def reset_bit(value, offset):
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


def toggle_bit(value, offset):
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
def word_list_to_long(val_list, big_endian=True, long_long=False):
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


def long_list_to_word(val_list, big_endian=True, long_long=False):
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
def get_2comp(val_int, val_size=16):
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


def get_list_2comp(val_list, val_size=16):
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
def decode_ieee(val_int, double=False):
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
        return struct.unpack("d", struct.pack("Q", val_int))[0]
    else:
        return struct.unpack("f", struct.pack("I", val_int))[0]


def encode_ieee(val_float, double=False):
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
        return struct.unpack("Q", struct.pack("d", val_float))[0]
    else:
        return struct.unpack("I", struct.pack("f", val_float))[0]


################
# misc functions
################
def crc16(frame):
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


def valid_host(host_str):
    """Validate a host string.

    Can be an IPv4/6 address or a valid hostname.

    :param host_str: the host string to test
    :type host_str: str
    :returns: True if host_str is valid
    :rtype: bool
    """
    # IPv4 valid address ?
    try:
        socket.inet_pton(socket.AF_INET, host_str)
        return True
    except socket.error:
        pass
    # IPv6 valid address ?
    try:
        socket.inet_pton(socket.AF_INET6, host_str)
        return True
    except socket.error:
        pass
    # valid hostname ?
    if len(host_str) > 255:
        return False
    # strip final dot, if present
    if host_str[-1] == '.':
        host_str = host_str[:-1]
    # validate each part of the hostname (part_1.part_2.part_3)
    re_part_ok = re.compile('(?!-)[a-z0-9-_]{1,63}(?<!-)$', re.IGNORECASE)
    return all(re_part_ok.match(part) for part in host_str.split('.'))
