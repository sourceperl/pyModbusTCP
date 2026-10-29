""" Test of the data model of the server: stores, transactions, notifications, wrappers, function registry. """

import socket
import sys
import threading
import time
import unittest
from typing import Any
from unittest import mock

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.codec import ByteOrder
from pyModbusTCP.constants import (
    EXP_DATA_ADDRESS,
    EXP_GATEWAY_PATH_UNAVAILABLE,
    EXP_ILLEGAL_FUNCTION,
    EXP_SLAVE_DEVICE_FAILURE,
)
from pyModbusTCP.server import (
    STANDARD_FUNCTIONS,
    AccessControl,
    ChangeEvent,
    Guard,
    IllegalDataAddress,
    IllegalFunction,
    MemoryStore,
    ModbusException,
    ModbusServer,
    ReadOnly,
    Request,
    RequestContext,
    SlaveDeviceFailure,
    Space,
    StoreWrapper,
)
from pyModbusTCP.server.session import ClientInfo
from pyModbusTCP.server.store import _MemoryTransaction
from pyModbusTCP.utils import _to_bool_list

H = Space.HOLDING_REGISTERS
C = Space.COILS


def client_ctx(address: str = '127.0.0.1', function_code: int = 3, unit_id: int = 1) -> RequestContext:
    return RequestContext(client=ClientInfo(address, 50000), unit_id=unit_id, function_code=function_code)


class TestMemoryStore(unittest.TestCase):
    def test_sizes_and_defaults(self) -> None:
        store = MemoryStore(100, coils=10, default_bit=True, default_word=7)
        self.assertEqual(store.size(C), 10)
        self.assertEqual(store.size(H), 100)
        self.assertEqual(store.app.coils.read(9), [True])
        self.assertEqual(store.app.input_registers.read(99), [7])
        with self.assertRaises(IllegalDataAddress):
            store.app.coils.read(10)
        for bad in (-1, 0x10001, 1.5, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                MemoryStore(bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            MemoryStore(default_word=0x10000)
        self.assertEqual(repr(MemoryStore(0, holding_registers=2)),
                         'MemoryStore(coils=0, discrete_inputs=0, holding_registers=2, input_registers=0)')

    def test_read_write_all_spaces(self) -> None:
        store = MemoryStore(16)
        store.app.coils.write(1, [1, 0, True])
        store.app.discrete_inputs.write(5, [True])
        store.app.holding_registers.write(0, [0xffff, True, 1])
        store.app.input_registers.write(15, [9])
        self.assertEqual(store.app.coils.read(0, 4), [False, True, False, True])
        self.assertEqual(store.app.discrete_inputs.read(5), [True])
        self.assertEqual(store.app.holding_registers.read(0, 3), [0xffff, 1, 1])
        self.assertEqual(store.app.read(Space.INPUT_REGISTERS, 15), [9])
        # an empty write is a no-op, even at the end of the space
        store.app.write(H, 16, [])

    def test_invalid_values_and_ranges(self) -> None:
        store = MemoryStore(16)
        for bad_values, exc in (([-1], ValueError), ([0x10000], ValueError), ([1.0], TypeError), (['5'], TypeError),
                                ([None], TypeError)):
            with self.subTest(bad_values), self.assertRaises(exc):
                store.app.holding_registers.write(0, bad_values)
        for address, values in ((15, [1, 2]), (-1, [1]), (16, [1])):
            with self.subTest(address=address), self.assertRaises(IllegalDataAddress):
                store.app.holding_registers.write(address, values)
        # nothing is written, even partially
        self.assertEqual(store.app.holding_registers.read(0, 16), [0] * 16)

    def test_int_like_and_bool_like_values(self) -> None:
        class IntLike:
            def __init__(self, value: int) -> None:
                self.value = value

            def __index__(self) -> int:
                return self.value

        store = MemoryStore(16)
        store.app.coils.write(0, [IntLike(1), IntLike(0), True])
        self.assertEqual(store.app.coils.read(0, 3), [True, False, True])
        store.app.holding_registers.write(0, (IntLike(1), IntLike(65535)))
        self.assertEqual(store.app.holding_registers.read(0, 2), [1, 65535])
        self.assertEqual(_to_bool_list(iter([1, 0, 1]), 'x'), [True, False, True])
        with self.assertRaises(TypeError):
            _to_bool_list([True, 'a'], 'x')


class TestTransactions(unittest.TestCase):
    def test_rollback_on_exception(self) -> None:
        """Every value written by a failed transaction is restored, in every space."""
        store = MemoryStore(16)
        store.app.holding_registers.write(0, [1, 2])
        with self.assertRaises(IllegalDataAddress), store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [10, 20])
            tx.write(C, 3, [True])
            tx.write(H, 1, [30])
            tx.read(H, 15, 2)
        self.assertEqual(store.app.holding_registers.read(0, 2), [1, 2])
        self.assertEqual(store.app.coils.read(3), [False])
        with self.assertRaises(RuntimeError), store.app.transaction() as tx:
            tx.write(H, 0, [5])
            raise RuntimeError('application error')
        self.assertEqual(store.app.holding_registers.read(0), [1])

    def test_write_then_read(self) -> None:
        store = MemoryStore(16)
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 1, [7, 8])
            self.assertEqual(tx.read(H, 0, 4), [0, 7, 8, 0])

    def test_nested_transaction_joins(self) -> None:
        """A transaction opened in a transaction of the same thread is part of it (no deadlock, one commit)."""
        store = MemoryStore(16)
        events: list[ChangeEvent] = []
        store.subscribe(events.append, app_writes=True)
        with store.app.transaction() as tx:
            tx.write(H, 0, [1])
            store.app.holding_registers.write(1, [2])
            self.assertEqual(events, [], 'nothing is notified before the commit')
        self.assertEqual(len(events), 1)
        self.assertEqual([(c.address, c.new) for c in events[0].changes], [(0, 1), (1, 2)])

    def test_atomic_with_concurrent_writes(self) -> None:
        """Another thread writes the same registers in a loop: a write then read always returns its own write."""
        store = MemoryStore(16)
        stop = threading.Event()

        def writer() -> None:
            while not stop.is_set():
                store.app.holding_registers.write(0, [1] * 10)

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(20000):
                with store.transaction(client_ctx(function_code=0x17)) as tx:
                    tx.write(H, 0, [2] * 10)
                    self.assertEqual(tx.read(H, 0, 10), [2] * 10)
        finally:
            sys.setswitchinterval(previous_interval)
            stop.set()
            thread.join(5.0)


