""" pyModbusTCP server: the data store, where the server reads and writes the Modbus data.

Every request of a client runs in a transaction of the store::

    with store.transaction(ctx) as tx:
        tx.write(Space.HOLDING_REGISTERS, 10, [1, 2])
        values = tx.read(Space.HOLDING_REGISTERS, 0, 4)

A transaction is atomic: no other request (or application access) can run during it, and if an exception is raised
inside it, every value it wrote is restored. The changes are notified to the listeners (see DataStore.subscribe())
once the transaction is committed.

The application accesses the store with store.app (no request context): store.app.holding_registers.write(0, [1]).
"""

from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NamedTuple

from ..codec import ByteOrder, Codec, DataType, Number
from ..utils import _to_bool_list, _to_int_list
from ._common import logger
from .errors import IllegalDataAddress, SlaveDeviceFailure
from .session import ClientInfo


class Space(Enum):
    """The 4 Modbus data spaces."""

    COILS = 'coils'
    DISCRETE_INPUTS = 'discrete_inputs'
    HOLDING_REGISTERS = 'holding_registers'
    INPUT_REGISTERS = 'input_registers'

    @property
    def is_bit(self) -> bool:
        """True for the spaces of bits (coils and discrete inputs), False for the spaces of 16 bits registers."""
        return self in (Space.COILS, Space.DISCRETE_INPUTS)

    def check_values(self, values: Iterable[Any]) -> list[Any]:
        """Return values as a list of bool (space of bits) or of 16 bits int (space of registers).

        :raises TypeError: if an item is not a bool/int (str, float...)
        :raises ValueError: if a register value is out of the 0 to 65535 range
        """
        if self.is_bit:
            return _to_bool_list(values, 'values')
        words = _to_int_list(values, 'values')
        if words and (min(words) < 0 or max(words) > 0xFFFF):
            raise ValueError('a register value must be between 0 and 65535')
        return words


@dataclass(frozen=True)
class RequestContext:
    """Where a request comes from: given to the transactions, the store wrappers and the change listeners.

    The application accesses the store without context (None).
    """

    client: ClientInfo = field(default_factory=ClientInfo)
    unit_id: int = 0
    function_code: int = 0


class Change(NamedTuple):
    """A value changed by a transaction."""

    space: Space
    address: int
    old: Any
    new: Any


@dataclass(frozen=True)
class ChangeEvent:
    """The changes of one committed transaction (one client request, or one application access)."""

    ctx: RequestContext | None
    changes: tuple[Change, ...]

    @property
    def by_client(self) -> bool:
        """True for the changes of a client request, False for an application write."""
        return self.ctx is not None

    def of(self, space: Space) -> tuple[Change, ...]:
        """The changes in a data space."""
        return tuple(change for change in self.changes if change.space is space)


# a listener of the changes, see DataStore.subscribe()
Listener = Callable[[ChangeEvent], None]


class Transaction(ABC):
    """The access to the data during a transaction (see DataStore.transaction())."""

    ctx: RequestContext | None

    @abstractmethod
    def read(self, space: Space, address: int, count: int = 1) -> list[Any]:
        """Read count values from address.

        :returns: a list of count bool (space of bits) or int (space of registers)
        :raises ModbusException: like IllegalDataAddress for an address out of the space
        """

    @abstractmethod
    def write(self, space: Space, address: int, values: Iterable[Any]) -> None:
        """Write values from address (nothing is written if a part of the range is invalid).

        :raises ModbusException: like IllegalDataAddress for an address out of the space
        :raises TypeError, ValueError: for an invalid value (see Space.check_values())
        """


class DataStore(ABC):
    """Where the server reads and writes the Modbus data: the base class of every store and store wrapper."""

    @abstractmethod
    def transaction(self, ctx: RequestContext | None = None) -> Any:
        """Return a context manager that runs an atomic transaction (it gives a Transaction object).

        :param ctx: the context of a client request, None for the application
        """

    @abstractmethod
    def subscribe(self, listener: Listener, app_writes: bool = False, threaded: bool = False) -> Callable[[], None]:
        """Call listener with a ChangeEvent after each transaction that changed some values.

        :param listener: a function called with the ChangeEvent (its exceptions are logged)
        :param app_writes: also notify the writes of the application (by default, only the client requests)
        :param threaded: call the listener from a thread of the store instead of the thread that committed the
            transaction (the session thread of the client: its response waits for the listener)
        :returns: a function to call to unsubscribe
        """

    @property
    def app(self) -> AppAccess:
        """The access of the application to the data (no client context, the wrappers don't apply)."""
        return AppAccess(self)


