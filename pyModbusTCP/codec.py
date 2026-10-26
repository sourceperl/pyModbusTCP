"""Values on 16 bits registers: data types and byte orders.

A Modbus register holds a 16 bits value, sent big endian. A device stores a 32 or 64 bits value on 2 or 4
consecutive registers, in an order that it chooses: there is no standard. A :class:`Codec` converts the values of a
:class:`DataType` to registers and back, in a :class:`ByteOrder`, named in the notation of most Modbus software,
where A is the most significant byte of the value:

==========================================  =====================================================================
``ByteOrder.ABCD`` (``BIG_ENDIAN``)         the first register holds the most significant word (default)
``ByteOrder.CDAB`` (``WORD_SWAP``)          the first register holds the least significant word (common on PLCs)
``ByteOrder.BADC`` (``BYTE_SWAP``)          the bytes of each register are swapped
``ByteOrder.DCBA`` (``LITTLE_ENDIAN``)      word swap and byte swap
==========================================  =====================================================================

For a 64 bits value, ``CDAB`` reverses the order of the 4 registers. For a 16 bits value, only the byte swap
(``BADC``, ``DCBA``) changes something.

A few devices store a 64 bits value in another order, like two 32 bits values in word swap (``CDABGHEF``): for
these rare cases, :class:`ByteOrder64` gives the 8 orders of the 8 bytes (A to H) on 4 registers, for a
:class:`Codec` of a 64 bits type only.

.. code-block:: python

    from pyModbusTCP.codec import ByteOrder, Codec, DataType

    f32 = Codec(DataType.FLOAT32, ByteOrder.CDAB)
    f32.encode([3.14])              # [0xF5C3, 0x4048]
    f32.decode([0xF5C3, 0x4048])    # [3.140000104904175]
"""

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = ['ByteOrder', 'ByteOrder64', 'Codec', 'DataType', 'Number']

#: A value of a data type: an int, or a float for ``FLOAT32`` and ``FLOAT64``.
Number = int | float


class DataType(Enum):
    """The type of a value on registers (the value of a member is its name in the command line tools)."""

    UINT16 = 'uint16'
    """Unsigned 16 bits int, 1 register."""
    INT16 = 'int16'
    """Signed 16 bits int (2's complement), 1 register."""
    UINT32 = 'uint32'
    """Unsigned 32 bits int, 2 registers."""
    INT32 = 'int32'
    """Signed 32 bits int (2's complement), 2 registers."""
    FLOAT32 = 'float32'
    """IEEE 754 single precision float, 2 registers."""
    UINT64 = 'uint64'
    """Unsigned 64 bits int, 4 registers."""
    INT64 = 'int64'
    """Signed 64 bits int (2's complement), 4 registers."""
    FLOAT64 = 'float64'
    """IEEE 754 double precision float, 4 registers."""

    @property
    def words(self) -> int:
        """The number of registers of a value: 1, 2 or 4."""
        return _FORMATS[self][1]

    @property
    def is_float(self) -> bool:
        """True for FLOAT32 and FLOAT64."""
        return self in (DataType.FLOAT32, DataType.FLOAT64)


class ByteOrder(Enum):
    """The order of the bytes of a value on the registers, A being the most significant byte of the value.

    Each order has 2 names: ``ABCD`` and ``BIG_ENDIAN`` are the same member.
    """

    ABCD = 'ABCD'
    """Big endian: the first register holds the most significant word (the order of the Modbus specification)."""
    CDAB = 'CDAB'
    """Word swap: the first register holds the least significant word (common on PLCs)."""
    BADC = 'BADC'
    """Byte swap: the bytes of each register are swapped."""
    DCBA = 'DCBA'
    """Little endian: word swap and byte swap."""

    # aliases (the same members)
    BIG_ENDIAN = ABCD
    WORD_SWAP = CDAB
    BYTE_SWAP = BADC
    LITTLE_ENDIAN = DCBA

    @property
    def word_swap(self) -> bool:
        """True if the first register holds the least significant word (CDAB, DCBA)."""
        return self in (ByteOrder.CDAB, ByteOrder.DCBA)

    @property
    def byte_swap(self) -> bool:
        """True if the bytes of each register are swapped (BADC, DCBA)."""
        return self in (ByteOrder.BADC, ByteOrder.DCBA)