class TestNotifications(unittest.TestCase):
    def test_one_event_per_transaction(self) -> None:
        store = MemoryStore(16)
        events: list[ChangeEvent] = []
        store.subscribe(events.append)
        ctx = client_ctx(function_code=0x10)
        with store.transaction(ctx) as tx:
            tx.write(H, 0, [1, 2, 3])
            # written twice: one change, first old value and last new value
            tx.write(H, 0, [4])
            # written back to its value: no change
            tx.write(H, 2, [0])
            tx.write(C, 1, [True])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIs(event.ctx, ctx)
        self.assertTrue(event.by_client)
        self.assertEqual([(c.address, c.old, c.new) for c in event.of(H)], [(0, 0, 4), (1, 0, 2)])
        self.assertEqual([(c.address, c.new) for c in event.of(C)], [(1, True)])
        # same values: no event
        with store.transaction(ctx) as tx:
            tx.write(H, 0, [4, 2])
        self.assertEqual(len(events), 1)

    def test_event_keeps_the_written_ranges(self) -> None:
        """An event gives the ranges written (writes, old and new values of each), the changes are computed from them
        only when read; ranges that cancel out (a value written back) give no event."""
        store = MemoryStore(16)
        events: list[ChangeEvent] = []
        store.subscribe(events.append)
        ctx = client_ctx(function_code=0x10)
        with store.transaction(ctx) as tx:
            tx.write(H, 2, [5, 0, 6])
        self.assertEqual(len(events), 1)
        (written,) = events[0].writes
        self.assertEqual((written.space, written.address, written.old, written.new), (H, 2, (0, 0, 0), (5, 0, 6)))
        self.assertEqual(written.count_changed(), 2)
        self.assertEqual(events[0].size, 3)
        self.assertEqual([(c.address, c.old, c.new) for c in events[0].changes], [(2, 0, 5), (4, 0, 6)])
        # 2 ranges that cancel out: no event
        with store.transaction(ctx) as tx:
            tx.write(H, 2, [9])
            tx.write(H, 2, [5])
        self.assertEqual(len(events), 1)

    def test_no_listener_no_event(self) -> None:
        """Without a listener, a transaction builds no event (no cost for the changes nobody reads)."""
        store = MemoryStore(16)
        with mock.patch.object(_MemoryTransaction, '_committed_event') as committed_event:
            store.app.holding_registers.write(0, [1, 2])
            committed_event.assert_not_called()
            unsubscribe = store.subscribe(lambda event: None, app_writes=True)
            store.app.holding_registers.write(0, [3])
            committed_event.assert_called_once()
            unsubscribe()
            store.app.holding_registers.write(0, [4])
            committed_event.assert_called_once()
        self.assertEqual(store.app.holding_registers.read(0, 2), [4, 2])

    def test_app_writes_on_demand(self) -> None:
        store = MemoryStore(16)
        clients: list[ChangeEvent] = []
        everything: list[ChangeEvent] = []
        store.subscribe(clients.append)
        store.subscribe(everything.append, app_writes=True)
        store.app.input_registers.write(0, [1])
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [1])
        self.assertEqual(len(clients), 1)
        self.assertEqual([e.by_client for e in everything], [False, True])
        self.assertIsNone(everything[0].ctx)

    def test_threaded_listener_and_unsubscribe(self) -> None:
        store = MemoryStore(16)
        threads: list[str] = []
        unsubscribe = store.subscribe(lambda event: threads.append(threading.current_thread().name), threaded=True)
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [1])
        store.join_notifications()
        self.assertEqual(threads, ['MemoryStore notifications'])
        unsubscribe()
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [2])
        store.join_notifications()
        self.assertEqual(len(threads), 1)

    def test_listener_can_use_the_store(self) -> None:
        """A listener runs after the commit, without the lock: it can answer a command (no deadlock)."""
        store = MemoryStore(16)

        def acknowledge(event: ChangeEvent) -> None:
            for change in event.of(H):
                if change.address == 0:
                    store.app.holding_registers.write(1, [change.new + 1])

        store.subscribe(acknowledge)
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [41])
            # the read of the same transaction doesn't see the acknowledge: it comes after the commit
            self.assertEqual(tx.read(H, 1), [0])
        self.assertEqual(store.app.holding_registers.read(0, 2), [41, 42])

    def test_listener_errors_are_logged(self) -> None:
        store = MemoryStore(16)

        def broken(event: ChangeEvent) -> None:
            raise RuntimeError('boom')

        store.subscribe(broken)
        with self.assertLogs('pyModbusTCP.server', level='ERROR'), store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [1])
        # the transaction is committed anyway
        self.assertEqual(store.app.holding_registers.read(0), [1])

    def test_threaded_listener_never_ends_the_notification_thread(self) -> None:
        """A threaded listener that raises SystemExit: logged, the other threaded listeners still get every event.

        The notification thread ended: no event was delivered after it, to any threaded listener, and
        join_notifications() never returned.
        """
        store = MemoryStore(16)
        received: list[int] = []

        def exits(event: ChangeEvent) -> None:
            sys.exit(1)

        store.subscribe(exits, app_writes=True, threaded=True)
        store.subscribe(lambda event: received.append(event.changes[0].new), app_writes=True, threaded=True)
        with self.assertLogs('pyModbusTCP.server', level='ERROR') as logs:
            for value in (1, 2):
                store.app.holding_registers.write(0, [value])
            # in a thread: never a hang of the test suite, whatever the result
            joined = threading.Thread(target=store.join_notifications, daemon=True)
            joined.start()
            joined.join(5.0)
        self.assertFalse(joined.is_alive(), 'join_notifications() never returns')
        self.assertEqual(received, [1, 2])
        self.assertIn('SystemExit', '\n'.join(logs.output))

    def test_threaded_listener_gets_the_events_in_the_order_of_the_commits(self) -> None:
        """Two concurrent writes: a threaded listener gets their events in the order of the commits.

        The events were queued after the lock of the store: the thread of the first commit, held by a slow listener
        called before (here a listener of its own thread), queued its event after the one of a later commit.
        """
        store = MemoryStore(16)
        slow_started = threading.Event()

        def slow(event: ChangeEvent) -> None:
            if event.changes[0].new == 1:
                slow_started.set()
                time.sleep(0.3)

        received: list[tuple[int, int]] = []
        # subscribed first: called before the threaded one was queued (before the fix)
        store.subscribe(slow, app_writes=True)
        store.subscribe(lambda event: received.extend((c.old, c.new) for c in event.changes), app_writes=True,
                        threaded=True)
        first = threading.Thread(target=store.app.holding_registers.write, args=(0, [1]))
        first.start()
        self.assertTrue(slow_started.wait(5.0))
        # committed after the first one, while the listener of the first one runs
        store.app.holding_registers.write(0, [2])
        first.join(5.0)
        store.join_notifications()
        self.assertEqual(received, [(0, 1), (1, 2)])
        self.assertEqual(store.app.holding_registers.read(0), [2])


