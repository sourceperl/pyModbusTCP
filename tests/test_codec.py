"""Tests of pyModbusTCP.codec."""

import math
from typing import Any

import pytest

from pyModbusTCP.codec import ByteOrder, ByteOrder64, Codec, DataType
from pyModbusTCP.server import IllegalDataAddress, MemoryStore

F32 = DataType.FLOAT32


@pytest.mark.parametrize('order, registers', [(ByteOrder.ABCD, [0x4048, 0xF5C3]), (ByteOrder.CDAB, [0xF5C3, 0x4048]),
                                              (ByteOrder.BADC, [0x4840, 0xC3F5]), (ByteOrder.DCBA, [0xC3F5, 0x4840])])
def test_byte_orders(order: ByteOrder, registers: list[int]) -> None:
    """The float32 3.14 (0x4048F5C3) in the 4 byte orders, and back."""
    codec = Codec(F32, order)
    assert codec.encode([3.14]) == registers
    assert codec.text(codec.decode(registers)[0]) == '3.14'
    assert codec.hex(codec.decode(registers)[0]) == '0x4048F5C3'


def test_enums() -> None:
    assert [t.value for t in DataType] == ['uint16', 'int16', 'uint32', 'int32', 'float32', 'uint64', 'int64',
                                           'float64']
    assert [t.words for t in DataType] == [1, 1, 2, 2, 2, 4, 4, 4]
    assert [t for t in DataType if t.is_float] == [DataType.FLOAT32, DataType.FLOAT64]
    # 4 byte orders, each one with an alias
    assert [o.name for o in ByteOrder] == ['ABCD', 'CDAB', 'BADC', 'DCBA']
    assert (ByteOrder.BIG_ENDIAN, ByteOrder.WORD_SWAP, ByteOrder.BYTE_SWAP, ByteOrder.LITTLE_ENDIAN) == \
        (ByteOrder.ABCD, ByteOrder.CDAB, ByteOrder.BADC, ByteOrder.DCBA)
    swaps = [(False, False), (True, False), (False, True), (True, True)]
    assert [(o.word_swap, o.byte_swap) for o in ByteOrder] == swaps
    # from a text (like the command line)
    assert DataType('float32') is F32 and ByteOrder('CDAB') is ByteOrder.WORD_SWAP


def test_types() -> None:
    assert Codec() == Codec(DataType.UINT16, ByteOrder.ABCD)
    assert Codec(F32, ByteOrder.WORD_SWAP) == Codec(F32, ByteOrder.CDAB)
    # 64 bits: CDAB reverses the 4 registers, BADC swaps the bytes of each register
    u64 = DataType.UINT64
    assert Codec(u64).encode([0x0102030405060708]) == [0x0102, 0x0304, 0x0506, 0x0708]
    assert Codec(u64, ByteOrder.CDAB).encode([0x0102030405060708]) == [0x0708, 0x0506, 0x0304, 0x0102]
    assert Codec(u64, ByteOrder.BADC).encode([0x0102030405060708]) == [0x0201, 0x0403, 0x0605, 0x0807]
    f64 = Codec(DataType.FLOAT64, ByteOrder.DCBA)
    assert f64.decode(f64.encode([-2.5])) == [-2.5]
    # several values (each one reordered), signed values
    assert Codec(DataType.UINT32, ByteOrder.CDAB).decode([2, 1, 4, 3]) == [0x10002, 0x30004]
    assert Codec(DataType.INT32).decode([0xFFFF, 0xFFFE, 0x0000, 0x0001]) == [-2, 1]
    assert Codec(DataType.INT16, ByteOrder.BADC).decode([0xFEFF]) == [-2]
    assert Codec(F32).encode([1, 2.5]) == [0x3F80, 0x0000, 0x4020, 0x0000]
    assert math.isnan(Codec(F32).decode([0x7FC0, 0x0000])[0])
    assert Codec().encode([]) == [] and Codec().decode([]) == []