class ByteOrder64(Enum):
    """The order of the 8 bytes of a 64 bits value on 4 registers, A being the most significant byte.

    For the rare devices that ByteOrder doesn't cover (only with a :class:`Codec` of UINT64, INT64 or FLOAT64). The
    value of a member is its notation: the bytes in the order of the registers, 2 per register.
    """

    ABCDEFGH = 'ABCDEFGH'
    """Big endian (like ByteOrder.ABCD)."""
    CDABGHEF = 'CDABGHEF'
    """Word swap in each 32 bits half (two 32 bits values in CDAB)."""
    EFGHABCD = 'EFGHABCD'
    """The two 32 bits halves swapped, each half big endian."""
    GHEFCDAB = 'GHEFCDAB'
    """The 4 registers reversed (like ByteOrder.CDAB)."""
    BADCFEHG = 'BADCFEHG'
    """Byte swap in each register (like ByteOrder.BADC)."""
    DCBAHGFE = 'DCBAHGFE'
    """Each 32 bits half little endian, the halves in big endian order."""
    FEHGBADC = 'FEHGBADC'
    """The halves swapped, byte swap in each register."""
    HGFEDCBA = 'HGFEDCBA'
    """Little endian (like ByteOrder.DCBA)."""

    @property
    def _mask(self) -> int:
        # each order moves the byte i of the value to the position i ^ mask (so it's its own inverse)
        return ord(self.value[0]) - ord('A')


# data type -> (struct format of a value, number of registers)
_FORMATS = {
    DataType.UINT16: ('H', 1), DataType.INT16: ('h', 1),
    DataType.UINT32: ('I', 2), DataType.INT32: ('i', 2), DataType.FLOAT32: ('f', 2),
    DataType.UINT64: ('Q', 4), DataType.INT64: ('q', 4), DataType.FLOAT64: ('d', 4),
}