class TestMappedRanges(unittest.TestCase):
    def test_read_only_mapping(self) -> None:
        store = MemoryStore(0)
        calls: list[tuple[int, int, Any]] = []

        def read(address: int, count: int, ctx: RequestContext | None) -> list[int]:
            calls.append((address, count, ctx))
            return list(range(address, address + count))

        store.map(Space.INPUT_REGISTERS, 100, 10, read)
        self.assertEqual(store.app.input_registers.read(102, 3), [102, 103, 104])
        ctx = client_ctx()
        with store.transaction(ctx) as tx:
            tx.read(Space.INPUT_REGISTERS, 100)
        self.assertEqual(calls, [(102, 3, None), (100, 1, ctx)])
        for address, count in ((99, 1), (109, 2)):
            with self.subTest(address=address), self.assertRaises(IllegalDataAddress):
                store.app.input_registers.read(address, count)
        with self.assertRaises(IllegalDataAddress):
            store.app.input_registers.write(100, [1])

    def test_mapping_and_memory_in_one_range(self) -> None:
        store = MemoryStore(16)
        written: list[tuple[int, list[Any]]] = []
        store.map(H, 4, 2, read=lambda address, count, ctx: [0xAA] * count,
                  write=lambda address, values, ctx: written.append((address, values)))
        store.app.holding_registers.write(2, [1, 2, 3, 4, 5, 6])
        self.assertEqual(written, [(4, [3, 4])])
        self.assertEqual(store.app.holding_registers.read(2, 6), [1, 2, 0xAA, 0xAA, 5, 6])

    def test_invalid_mappings(self) -> None:
        store = MemoryStore(16)
        store.map(H, 10, 5, read=lambda address, count, ctx: [0] * count)
        for address, count in ((12, 5), (8, 3), (0xFFFF, 2), (-1, 1), (0, 0)):
            with self.subTest(address=address, count=count), self.assertRaises(ValueError):
                store.map(H, address, count, read=lambda address, count, ctx: [0] * count)

    def test_bad_values_from_a_mapping(self) -> None:
        store = MemoryStore(0)
        store.map(H, 0, 2, read=lambda address, count, ctx: [1])
        store.map(H, 10, 1, read=lambda address, count, ctx: [0x10000])
        store.map(C, 0, 1, read=lambda address, count, ctx: ['on'])
        for space, address, count in ((H, 0, 2), (H, 10, 1), (C, 0, 1)):
            with (self.subTest(address=address), self.assertLogs('pyModbusTCP.server', level='ERROR'),
                  self.assertRaises(SlaveDeviceFailure)):
                store.app.read(space, address, count)