def test_errors() -> None:
    # strings are refused: the enums catch a typo at once
    # (bad arguments typed Any: they are deliberately not of the annotated type)
    bad_args: list[tuple[Any, ...]] = [('float32',), (F32, 'CDAB'), (F32, 1)]
    for args in bad_args:
        with pytest.raises(TypeError):
            Codec(*args)
    with pytest.raises(AttributeError):
        DataType.FLOAT16  # type: ignore[attr-defined]  # noqa: B018
    for codec, values in ((Codec(DataType.INT32), [2 ** 31]), (Codec(), [-1]), (Codec(F32), [1e39])):
        with pytest.raises(ValueError, match='out of the'):
            codec.encode(values)
    for codec, value in ((Codec(DataType.INT32), 1.5), (Codec(), True), (Codec(F32), '1')):
        with pytest.raises(TypeError):
            codec.encode([value])  # type: ignore[list-item]
    for registers in ([0x0001], [0x10000, 0], [-1, 0]):
        with pytest.raises(ValueError):
            Codec(DataType.UINT32).decode(registers)
    # a register out of range is refused with every byte order: a byte swap (BADC, DCBA) kept its 16 low bits only,
    # 0x12345 was decoded as 0x4523; and never a bool or a float
    orders: list[ByteOrder | ByteOrder64] = [*ByteOrder, *ByteOrder64]
    for order in orders:
        codec = Codec(DataType.UINT64 if isinstance(order, ByteOrder64) else DataType.UINT32, order)
        for bad in (0x12345, -1, True, 1.0):
            bad_registers: list[Any] = [bad] + [0] * (codec.words - 1)
            with pytest.raises(ValueError, match='between 0 and 65535'):
                codec.decode(bad_registers)
        assert codec.decode([0xFFFF] * codec.words) == [2 ** (16 * codec.words) - 1]


def test_text() -> None:
    f32 = Codec(F32)
    # the shortest text that gives back the float32, the float look of an integral value
    assert [f32.text(v) for v in (0.1, 10.0, 1e-45, float('nan'), float('-inf'))] == \
        ['0.1', '10.0', '1e-45', 'nan', '-inf']
    assert f32.text(f32.decode(f32.encode([0.1]))[0]) == '0.1'
    # a float32 that needs 9 significant digits
    value = f32.decode(f32.encode([0.0110045085]))[0]
    assert f32.text(value) == '0.0110045085'
    # every text gives back its float32
    for raw in range(0x3C000000, 0x3C000000 + 0x200000, 997):
        value = f32.decode([raw >> 16, raw & 0xFFFF])[0]
        assert f32.decode(f32.encode([float(f32.text(value))]))[0] == value, hex(raw)
    assert Codec(DataType.FLOAT64).text(0.1) == '0.1' and Codec(DataType.INT32).text(-2) == '-2'
    assert Codec(DataType.INT32).parse('-0x10') == -16 and Codec(DataType.FLOAT64).parse('1e300') == 1e300
    for data_type, text in ((DataType.INT32, '0x80000000'), (DataType.UINT16, '-1'), (F32, '1e39'),
                            (DataType.INT16, '1.5'), (F32, 'abc')):
        with pytest.raises(ValueError):
            Codec(data_type).parse(text)


def test_store_values() -> None:
    """RegistersAccess.read_values() and write_values() of the server store."""
    regs = MemoryStore(16).app.holding_registers
    f32 = Codec(F32, ByteOrder.CDAB)
    regs.write_values(0, [3.14, -2.5], f32)
    assert regs.read(0, 4) == [0xF5C3, 0x4048, 0x0000, 0xC020]
    assert [f32.text(v) for v in regs.read_values(0, f32, 2)] == ['3.14', '-2.5']
    # nothing written for an invalid value
    with pytest.raises(ValueError):
        regs.write_values(0, [1.0, 1e39], f32)
    assert regs.read(0, 2) == [0xF5C3, 0x4048]
    with pytest.raises(IllegalDataAddress):
        regs.read_values(14, Codec(DataType.FLOAT64))