@dataclass(frozen=True)
class Codec:
    """Convert the values of a data type to registers, and back, in a byte order.

    A codec is immutable: create one per data type and byte order of a device, and reuse it.

    :param data_type: the type of the values
    :param byte_order: the order of the bytes on the registers, a ByteOrder64 for a rare order of a 64 bits type
    :raises TypeError: if data_type is not a DataType, or byte_order not a ByteOrder (or ByteOrder64)
    :raises ValueError: for a ByteOrder64 with a type of 1 or 2 registers
    """

    data_type: DataType = DataType.UINT16
    byte_order: ByteOrder | ByteOrder64 = ByteOrder.ABCD

    def __post_init__(self) -> None:
        if not isinstance(self.data_type, DataType):
            raise TypeError(f'data_type must be a DataType (like DataType.FLOAT32), not {self.data_type!r}')
        if not isinstance(self.byte_order, (ByteOrder, ByteOrder64)):
            raise TypeError(f'byte_order must be a ByteOrder (like ByteOrder.CDAB), not {self.byte_order!r}')
        if isinstance(self.byte_order, ByteOrder64) and self.data_type.words != 4:
            raise ValueError(f'a ByteOrder64 is for a 64 bits type, not {self.data_type.value}: use a ByteOrder')

    @property
    def words(self) -> int:
        """The number of registers of a value: 1, 2 or 4."""
        return self.data_type.words

    @property
    def is_float(self) -> bool:
        """True for FLOAT32 and FLOAT64."""
        return self.data_type.is_float

    @property
    def _fmt(self) -> str:
        return '>' + _FORMATS[self.data_type][0]

    def _reorder(self, registers: Sequence[int]) -> list[int]:
        """The registers of a value: device order <-> ABCD order (the operation is its own inverse)."""
        if isinstance(self.byte_order, ByteOrder64):
            raw = b''.join(r.to_bytes(2, 'big') for r in registers)
            mask = self.byte_order._mask
            raw = bytes(raw[i ^ mask] for i in range(8))
            return [int.from_bytes(raw[i:i + 2], 'big') for i in range(0, 8, 2)]
        regs = list(reversed(registers)) if self.byte_order.word_swap else list(registers)
        if self.byte_order.byte_swap:
            regs = [((r & 0xFF) << 8) | (r >> 8) for r in regs]
        return regs

    def decode(self, registers: Sequence[int]) -> list[Number]:
        """The values stored on registers.

        :param registers: register values (0 to 65535), :attr:`words` per value
        :returns: the values (int, or float for a float type)
        :raises ValueError: if the number of registers is not a multiple of :attr:`words`, or for a register that is
            not an int between 0 and 65535
        """
        words = self.words
        if len(registers) % words:
            raise ValueError(f'{len(registers)} registers: a {self.data_type.value} value takes {words} registers')
        # checked before the reorder: a byte swap (BADC, DCBA) keeps the 16 low bits only, 0x12345 would pass as 0x4523
        for r in registers:
            if isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= 0xFFFF:
                raise ValueError(f'a register value must be an int between 0 and 65535, not {r!r}')
        raw = b''.join(r.to_bytes(2, 'big') for i in range(0, len(registers), words)
                       for r in self._reorder(registers[i:i + words]))
        return [v[0] for v in struct.iter_unpack(self._fmt, raw)]

    def encode(self, values: Sequence[Number]) -> list[int]:
        """The registers of values, ready for a write.

        :param values: the values (int, or int/float for a float type)
        :returns: the register values, :attr:`words` per value
        :raises TypeError: for a value that is not a number, or a float for an integer type
        :raises ValueError: for a value out of the range of the type
        """
        registers: list[int] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float) if self.is_float else int):
                raise TypeError(f'{value!r} is not a {self.data_type.value} value')
            try:
                raw = struct.pack(self._fmt, value)
            except (struct.error, OverflowError):
                raise ValueError(f'{value!r} out of the {self.data_type.value} range') from None
            regs = [int.from_bytes(raw[i:i + 2], 'big') for i in range(0, len(raw), 2)]
            registers.extend(self._reorder(regs))
        return registers

    def parse(self, text: str) -> Number:
        """A value from a text: an int in any Python notation (42, 0x2a, 0b101010...), a float for a float type.

        :raises ValueError: for a text that is not a value of the type
        """
        try:
            value: Number = float(text) if self.is_float else int(text, 0)
        except ValueError:
            raise ValueError(f'{text!r} is not {"a number" if self.is_float else "an integer"}') from None
        self.encode([value])
        return value

    def text(self, value: Number) -> str:
        """A value as text, the shortest one that gives back the same value of the type.

        A FLOAT32 read as 3.140000104904175 is "3.14"; an integral float keeps its float look ("10.0").
        """
        if not isinstance(value, float) or not math.isfinite(value):
            return str(value)
        if self.data_type is DataType.FLOAT64:
            return repr(value)
        # 9 significant digits always give back a float32: keep them unless a shorter text gives it back too
        text = f'{value:.9g}'
        for precision in range(6, 9):
            shorter = f'{value:.{precision}g}'
            if struct.unpack('>f', struct.pack('>f', float(shorter)))[0] == value:
                text = shorter
                break
        return text if any(c in text for c in '.en') else text + '.0'

    def bits(self, value: Number) -> int:
        """The bits of a value (its bytes in the ABCD order) as an unsigned int: 0x4048F5C3 for the FLOAT32 3.14."""
        return int.from_bytes(struct.pack(self._fmt, value), 'big')

    def hex(self, value: Number) -> str:
        """The bits of a value in hexadecimal: "0x4048F5C3" for the FLOAT32 3.14."""
        return f'0x{self.bits(value):0{self.words * 4}X}'