class TestTypedAccess(unittest.TestCase):
    def test_round_trips(self) -> None:
        regs = MemoryStore(16).app.holding_registers
        regs.write_float32(0, 3.5)
        self.assertEqual(regs.read(0, 2), [0x4060, 0x0000])
        self.assertEqual(regs.read_float32(0), 3.5)
        regs.write_float32(2, -1.25, ByteOrder.CDAB)
        self.assertEqual(regs.read(2, 2), [0x0000, 0xBFA0])
        self.assertEqual(regs.read_float32(2, ByteOrder.CDAB), -1.25)
        # every byte order, the old bool argument is refused
        regs.write_uint32(12, 0x11223344, byte_order=ByteOrder.BADC)
        self.assertEqual(regs.read(12, 2), [0x2211, 0x4433])
        self.assertEqual(regs.read_uint32(12, ByteOrder.BADC), 0x11223344)
        self.assertEqual(regs.read_uint32(12, ByteOrder.DCBA), 0x33441122)
        with self.assertRaises(TypeError):
            regs.read_uint32(12, False)  # type: ignore[arg-type]

    def test_16_and_64_bits(self) -> None:
        regs = MemoryStore(16).app.holding_registers
        # uint16 and int16: one register, no byte order
        regs.write_uint16(1, 0xFFFB)
        self.assertEqual(regs.read_uint16(1), 0xFFFB)
        self.assertEqual(regs.read_int16(1), -5)
        for value in (65536, -1):
            with self.assertRaises(ValueError):
                regs.write_uint16(1, value)
        with self.assertRaises(TypeError):
            regs.write_uint16(1, 1.5)  # type: ignore[arg-type]
        regs.write_int16(0, -5)
        self.assertEqual(regs.read(0), [0xFFFB])
        self.assertEqual(regs.read_int16(0), -5)
        for value in (32768, -32769):
            with self.assertRaises(ValueError):
                regs.write_int16(0, value)
        # 64 bits: 4 registers, in a byte order
        regs.write_uint64(4, 0x0102030405060708)
        self.assertEqual(regs.read(4, 4), [0x0102, 0x0304, 0x0506, 0x0708])
        self.assertEqual(regs.read_uint64(4), 0x0102030405060708)
        regs.write_int64(8, -2, ByteOrder.CDAB)
        self.assertEqual(regs.read(8, 4), [0xFFFE, 0xFFFF, 0xFFFF, 0xFFFF])
        self.assertEqual(regs.read_int64(8, ByteOrder.CDAB), -2)
        self.assertEqual(regs.read_uint64(8, ByteOrder.CDAB), 2 ** 64 - 2)
        for method, value in ((regs.write_uint64, -1), (regs.write_int64, 2 ** 63)):
            with self.assertRaises(ValueError):
                method(0, value)
        with self.assertRaises(IllegalDataAddress):
            regs.read_uint64(13)
        regs.write_int32(4, -2)
        self.assertEqual(regs.read(4, 2), [0xFFFF, 0xFFFE])
        self.assertEqual(regs.read_int32(4), -2)
        self.assertEqual(regs.read_uint32(4), 0xFFFFFFFE)
        regs.write_uint32(6, 0x12345678)
        self.assertEqual(regs.read(6, 2), [0x1234, 0x5678])
        regs.write_float64(8, 1e300)
        self.assertEqual(regs.read_float64(8), 1e300)
        for method32, value in ((regs.write_int32, 2 ** 31), (regs.write_uint32, -1)):
            with self.assertRaises(ValueError):
                method32(0, value)
        with self.assertRaises(IllegalDataAddress):
            regs.read_float32(15)