@pytest.mark.parametrize('order, registers', [
    (ByteOrder64.ABCDEFGH, [0x0102, 0x0304, 0x0506, 0x0708]), (ByteOrder64.CDABGHEF, [0x0304, 0x0102, 0x0708, 0x0506]),
    (ByteOrder64.EFGHABCD, [0x0506, 0x0708, 0x0102, 0x0304]), (ByteOrder64.GHEFCDAB, [0x0708, 0x0506, 0x0304, 0x0102]),
    (ByteOrder64.BADCFEHG, [0x0201, 0x0403, 0x0605, 0x0807]), (ByteOrder64.DCBAHGFE, [0x0403, 0x0201, 0x0807, 0x0605]),
    (ByteOrder64.FEHGBADC, [0x0605, 0x0807, 0x0201, 0x0403]), (ByteOrder64.HGFEDCBA, [0x0807, 0x0605, 0x0403, 0x0201]),
])
def test_byte_order64(order: ByteOrder64, registers: list[int]) -> None:
    """The 8 orders of a 64 bits value (A=01 ... H=08), and back, for the 3 types of 64 bits."""
    codec = Codec(DataType.UINT64, order)
    assert codec.encode([0x0102030405060708]) == registers
    assert codec.decode(registers) == [0x0102030405060708]
    assert Codec(DataType.INT64, order).decode(Codec(DataType.INT64, order).encode([-2, 7])) == [-2, 7]
    assert Codec(DataType.FLOAT64, order).decode(Codec(DataType.FLOAT64, order).encode([-2.5])) == [-2.5]


def test_byte_order64_rules() -> None:
    # the 4 orders of ByteOrder are 4 of the 8 orders of ByteOrder64
    for short, long in ((ByteOrder.ABCD, ByteOrder64.ABCDEFGH), (ByteOrder.CDAB, ByteOrder64.GHEFCDAB),
                        (ByteOrder.BADC, ByteOrder64.BADCFEHG), (ByteOrder.DCBA, ByteOrder64.HGFEDCBA)):
        value = [0x0102030405060708]
        assert Codec(DataType.UINT64, short).encode(value) == Codec(DataType.UINT64, long).encode(value)
    # only for the 64 bits types
    for data_type in (DataType.UINT16, DataType.INT32, DataType.FLOAT32):
        with pytest.raises(ValueError, match='ByteOrder64 is for a 64 bits type'):
            Codec(data_type, ByteOrder64.CDABGHEF)
    assert Codec(DataType.FLOAT64, ByteOrder64.CDABGHEF).byte_order is ByteOrder64.CDABGHEF


def test_store_byte_order64() -> None:
    """The 64 bits shortcuts of the store take a ByteOrder64 (typed and documented), the 32 bits ones refuse it."""
    regs = MemoryStore().app.holding_registers
    regs.write_uint64(0, 0x0102030405060708, ByteOrder64.CDABGHEF)
    assert regs.read(0, 4) == [0x0304, 0x0102, 0x0708, 0x0506]
    assert regs.read_uint64(0, ByteOrder64.CDABGHEF) == 0x0102030405060708
    regs.write_int64(4, -2, ByteOrder64.EFGHABCD)
    assert regs.read_int64(4, ByteOrder64.EFGHABCD) == -2
    regs.write_float64(8, 1.5, ByteOrder64.HGFEDCBA)
    assert regs.read_float64(8, ByteOrder64.HGFEDCBA) == regs.read_float64(8, ByteOrder.DCBA) == 1.5
    # a 32 bits type has no ByteOrder64: refused, nothing written
    with pytest.raises(ValueError, match='ByteOrder64 is for a 64 bits type'):
        regs.write_uint32(12, 1, ByteOrder64.CDABGHEF)  # type: ignore[arg-type]
    assert regs.read(12, 2) == [0, 0]
