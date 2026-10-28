""" pyModbusTCP server: the Modbus functions, as handlers of a registry.

A function handler takes a Request and returns the response PDU (bytes, the function code first). It raises a
ModbusException (like IllegalDataAddress) for an exception response. The server routes each request to the handler
of its function code (ModbusServer.functions), or to its fallback for the other codes::

    @server.function(0x41)
    def my_function(req: Request) -> bytes:
        (address,) = req.unpack('>H', 1, 3)
        with req.transaction() as tx:
            value = tx.read(Space.HOLDING_REGISTERS, address)[0]
        return req.reply('>H', value)

Every standard function runs in one transaction of the store: it's atomic (like the write then the read of 0x17).
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    MAX_PDU_SIZE,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
    WRITE_MULTIPLE_COILS,
    WRITE_MULTIPLE_REGISTERS,
    WRITE_READ_MULTIPLE_REGISTERS,
    WRITE_SINGLE_COIL,
    WRITE_SINGLE_REGISTER,
)
from ..utils import _pack_bits, _unpack_bits
from .errors import (
    DataFormatError,
    IllegalDataAddress,
    IllegalDataValue,
    PDUDecodeError,
)
from .store import DataStore, RequestContext, Space, Transaction

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from .device_id import DeviceIdentification


@dataclass
class Request:
    """A request given to a function handler."""

    # the request PDU (function code first)
    pdu: bytes
    # where the request comes from (client, unit id, function code)
    ctx: RequestContext
    # the data store of the unit id of the request
    store: DataStore
    # the device identification of the server (function 0x2B/0x0E), if any
    device_id: DeviceIdentification | None = None

    @property
    def function_code(self) -> int:
        """The function code of the request (the first byte of the PDU)."""
        return self.pdu[0]

    def unpack(self, fmt: str, from_byte: int | None = None, to_byte: int | None = None) -> tuple[Any, ...]:
        """Decode a part of the PDU with struct.

        :param fmt: struct format; its byte order prefix is used as is ('<' for little endian data...), a format
            without prefix is big endian (the byte order of Modbus), not the native order of struct
        :raises PDUDecodeError: if this part is too short (the client gets an "illegal data value" exception)
        """
        fmt = _with_byte_order(fmt)
        raw_section = self.pdu[from_byte:to_byte]
        try:
            return struct.unpack(fmt, raw_section)
        except struct.error:
            raise PDUDecodeError(f"unable to decode PDU (fmt: '{fmt}', values: {raw_section!r})") from None

    def reply(self, fmt: str = '', *args: Any) -> bytes:
        """Build a response PDU: the function code of the request, then args packed with struct.

        :param fmt: struct format of the fields after the function code; its byte order prefix is used as is ('<'
            for little endian data...), a format without prefix is big endian (the byte order of Modbus), not the
            native order of struct
        :raises DataFormatError: if a value doesn't fit its field (the client gets a "slave device failure")
        """
        fmt = _with_byte_order(fmt)
        try:
            return bytes([self.function_code]) + struct.pack(fmt, *args)
        except struct.error as e:
            raise DataFormatError(f'unable to build the response PDU (fmt: {fmt}, values: {args}): {e}') from None

    def transaction(self) -> AbstractContextManager[Transaction]:
        """A transaction of the store, with the context of this request."""
        return self.store.transaction(self.ctx)


def _with_byte_order(fmt: str) -> str:
    """A struct format with a byte order: its own prefix if any ('<', '>', '!', '=', '@'), else big endian ('>', the
    byte order of Modbus) instead of the native order of struct."""
    return fmt if fmt[:1] in ('<', '>', '!', '=', '@') else '>' + fmt


# a function handler: returns the response PDU, raises a ModbusException for an exception response
FunctionHandler = Callable[[Request], bytes]


def read_bits(req: Request) -> bytes:
    """Functions Read Coils (0x01) and Read Discrete Inputs (0x02)."""
    (address, quantity) = req.unpack('>HH', 1, 5)
    if not 0x0001 <= quantity <= 0x07D0:
        raise IllegalDataValue()
    space = Space.COILS if req.function_code == READ_COILS else Space.DISCRETE_INPUTS
    with req.transaction() as tx:
        bits = tx.read(space, address, quantity)
    data = _pack_bits(bits)
    return req.reply(f'>B{len(data)}s', len(data), data)


def read_words(req: Request) -> bytes:
    """Functions Read Holding Registers (0x03) and Read Input Registers (0x04)."""
    (address, quantity) = req.unpack('>HH', 1, 5)
    if not 0x0001 <= quantity <= 0x007D:
        raise IllegalDataValue()
    space = Space.HOLDING_REGISTERS if req.function_code == READ_HOLDING_REGISTERS else Space.INPUT_REGISTERS
    with req.transaction() as tx:
        words = tx.read(space, address, quantity)
    return req.reply(f'>B{quantity}H', quantity * 2, *words)


def write_single_coil(req: Request) -> bytes:
    """Function Write Single Coil (0x05)."""
    (address, value) = req.unpack('>HH', 1, 5)
    if value not in (0x0000, 0xFF00):
        raise IllegalDataValue()
    with req.transaction() as tx:
        tx.write(Space.COILS, address, [value == 0xFF00])
    return req.reply('>HH', address, value)


def write_single_register(req: Request) -> bytes:
    """Function Write Single Register (0x06)."""
    (address, value) = req.unpack('>HH', 1, 5)
    with req.transaction() as tx:
        tx.write(Space.HOLDING_REGISTERS, address, [value])
    return req.reply('>HH', address, value)


def write_multiple_coils(req: Request) -> bytes:
    """Function Write Multiple Coils (0x0F)."""
    (address, quantity, byte_count) = req.unpack('>HHB', 1, 6)
    # byte count must match the quantity, the data must be complete, any extra bytes after them are ignored
    if not (0x0001 <= quantity <= 0x07B0 and byte_count == (quantity + 7) // 8 and len(req.pdu) >= 6 + byte_count):
        raise IllegalDataValue()
    bits = _unpack_bits(req.pdu[6:6 + byte_count], quantity)
    with req.transaction() as tx:
        tx.write(Space.COILS, address, bits)
    return req.reply('>HH', address, quantity)


def write_multiple_registers(req: Request) -> bytes:
    """Function Write Multiple Registers (0x10)."""
    (address, quantity, byte_count) = req.unpack('>HHB', 1, 6)
    if not (0x0001 <= quantity <= 0x007B and byte_count == quantity * 2 and len(req.pdu) >= 6 + byte_count):
        raise IllegalDataValue()
    words = list(req.unpack(f'>{quantity}H', 6, 6 + byte_count))
    with req.transaction() as tx:
        tx.write(Space.HOLDING_REGISTERS, address, words)
    return req.reply('>HH', address, quantity)


def write_read_multiple_registers(req: Request) -> bytes:
    """Function Write Read Multiple Registers (0x17).

    The write is done before the read, in one transaction: no other access can occur between them, and if the read
    fails (like an invalid address), the write is cancelled.
    """
    (read_address, read_quantity, write_address, write_quantity, byte_count) = req.unpack('>HHHHB', 1, 10)
    if not (0x0001 <= write_quantity <= 0x0079 and byte_count == write_quantity * 2
            and len(req.pdu) >= 10 + byte_count and 0x0001 <= read_quantity <= 0x007D):
        raise IllegalDataValue()
    words = list(req.unpack(f'>{write_quantity}H', 10, 10 + byte_count))
    with req.transaction() as tx:
        tx.write(Space.HOLDING_REGISTERS, write_address, words)
        read_words_l = tx.read(Space.HOLDING_REGISTERS, read_address, read_quantity)
    return req.reply(f'>B{read_quantity}H', read_quantity * 2, *read_words_l)


# read device identification, stream access: ReadDevId code -> (first, last) object id of its category
_DEVICE_ID_STREAM_RANGES = {1: (0x00, 0x02), 2: (0x03, 0x7f), 3: (0x80, 0xff)}


def encapsulated_interface_transport(req: Request) -> bytes:
    """Modbus Encapsulated Interface transport (0x2B): only the read device identification (MEI type 0x0E)."""
    (mei_type,) = req.unpack('B', 1, 2)
    if mei_type != MEI_TYPE_READ_DEVICE_ID:
        raise IllegalDataAddress()
    (device_id_code, object_id) = req.unpack('BB', 2, 4)
    if device_id_code not in (1, 2, 3, 4):
        raise IllegalDataValue()
    device_id = req.device_id
    if device_id is None:
        raise IllegalDataAddress()
    if device_id_code == 4:
        # individual access: an unknown object is an illegal data address
        if object_id not in device_id:
            raise IllegalDataAddress()
        objects = device_id.items(start=object_id, end=object_id)
    else:
        # stream access: basic (code 1), regular (code 2) or extended (code 3) objects
        first_id, last_id = _DEVICE_ID_STREAM_RANGES[device_id_code]
        # an object id that is not a known object of the category: restart at its first object (as the spec
        # requires for object 0)
        if not (first_id <= object_id <= last_id and object_id in device_id):
            object_id = first_id
        objects = device_id.items(start=object_id, end=last_id)
    conformity_level = device_id.conformity_level
    more_follows = 0
    next_object_id = 0
    head_size = 7
    data = b''
    number_of_objs = 0
    for obj_id, obj_value in objects:
        block = struct.pack(f'BB{len(obj_value)}s', obj_id, len(obj_value), obj_value)
        # stop before an object that would exceed the max PDU size: the client asks the next one
        if head_size + len(data) + len(block) > MAX_PDU_SIZE:
            more_follows = 0xff
            next_object_id = obj_id
            break
        data += block
        number_of_objs += 1
    return req.reply('>BBBBBB', mei_type, device_id_code, conformity_level, more_follows, next_object_id,
                     number_of_objs) + data


# the standard functions of the server (a copy is the default of ModbusServer.functions)
STANDARD_FUNCTIONS: dict[int, FunctionHandler] = {
    READ_COILS: read_bits,
    READ_DISCRETE_INPUTS: read_bits,
    READ_HOLDING_REGISTERS: read_words,
    READ_INPUT_REGISTERS: read_words,
    WRITE_SINGLE_COIL: write_single_coil,
    WRITE_SINGLE_REGISTER: write_single_register,
    WRITE_MULTIPLE_COILS: write_multiple_coils,
    WRITE_MULTIPLE_REGISTERS: write_multiple_registers,
    WRITE_READ_MULTIPLE_REGISTERS: write_read_multiple_registers,
    ENCAPSULATED_INTERFACE_TRANSPORT: encapsulated_interface_transport,
}