class TestWrappers(unittest.TestCase):
    def test_read_only(self) -> None:
        store = ReadOnly(MemoryStore(16), spaces=[C])
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [1])
            with self.assertRaises(IllegalFunction):
                tx.write(C, 0, [True])
        # the application is never restricted, and the extras of the store are available on the wrapper
        store.app.coils.write(0, [True])
        self.assertEqual(store.app.coils.read(0), [True])
        self.assertEqual(store.size(C), 16)
        self.assertEqual(repr(store), 'ReadOnly(MemoryStore(coils=16, discrete_inputs=16, holding_registers=16, '
                                      'input_registers=16))')

    def test_access_control(self) -> None:
        store = AccessControl(MemoryStore(16), read_hosts=['10.0.0.0/8', '127.0.0.1'], write_hosts=['127.0.0.1'])
        with store.transaction(client_ctx('127.0.0.1')) as tx:
            tx.write(H, 0, [1])
            self.assertEqual(tx.read(H, 0), [1])
        with store.transaction(client_ctx('10.1.2.3')) as tx:
            self.assertEqual(tx.read(H, 0), [1])
            with self.assertRaises(IllegalFunction):
                tx.write(H, 0, [2])
        with store.transaction(client_ctx('192.168.1.1')) as tx, self.assertRaises(IllegalFunction):
            tx.read(H, 0)
        with self.assertRaises(ValueError) as cm:
            AccessControl(MemoryStore(1), read_hosts=['not an address'])
        self.assertIn('read_hosts', str(cm.exception))

    def test_access_control_empty_lists(self) -> None:
        """None: every client, an empty list: no client at all (like every allow list)."""
        store = AccessControl(MemoryStore(16), write_hosts=[])
        with store.transaction(client_ctx('127.0.0.1')) as tx:
            self.assertEqual(tx.read(H, 0), [0])
            with self.assertRaises(IllegalFunction):
                tx.write(H, 0, [1])
        store = AccessControl(MemoryStore(16), read_hosts=[], write_hosts=None)
        with store.transaction(client_ctx('127.0.0.1')) as tx:
            tx.write(H, 0, [1])
            with self.assertRaises(IllegalFunction):
                tx.read(H, 0)
        # the application is never restricted
        self.assertEqual(store.app.holding_registers.read(0), [1])

    def test_guard_and_stacking(self) -> None:
        def no_high_addresses(ctx: RequestContext, access: str, space: Space, address: int, count: int) -> bool:
            if space is C and access == 'write':
                raise ModbusException(code=0x06)
            return address + count <= 8

        store = ReadOnly(Guard(MemoryStore(16), no_high_addresses), spaces=[Space.INPUT_REGISTERS])
        with store.transaction(client_ctx()) as tx:
            tx.write(H, 0, [1])
            with self.assertRaises(IllegalDataAddress):
                tx.read(H, 7, 2)
            with self.assertRaises(ModbusException) as cm:
                tx.write(C, 0, [True])
            self.assertEqual(cm.exception.code, 6)
        with self.assertRaises(TypeError):
            StoreWrapper('not a store')  # type: ignore[arg-type]

    def test_guard_fails_closed(self) -> None:
        """Only True allows an access: a check that returns None (a forgotten return), 0, 1 or a string refuses it
        ("illegal data address"), nothing is written."""
        results: list[Any] = [None, 0, 1, '', 'yes', [True]]
        for result in results:
            with self.subTest(result=result):
                def check(ctx: RequestContext, access: str, space: Space, address: int, count: int,
                          r: Any = result) -> Any:
                    return r

                store = Guard(MemoryStore(16), check)
                with self.assertRaises(IllegalDataAddress), store.transaction(client_ctx()) as tx:
                    tx.write(H, 0, [1])
                self.assertEqual(store.app.holding_registers.read(0), [0])

    def test_guard_forgotten_return_on_a_server(self) -> None:
        """S4 of the audit: a check meant to allow the writes of the registers 0 to 9 only, without a return for the
        other accesses, refuses them (it allowed everything)."""
        def check(ctx: RequestContext, access: str, space: Space, address: int, count: int) -> bool:
            if access == 'write' and space is H and address + count <= 10:
                return True
            return None  # type: ignore[return-value]

        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, store=Guard(MemoryStore(1000), check))
        port = server.start()[1]
        self.addCleanup(server.stop)
        c = ModbusClient(host='127.0.0.1', port=port, timeout=5.0)
        self.addCleanup(c.close)
        self.assertTrue(c.write_single_register(5, 1))
        self.assertFalse(c.write_single_register(500, 1))
        self.assertEqual(c.last_except, EXP_DATA_ADDRESS)
        self.assertFalse(c.write_single_coil(0, True))
        self.assertEqual(server.store.app.holding_registers.read(500), [0])
        self.assertEqual(server.store.app.coils.read(0), [False])

    def test_modbus_exception_codes(self) -> None:
        self.assertEqual(IllegalDataAddress().code, EXP_DATA_ADDRESS)
        self.assertEqual(str(IllegalDataAddress()), 'illegal data address')
        self.assertEqual(ModbusException(code=0x42).code, 0x42)
        for bad in (0, 256, True):
            with self.assertRaises(ValueError):
                ModbusException(code=bad)

    def test_modbus_exception_subclass_code(self) -> None:
        """A subclass with an invalid code is refused at its definition (it closed the session of the client)."""
        class Custom(ModbusException):
            code = 0x80

        self.assertEqual(Custom().code, 0x80)
        bad_codes: tuple[Any, ...] = (0, 300, -1, True, '1', None)
        for bad in bad_codes:
            with self.subTest(code=bad), self.assertRaisesRegex(ValueError, 'the code of Bad must be an int'):
                type('Bad', (ModbusException,), {'code': bad})


