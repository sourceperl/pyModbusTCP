"""Tests of pyModbusTCP.codec."""

import math

import pytest

from pyModbusTCP.codec import ByteOrder, Codec, DataType
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
    for args in (('float32',), (F32, 'CDAB'), (F32, 1)):
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


def test_text() -> None:
    f32 = Codec(F32)
    # the shortest text that gives back the float32, the float look of an integral value
    assert [f32.text(v) for v in (0.1, 10.0, 1e-45, float('nan'), float('-inf'))] == \
        ['0.1', '10.0', '1e-45', 'nan', '-inf']
    assert f32.text(f32.decode(f32.encode([0.1]))[0]) == '0.1'
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