# --- the in-memory store ---

@dataclass
class _Mapping:
    """A range of addresses whose values are computed (see MemoryStore.map())."""

    start: int
    count: int
    read: Callable[[int, int, RequestContext | None], Sequence[Any]]
    write: Callable[[int, list[Any], RequestContext | None], None] | None

    @property
    def end(self) -> int:
        return self.start + self.count


@dataclass
class _ListenerEntry:
    listener: Listener
    app_writes: bool
    threaded: bool


class _MemoryTransaction(Transaction):
    """A transaction of a MemoryStore: runs with the lock of the store, keeps a journal to roll back."""

    def __init__(self, store: MemoryStore, ctx: RequestContext | None) -> None:
        self.ctx = ctx
        self._store = store
        # journal of the memory writes (space, address, previous values), to restore them on an exception
        self._journal: list[tuple[Space, int, list[Any]]] = []
        # (space, address) -> [first old value, last new value], in the order of the first change
        self._changes: dict[tuple[Space, int], list[Any]] = {}

    def read(self, space: Space, address: int, count: int = 1) -> list[Any]:
        values: list[Any] = []
        memory = self._store._data[space]
        for mapping, seg_address, seg_count in self._store._segments(space, address, count):
            if mapping is None:
                values.extend(memory[seg_address:seg_address + seg_count])
            else:
                values.extend(self._mapped_read(space, mapping, seg_address, seg_count))
        return values

    def _mapped_read(self, space: Space, mapping: _Mapping, address: int, count: int) -> list[Any]:
        """Call the read function of a mapping, check what it returns (a slave device failure if it's invalid)."""
        try:
            values = space.check_values(mapping.read(address, count, self.ctx))
        except (TypeError, ValueError) as e:
            logger.error('Mapped read of %s @%d returned an invalid value: %s', space.value, address, e)
            raise SlaveDeviceFailure() from e
        if len(values) != count:
            logger.error('Mapped read of %s @%d returned %d value(s), %d expected', space.value, address,
                         len(values), count)
            raise SlaveDeviceFailure()
        return values

    def write(self, space: Space, address: int, values: Iterable[Any]) -> None:
        values = space.check_values(values)
        if not values:
            return
        # checked first: nothing is written if a part of the range is invalid
        segments = self._store._segments(space, address, len(values))
        for mapping, _, _ in segments:
            if mapping is not None and mapping.write is None:
                raise IllegalDataAddress()
        memory = self._store._data[space]
        offset = 0
        for mapping, seg_address, seg_count in segments:
            seg_values = values[offset:offset + seg_count]
            offset += seg_count
            if mapping is not None:
                assert mapping.write is not None
                mapping.write(seg_address, seg_values, self.ctx)
                continue
            old_values = memory[seg_address:seg_address + seg_count]
            # compare the whole slice at C speed: skip everything if nothing changes
            if old_values == seg_values:
                continue
            self._journal.append((space, seg_address, old_values))
            for i, (old, new) in enumerate(zip(old_values, seg_values)):
                if old != new:
                    entry = self._changes.get((space, seg_address + i))
                    if entry is None:
                        self._changes[(space, seg_address + i)] = [old, new]
                    else:
                        entry[1] = new
            memory[seg_address:seg_address + seg_count] = seg_values

    def _rollback(self) -> None:
        for space, address, old_values in reversed(self._journal):
            self._store._data[space][address:address + len(old_values)] = old_values
        self._journal.clear()
        self._changes.clear()

    def _committed_changes(self) -> tuple[Change, ...]:
        # a value written back to its first value is not a change
        return tuple(Change(space, address, old, new) for (space, address), (old, new) in self._changes.items()
                     if old != new)


