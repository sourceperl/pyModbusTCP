""" pyModbusTCP server: Modbus/TCP frame containers (MBAP header, PDU and the full frame). """

from __future__ import annotations

import struct
from typing import Any

from ..constants import MBAP_MAX_LENGTH, MBAP_MIN_LENGTH, MBAP_SIZE
from .errors import DataFormatError, PDUDecodeError


class MBAP:
    """ MBAP (Modbus Application Protocol) container class. """

    def __init__(self, transaction_id: int = 0, protocol_id: int = 0, length: int = 0, unit_id: int = 0) -> None:
        # public
        self.transaction_id = transaction_id
        self.protocol_id = protocol_id
        self.length = length
        self.unit_id = unit_id

    @property
    def raw(self) -> bytes:
        try:
            return struct.pack('>HHHB', self.transaction_id,
                               self.protocol_id, self.length,
                               self.unit_id)
        except struct.error as e:
            raise DataFormatError(f'MBAP raw encode pack error: {e}')

    @raw.setter
    def raw(self, value: bytes) -> None:
        # close connection if no standard 7 bytes mbap header
        if not (value and len(value) == MBAP_SIZE):
            raise DataFormatError(f'MBAP must have a length of {MBAP_SIZE} bytes')
        # decode header
        (self.transaction_id, self.protocol_id,
         self.length, self.unit_id) = struct.unpack('>HHHB', value)
        # check frame header content inconsistency
        if self.protocol_id != 0:
            raise DataFormatError('MBAP protocol ID must be 0')
        if not MBAP_MIN_LENGTH <= self.length <= MBAP_MAX_LENGTH:
            msg = f'MBAP length must be between {MBAP_MIN_LENGTH} and {MBAP_MAX_LENGTH}'
            raise DataFormatError(msg)


class PDU:
    """ PDU (Protocol Data Unit) container class. """

    def __init__(self, raw: bytes = b'') -> None:
        """
        Constructor

        :param raw: raw PDU
        :type raw: bytes
        """
        self.raw = raw

    def __len__(self) -> int:
        return len(self.raw)

    @property
    def func_code(self) -> int:
        return self.raw[0]

    @property
    def except_code(self) -> int:
        return self.raw[1]

    @property
    def is_except(self) -> bool:
        return self.func_code > 0x7F

    @property
    def is_valid(self) -> bool:
        # PDU min length is 2 bytes
        return self.__len__() >= 2

    def clear(self) -> None:
        self.raw = b''

    def build_except(self, func_code: int, exp_status: int) -> PDU:
        self.clear()
        self.add_pack('BB', func_code + 0x80, exp_status)
        return self

    def add_pack(self, fmt: str, *args: Any) -> None:
        try:
            self.raw += struct.pack(fmt, *args)
        except struct.error:
            err_msg = f'unable to format PDU message (fmt: {fmt}, values: {args})'
            raise DataFormatError(err_msg)

    def unpack(self, fmt: str, from_byte: int | None = None, to_byte: int | None = None) -> tuple[Any, ...]:
        raw_section = self.raw[from_byte:to_byte]
        try:
            return struct.unpack(fmt, raw_section)
        except struct.error:
            err_msg = f"unable to decode PDU message  (fmt: '{fmt}', values: {raw_section!r})"
            raise PDUDecodeError(err_msg)


class Frame:
    def __init__(self) -> None:
        """ Modbus Frame container. """
        self.mbap = MBAP()
        self.pdu = PDU()

    @property
    def raw(self) -> bytes:
        self.mbap.length = len(self.pdu) + 1
        return self.mbap.raw + self.pdu.raw