class ServerTestCase(unittest.TestCase):
    def start(self, **kwargs: Any) -> ModbusServer:
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **kwargs)
        self.port = server.start()[1]
        self.addCleanup(server.stop)
        return server

    def client(self, unit_id: int = 1) -> ModbusClient:
        client = ModbusClient(host='127.0.0.1', port=self.port, unit_id=unit_id, timeout=5.0)
        self.addCleanup(client.close)
        return client

    def exchange(self, pdu_hex: str, unit_id: int = 1) -> str:
        pdu = bytes.fromhex(pdu_hex)
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
            sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + bytes([unit_id]) + pdu)
            data = b''
            while len(data) < 7 or len(data) < 6 + int.from_bytes(data[4:6], 'big'):
                chunk = sock.recv(512)
                self.assertTrue(chunk)
                data += chunk
            return data[7:].hex()


class TestServerWithStores(ServerTestCase):
    def test_write_read_0x17_is_atomic(self) -> None:
        """Clients write the registers in a loop while another one uses 0x17: it always reads its own write."""
        server = self.start(store=MemoryStore(16))
        stop = threading.Event()

        def writer() -> None:
            client = ModbusClient(host='127.0.0.1', port=self.port, timeout=5.0)
            while not stop.is_set():
                client.write_multiple_registers(0, [1] * 10)
            client.close()

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        client = self.client()
        try:
            for _ in range(2000):
                self.assertEqual(client.write_read_multiple_registers(0, [2] * 10, 0, 10), [2] * 10)
        finally:
            sys.setswitchinterval(previous_interval)
            stop.set()
            thread.join(5.0)
        self.assertIsNotNone(server.store)

    def test_0x17_invalid_read_cancels_the_write(self) -> None:
        store = MemoryStore(16)
        events: list[ChangeEvent] = []
        store.subscribe(events.append)
        self.start(store=store)
        self.assertEqual(self.exchange('17 000f 0002 0005 0001 02 1234'), '9702')
        self.assertEqual(store.app.holding_registers.read(5), [0])
        self.assertEqual(events, [])

    def test_wrapped_store_on_a_server(self) -> None:
        self.start(store=AccessControl(MemoryStore(16), write_hosts=['10.0.0.1']))
        client = self.client()
        self.assertEqual(client.read_holding_registers(0), [0])
        self.assertFalse(client.write_single_register(0, 1))
        self.assertEqual(client.last_except, EXP_ILLEGAL_FUNCTION)

    def test_one_store_per_unit_id(self) -> None:
        stores = {1: MemoryStore(16), 2: MemoryStore(16)}
        server = self.start(store=stores)
        self.assertTrue(self.client(1).write_single_register(0, 11))
        self.assertTrue(self.client(2).write_single_register(0, 22))
        self.assertEqual(stores[1].app.holding_registers.read(0), [11])
        self.assertEqual(stores[2].app.holding_registers.read(0), [22])
        client = self.client(3)
        self.assertIsNone(client.read_holding_registers(0))
        self.assertEqual(client.last_except, EXP_GATEWAY_PATH_UNAVAILABLE)
        self.assertEqual(server.stores, stores)
        with self.assertRaises(AttributeError):
            server.store  # noqa: B018
        for bad, exc in (({}, ValueError), ({256: MemoryStore(1)}, ValueError), ({1: 'x'}, TypeError),
                         ('store', TypeError)):
            with self.subTest(bad=bad), self.assertRaises(exc):
                ModbusServer(store=bad)  # type: ignore[arg-type]