class MemoryStore(DataStore):
    """The default data store: the 4 Modbus data spaces in memory, thread-safe.

    Each space has size addresses (65536 by default), the size of a space can be set by its own argument. A range
    of addresses can be computed at each access instead (see map()), even beyond the size of its space.
    """

    def __init__(self, size: int = 0x10000, *, coils: int | None = None, discrete_inputs: int | None = None,
                 holding_registers: int | None = None, input_registers: int | None = None,
                 default_bit: bool = False, default_word: int = 0) -> None:
        """Constructor

        :param size: number of addresses of each space (default 65536, 0 for a store with mapped ranges only)
        :param coils: number of coils, if different from size
        :param discrete_inputs: number of discrete inputs, if different from size
        :param holding_registers: number of holding registers, if different from size
        :param input_registers: number of input registers, if different from size
        :param default_bit: startup value of the coils and the discrete inputs (default False)
        :param default_word: startup value of the registers (default 0)
        """
        sizes = {Space.COILS: coils, Space.DISCRETE_INPUTS: discrete_inputs,
                 Space.HOLDING_REGISTERS: holding_registers, Space.INPUT_REGISTERS: input_registers}
        default_word = Space.HOLDING_REGISTERS.check_values([default_word])[0]
        self._data: dict[Space, list[Any]] = {}
        for space, space_size in sizes.items():
            space_size = size if space_size is None else space_size
            if isinstance(space_size, bool) or not isinstance(space_size, int) or not 0 <= space_size <= 0x10000:
                raise ValueError(f'the size of {space.value} must be an int between 0 and 65536')
            self._data[space] = [bool(default_bit) if space.is_bit else default_word] * space_size
        self._maps: dict[Space, list[_Mapping]] = {space: [] for space in Space}
        self._lock = threading.Lock()
        # the transaction in progress in the current thread (a nested transaction joins it)
        self._local = threading.local()
        self._listeners: list[_ListenerEntry] = []
        self._listeners_lock = threading.Lock()
        self._notify_queue: queue.Queue[tuple[Listener, ChangeEvent]] | None = None

    def __repr__(self) -> str:
        sizes = ', '.join(f'{space.value}={len(data)}' for space, data in self._data.items())
        return f'MemoryStore({sizes})'

    def size(self, space: Space) -> int:
        """The number of addresses of a space in memory (the mapped ranges are not counted)."""
        return len(self._data[space])

    # transactions

    @contextmanager
    def transaction(self, ctx: RequestContext | None = None) -> Iterator[Transaction]:
        current: _MemoryTransaction | None = getattr(self._local, 'tx', None)
        if current is not None:
            # nested in the same thread (like a mapped function that uses the store): part of the same transaction
            yield current
            return
        with self._lock:
            tx = _MemoryTransaction(self, ctx)
            self._local.tx = tx
            try:
                yield tx
            except BaseException:
                tx._rollback()
                raise
            finally:
                self._local.tx = None
        # committed: notify the listeners, without the lock (they can use the store)
        changes = tx._committed_changes()
        if changes:
            self._notify(ChangeEvent(ctx, changes))

    def _segments(self, space: Space, address: int, count: int) -> list[tuple[_Mapping | None, int, int]]:
        """Split a range in memory and mapped parts: [(mapping or None, address, count), ...].

        :raises IllegalDataAddress: if a part of the range is neither in memory nor mapped
        """
        if address < 0 or count < 0 or address + count > 0x10000:
            raise IllegalDataAddress()
        memory_size = len(self._data[space])
        maps = self._maps[space]
        # usual case: no mapped range in this space
        if not maps:
            if address + count > memory_size:
                raise IllegalDataAddress()
            return [(None, address, count)]
        segments: list[tuple[_Mapping | None, int, int]] = []
        current, end = address, address + count
        while current < end:
            mapping = next((m for m in maps if m.start <= current < m.end), None)
            if mapping is not None:
                seg_end = min(end, mapping.end)
            else:
                if current >= memory_size:
                    raise IllegalDataAddress()
                # memory, until the end of the memory or the next mapped range
                next_start = min((m.start for m in maps if m.start > current), default=end)
                seg_end = min(end, memory_size, next_start)
            segments.append((mapping, current, seg_end - current))
            current = seg_end
        return segments

    # computed values

    def map(self, space: Space, address: int, count: int,
            read: Callable[[int, int, RequestContext | None], Sequence[Any]],
            write: Callable[[int, list[Any], RequestContext | None], None] | None = None) -> None:
        """Compute the values of a range of addresses at each access, instead of keeping them in memory.

        The functions run in the transaction (with the lock of the store): keep them fast. A mapped range can be
        beyond the size of its space (MemoryStore(size=0) for a store with mapped ranges only).

        :param space: the data space
        :param address: first address of the range
        :param count: number of addresses
        :param read: read(address, count, ctx) returns the count values from address (ctx is None for the
            application)
        :param write: write(address, values, ctx) for a writable range, None for a read-only range (a write gets an
            "illegal data address" exception). The store can't restore what this function did if the transaction
            fails afterwards: only the values in memory are restored.
        :raises ValueError: if the range overlaps another mapped range, or is out of 0 to 65535
        """
        if address < 0 or count < 1 or address + count > 0x10000:
            raise ValueError('a mapped range must be in the 0 to 65535 address range')
        new = _Mapping(address, count, read, write)
        with self._lock:
            if any(m.start < new.end and new.start < m.end for m in self._maps[space]):
                raise ValueError('a mapped range must not overlap another one')
            self._maps[space].append(new)
            self._maps[space].sort(key=lambda m: m.start)

    # notifications

    def subscribe(self, listener: Listener, app_writes: bool = False, threaded: bool = False) -> Callable[[], None]:
        entry = _ListenerEntry(listener, app_writes, threaded)
        with self._listeners_lock:
            self._listeners.append(entry)
            if threaded and self._notify_queue is None:
                self._notify_queue = queue.Queue()
                threading.Thread(target=self._notify_loop, args=(self._notify_queue,), daemon=True,
                                 name='MemoryStore notifications').start()

        def unsubscribe() -> None:
            with self._listeners_lock:
                if entry in self._listeners:
                    self._listeners.remove(entry)

        return unsubscribe

    def join_notifications(self) -> None:
        """Wait until the threaded listeners have processed every change event (useful in tests)."""
        notify_queue = self._notify_queue
        if notify_queue is not None:
            notify_queue.join()

    def _notify(self, event: ChangeEvent) -> None:
        with self._listeners_lock:
            entries = list(self._listeners)
        for entry in entries:
            if not event.by_client and not entry.app_writes:
                continue
            if entry.threaded and self._notify_queue is not None:
                self._notify_queue.put((entry.listener, event))
            else:
                _call_listener(entry.listener, event)

    @staticmethod
    def _notify_loop(notify_queue: queue.Queue[tuple[Listener, ChangeEvent]]) -> None:
        while True:
            listener, event = notify_queue.get()
            try:
                _call_listener(listener, event)
            finally:
                notify_queue.task_done()


def _call_listener(listener: Listener, event: ChangeEvent) -> None:
    """Call a listener: the transaction is committed, an error of the listener can't change it, it's logged."""
    try:
        listener(event)
    except Exception:  # noqa: BLE001
        logger.exception('Error in the change listener %r', listener)


# --- the access of the application ---

class SpaceAccess:
    """The access of the application to one data space: store.app.coils, store.app.holding_registers..."""

    def __init__(self, store: DataStore, space: Space) -> None:
        self.store = store
        self.space = space

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.space.value})'

    def read(self, address: int, count: int = 1) -> list[Any]:
        """Read count values from address.

        :raises IllegalDataAddress: if the range is out of the space
        """
        with self.store.transaction() as tx:
            return list(tx.read(self.space, address, count))

    def write(self, address: int, values: Iterable[Any]) -> None:
        """Write values from address (all or nothing).

        :raises IllegalDataAddress: if the range is out of the space
        :raises TypeError, ValueError: for an invalid value (a register is a 16 bits unsigned int)
        """
        with self.store.transaction() as tx:
            tx.write(self.space, address, values)


class RegistersAccess(SpaceAccess):
    """The access of the application to a space of registers, with typed values on 1, 2 or 4 registers.

    read_values() and write_values() take a :class:`~pyModbusTCP.codec.Codec` (any type and byte order). The
    shortcuts read_uint32(), write_float32()... take big_endian: True (default) for the ``ABCD`` order (the first
    register holds the most significant word), False for ``CDAB``.
    """

    def read_values(self, address: int, codec: Codec, count: int = 1) -> list[Number]:
        """Read count values of the type of codec (count * codec.words registers from address).

        :raises IllegalDataAddress: if a register is out of the space
        """
        return codec.decode(self.read(address, count * codec.words))

    def write_values(self, address: int, values: Sequence[Number], codec: Codec) -> None:
        """Write values of the type of codec from address (atomic: all the registers or nothing).

        :raises TypeError, ValueError: for a value that is not a value of the type (nothing is written)
        :raises IllegalDataAddress: if a register is out of the space
        """
        self.write(address, codec.encode(values))

    def read_uint32(self, address: int, big_endian: bool = True) -> int:
        """Read an unsigned 32 bits int on 2 registers."""
        return int(self.read_values(address, _codec(DataType.UINT32, big_endian))[0])

    def write_uint32(self, address: int, value: int, big_endian: bool = True) -> None:
        """Write an unsigned 32 bits int on 2 registers (ValueError out of 0 to 2**32 - 1)."""
        self.write_values(address, [value], _codec(DataType.UINT32, big_endian))

    def read_int32(self, address: int, big_endian: bool = True) -> int:
        """Read a signed 32 bits int (2's complement) on 2 registers."""
        return int(self.read_values(address, _codec(DataType.INT32, big_endian))[0])

    def write_int32(self, address: int, value: int, big_endian: bool = True) -> None:
        """Write a signed 32 bits int (2's complement) on 2 registers (ValueError out of -2**31 to 2**31 - 1)."""
        self.write_values(address, [value], _codec(DataType.INT32, big_endian))

    def read_float32(self, address: int, big_endian: bool = True) -> float:
        """Read an IEEE 754 single precision float on 2 registers."""
        return float(self.read_values(address, _codec(DataType.FLOAT32, big_endian))[0])

    def write_float32(self, address: int, value: float, big_endian: bool = True) -> None:
        """Write an IEEE 754 single precision float on 2 registers."""
        self.write_values(address, [value], _codec(DataType.FLOAT32, big_endian))

    def read_float64(self, address: int, big_endian: bool = True) -> float:
        """Read an IEEE 754 double precision float on 4 registers."""
        return float(self.read_values(address, _codec(DataType.FLOAT64, big_endian))[0])

    def write_float64(self, address: int, value: float, big_endian: bool = True) -> None:
        """Write an IEEE 754 double precision float on 4 registers."""
        self.write_values(address, [value], _codec(DataType.FLOAT64, big_endian))


def _codec(data_type: DataType, big_endian: bool) -> Codec:
    """The codec of the shortcuts of RegistersAccess: ABCD (big_endian) or CDAB."""
    return Codec(data_type, ByteOrder.ABCD if big_endian else ByteOrder.CDAB)


class AppAccess:
    """The access of the application to a store (store.app): no client context, the wrappers don't apply."""

    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.coils = SpaceAccess(store, Space.COILS)
        self.discrete_inputs = SpaceAccess(store, Space.DISCRETE_INPUTS)
        self.holding_registers = RegistersAccess(store, Space.HOLDING_REGISTERS)
        self.input_registers = RegistersAccess(store, Space.INPUT_REGISTERS)

    def transaction(self) -> Any:
        """An atomic transaction of the application, to change several values (of several spaces) at once."""
        return self.store.transaction(None)

    def read(self, space: Space, address: int, count: int = 1) -> list[Any]:
        """Read count values of a space from address."""
        return SpaceAccess(self.store, space).read(address, count)

    def write(self, space: Space, address: int, values: Iterable[Any]) -> None:
        """Write values in a space from address."""
        SpaceAccess(self.store, space).write(address, values)