class TestAddressRanges(ServerTestCase):
    """A space with its own range of addresses: MemoryStore(0, holding_registers=range(40_000, 41_000))."""

    def test_store_with_a_range(self) -> None:
        store = MemoryStore(0, holding_registers=range(40_000, 41_000), coils=range(10, 20), default_word=7)
        self.assertEqual(store.addresses(H), range(40_000, 41_000))
        self.assertEqual(store.size(H), 1000)
        self.assertEqual(store.addresses(Space.INPUT_REGISTERS), range(0))
        self.assertEqual(repr(store), 'MemoryStore(coils=range(10, 20), discrete_inputs=0, '
                                      'holding_registers=range(40000, 41000), input_registers=0)')
        regs = store.app.holding_registers
        regs.write(40_998, [1, 2])
        self.assertEqual(regs.read(40_997, 3), [7, 1, 2])
        self.assertEqual(regs.read(40_000), [7])
        regs.write_float32(40_500, 1.5)
        self.assertEqual(regs.read_float32(40_500), 1.5)
        for address, count in ((39_999, 1), (39_999, 2), (40_999, 2), (41_000, 1), (0, 1)):
            with self.subTest(address=address, count=count), self.assertRaises(IllegalDataAddress):
                regs.read(address, count)
        with self.assertRaises(IllegalDataAddress):
            regs.write(40_999, [1, 2])
        self.assertEqual(regs.read(40_999), [2], 'nothing written by a refused write')
        self.assertEqual(store.app.coils.read(10, 10), [False] * 10)
        with self.assertRaises(IllegalDataAddress):
            store.app.coils.read(9)

    def test_rollback_and_events_use_the_modbus_addresses(self) -> None:
        store = MemoryStore(0, holding_registers=range(40_000, 41_000))
        events: list[ChangeEvent] = []
        store.subscribe(events.append, app_writes=True)
        with self.assertRaises(IllegalDataAddress), store.app.transaction() as tx:
            tx.write(H, 40_010, [1, 2])
            tx.read(H, 41_000)
        self.assertEqual(store.app.holding_registers.read(40_010, 2), [0, 0], 'the write is cancelled')
        self.assertEqual(events, [])
        store.app.holding_registers.write(40_010, [3])
        self.assertEqual([(c.address, c.old, c.new) for c in events[0].changes], [(40_010, 0, 3)])
        self.assertEqual(events[0].writes[0].address, 40_010)

    def test_mapped_range_next_to_a_range(self) -> None:
        store = MemoryStore(0, holding_registers=range(40_000, 40_010))
        store.map(H, 40_010, 2, read=lambda address, count, ctx: list(range(address, address + count)))
        self.assertEqual(store.app.holding_registers.read(40_008, 4), [0, 0, 40_010, 40_011])
        with self.assertRaises(IllegalDataAddress):
            store.app.holding_registers.read(39_999, 2)

    def test_server_with_a_range(self) -> None:
        store = MemoryStore(0, holding_registers=range(40_000, 41_000))
        self.start(store=store)
        c = self.client()
        self.assertTrue(c.write_multiple_registers(40_000, [1, 2, 3]))
        self.assertEqual(c.read_holding_registers(40_000, 3), [1, 2, 3])
        self.assertEqual(c.write_read_multiple_registers(40_999, [9], 40_998, 2), [0, 9])
        for request in (lambda: c.read_holding_registers(39_999, 2), lambda: c.read_holding_registers(41_000),
                        lambda: c.read_holding_registers(0), lambda: c.read_coils(40_000),
                        lambda: c.write_single_register(41_000, 1)):
            self.assertIn(request(), (None, False))
            self.assertEqual(c.last_except, EXP_DATA_ADDRESS)

    def test_invalid_ranges(self) -> None:
        for kwargs in ({'holding_registers': range(-1, 10)}, {'holding_registers': range(0x10001)},
                       {'coils': range(0, 10, 2)}, {'coils': range(10, 5)}, {'coils': 0x10001}, {'coils': True},
                       {'coils': '100'}, {'size': range(10)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MemoryStore(**kwargs)


class TestInvalidExceptionCode(ServerTestCase):
    def test_code_changed_after_the_checks(self) -> None:
        """A handler that raises an exception whose code was changed to an invalid one: "slave device failure",
        logged, and the session goes on (S9 of the audit: the session was closed)."""
        server = self.start()

        @server.function(0x41)
        def broken(req: Request) -> bytes:
            e = ModbusException()
            e.code = 300
            raise e

        c = self.client()
        with self.assertLogs('pyModbusTCP.server', level='ERROR') as logs:
            self.assertIsNone(c.custom_request(b'\x41'))
        self.assertEqual(c.last_except, EXP_SLAVE_DEVICE_FAILURE)
        self.assertIn('Invalid Modbus exception code', logs.output[0])
        self.assertTrue(c.is_open)
        self.assertEqual(c.read_holding_registers(0), [0])


class TestFunctionRegistry(ServerTestCase):
    def test_add_replace_and_remove_functions(self) -> None:
        server = self.start(store=MemoryStore(16))

        @server.function(0x41)
        def read_two_registers_sum(req: Request) -> bytes:
            (address,) = req.unpack('>H', 1, 3)
            with req.transaction() as tx:
                values = tx.read(Space.HOLDING_REGISTERS, address, 2)
            return req.reply('>I', sum(values))

        server.store.app.holding_registers.write(0, [0xFFFF, 2])
        self.assertEqual(self.exchange('41 0000'), '4100010001')
        # a short request: illegal data value, an address out of the space: illegal data address
        self.assertEqual(self.exchange('41 00'), 'c103')
        self.assertEqual(self.exchange('41 000f'), 'c102')

        # replace a standard function: a write single register that refuses the value 0
        @server.function(0x06)
        def no_zero(req: Request) -> bytes:
            if req.pdu[3:5] == b'\x00\x00':
                raise ModbusException('zero refused', code=0x03)
            return STANDARD_FUNCTIONS[0x06](req)

        self.assertEqual(self.exchange('06 0001 0000'), '8603')
        self.assertEqual(self.exchange('06 0001 0005'), '0600010005')
        del server.functions[0x06]
        self.assertEqual(self.exchange('06 0001 0005'), '8601')
        for bad in (0, 0x80, '1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server.function(bad)  # type: ignore[arg-type]

    def test_fallback(self) -> None:
        """The fallback gets the requests without handler, with their unit id and client."""
        seen: list[tuple[int, int, str]] = []

        def fallback(req: Request) -> bytes:
            seen.append((req.function_code, req.ctx.unit_id, req.ctx.client.address))
            return req.pdu

        self.start(store=MemoryStore(16), fallback=fallback)
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')
        self.assertEqual(self.exchange('64 0102', unit_id=7), '640102')
        self.assertEqual(seen, [(0x64, 7, '127.0.0.1')])

    def test_constructor_checks(self) -> None:
        self.assertEqual(sorted(ModbusServer().functions), sorted(STANDARD_FUNCTIONS))
        self.assertEqual(ModbusServer(functions={}).functions, {})
        # bad arguments typed Any: they are deliberately not of the annotated type
        cases: tuple[tuple[dict[str, Any], type[Exception]], ...] = (
            ({'functions': {0x41: 'x'}}, TypeError), ({'functions': {0x100: len}}, ValueError),
            ({'functions': [1]}, TypeError), ({'fallback': 'x'}, TypeError))
        for kwargs, exc in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(exc):
                ModbusServer(**kwargs)


class TestNotifyQueueBound(unittest.TestCase):
    def test_slow_threaded_listener(self) -> None:
        """The events over notify_queue_size are dropped and logged, the writes are never blocked."""
        store = MemoryStore(16)
        store.notify_queue_size = 3
        started, release = threading.Event(), threading.Event()
        seen: list[int] = []

        def slow(event: ChangeEvent) -> None:
            started.set()
            release.wait(5.0)
            seen.append(event.changes[0].new)

        store.subscribe(slow, app_writes=True, threaded=True)
        store.app.holding_registers.write(0, [1])
        self.assertTrue(started.wait(5.0))
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            for value in range(2, 11):
                store.app.holding_registers.write(0, [value])
        # 1 event in the listener, 3 in the queue, the 6 others dropped (one log message)
        self.assertEqual(len(logs.output), 1)
        self.assertIn('change event(s) dropped (queue full: 3 events or 200000 values at most)', logs.output[0])
        release.set()
        store.join_notifications()
        self.assertEqual(len(seen), 4)
        self.assertEqual(seen[0], 1)
        # the store is still usable, the next events are delivered
        store.app.holding_registers.write(0, [42])
        store.join_notifications()
        self.assertEqual(seen[-1], 42)

    def test_slow_threaded_listener_bounded_in_changes(self) -> None:
        """The values waiting for the threaded listeners are bounded too (notify_queue_values): a few events of
        many values each (a write of 1968 coils) can't fill the memory before the bound in events."""
        store = MemoryStore(4000)
        store.notify_queue_values = 2000
        started, release = threading.Event(), threading.Event()
        seen: list[int] = []

        def slow(event: ChangeEvent) -> None:
            started.set()
            release.wait(5.0)
            seen.append(len(event.changes))

        store.subscribe(slow, app_writes=True, threaded=True)
        # an event larger than the bound alone is queued when nothing waits: it is in the listener
        store.app.coils.write(0, [True] * 2500)
        self.assertTrue(started.wait(5.0))
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            # 2500 changes still in the listener: these events wait in vain
            store.app.coils.write(0, [False] * 1000)
            store.app.holding_registers.write(0, [1])
        # the first drop logged at once, the second one counted for the next message (one per 10 s at most)
        self.assertEqual(len(logs.output), 1)
        self.assertIn('queue full: 10000 events or 2000 values at most', logs.output[0])
        release.set()
        store.join_notifications()
        self.assertEqual(seen, [2500])
        self.assertEqual(store._queued_values, 0)
        # under the bound (the event in the listener counts until its end): queued, then delivered in order
        release.clear()
        started.clear()
        store.app.coils.write(3000, [True] * 900)
        self.assertTrue(started.wait(5.0))
        store.app.coils.write(3000, [False] * 900)
        store.app.holding_registers.write(0, [2])
        self.assertEqual(store._queued_values, 1801)
        release.set()
        store.join_notifications()
        self.assertEqual(seen, [2500, 900, 900, 1])
        self.assertEqual(store._queued_values, 0)
