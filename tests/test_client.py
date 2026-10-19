""" Test of pyModbusTCP.ModbusClient """

import random
import socket
import struct
import threading
import time
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.client._functions import _decode_regs
from pyModbusTCP.constants import (
    EXP_DATA_ADDRESS,
    EXP_NONE,
    MB_CONNECT_ERR,
    MB_EXCEPT_ERR,
    MB_NO_ERR,
    MB_RECV_ERR,
    MB_TIMEOUT_ERR,
)
from pyModbusTCP.utils import _unpack_bits, set_bit


class TestModbusClient(unittest.TestCase):
    """ ModbusClient tests class. """

    def test_host(self) -> None:
        """Test of host property."""
        # default value
        self.assertEqual(ModbusClient().host, 'localhost')
        # should raise ValueError for bad value
        self.assertRaises(ValueError, ModbusClient, host='wrong@host')
        self.assertRaises(ValueError, ModbusClient, host='::notip:1')
        # an empty host or a host with a NUL char is invalid too (ValueError, not another exception)
        self.assertRaises(ValueError, ModbusClient, host='')
        self.assertRaises(ValueError, ModbusClient, host='bad\x00host')
        # shouldn't raise ValueError for valid value
        try:
            [ModbusClient(host=h) for h in ['CamelCaseHost', 'plc-1.net', 'my.good.host',
                                            '_test.example.com', '42.example.com',
                                            '127.0.0.1', '::1']]
        except ValueError:
            self.fail('ModbusClient.host property raised ValueError unexpectedly')

    def test_port(self) -> None:
        """Test of port property."""
        # default value
        self.assertEqual(ModbusClient().port, 502)
        # should raise an exception for bad value
        self.assertRaises(TypeError, ModbusClient, port='amsterdam')
        self.assertRaises(ValueError, ModbusClient, port=-1)
        # shouldn't raise ValueError for valid value
        try:
            ModbusClient(port=5020)
        except ValueError:
            self.fail('ModbusClient.port property raised ValueError unexpectedly')

    def test_unit_id(self) -> None:
        """Test of unit_id property."""
        # default value
        self.assertEqual(ModbusClient().unit_id, 1)
        # should raise an exception for bad unit_id
        self.assertRaises(TypeError, ModbusClient, unit_id='@')
        self.assertRaises(ValueError, ModbusClient, unit_id=420)
        # shouldn't raise ValueError for valid value
        try:
            ModbusClient(unit_id=0)
            ModbusClient(unit_id=255)
        except ValueError:
            self.fail('ModbusClient.unit_id property raised ValueError unexpectedly')

    def test_timeout(self) -> None:
        """Test of timeout property."""
        # default value
        self.assertEqual(ModbusClient().timeout, 30.0)
        # an int is accepted and stored as a float
        self.assertEqual(ModbusClient(timeout=5).timeout, 5.0)
        self.assertIsInstance(ModbusClient(timeout=5).timeout, float)
        # bad types: the message is about timeout (not about another param)
        # bad values are typed Any: they are deliberately not of the annotated type
        bad_types: tuple[Any, ...] = ('1', None, [1.0])
        for bad_type in bad_types:
            with self.assertRaisesRegex(TypeError, 'timeout'):
                ModbusClient(timeout=bad_type)
        # bad values: out of range or not finite
        for bad_value in (0, -1.0, 3600, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                ModbusClient(timeout=bad_value)

    def test_bool_is_not_an_int_param(self) -> None:
        """A bool is not accepted where a number is expected (port=True would silently be port 1)."""
        self.assertRaises(TypeError, ModbusClient, port=True)
        self.assertRaises(TypeError, ModbusClient, unit_id=False)
        self.assertRaises(TypeError, ModbusClient, timeout=True)
        c = ModbusClient()
        with self.assertRaises(TypeError):
            c.port = True
        with self.assertRaises(TypeError):
            c.unit_id = True

    def test_misc(self) -> None:
        """Check of misc default values."""
        self.assertEqual(ModbusClient().auto_open, True)
        self.assertEqual(ModbusClient().auto_close, False)

    def test_is_open(self) -> None:
        """Test of is_open property (no connection: must be closed)."""
        c = ModbusClient()
        self.assertFalse(c.is_open)
        c.close()
        self.assertFalse(c.is_open)

    def test_context_manager(self) -> None:
        """The with statement closes the connection on exit (also on an exception)."""
        port = self._start_fake_server(lambda req: req[:2] + b'\x00\x00\x00\x05\x01\x03\x02\x00\x2a')
        with ModbusClient('127.0.0.1', port, timeout=2.0) as c:
            self.assertEqual(c.read_holding_registers(0), [42])
            self.assertTrue(c.is_open)
        self.assertFalse(c.is_open)
        c2 = ModbusClient()
        with self.assertRaises(RuntimeError), c2:
            raise RuntimeError('boom')
        self.assertFalse(c2.is_open)

    def test_params_type_check(self) -> None:
        """Non-integer params must raise TypeError (not struct.error), bad ranges ValueError."""
        c = ModbusClient()
        calls: list[Callable[[Any], object]] = [
            lambda v: c.read_coils(v, 1),
            lambda v: c.read_coils(0, v),
            lambda v: c.read_discrete_inputs(v, 1),
            lambda v: c.read_holding_registers(v, 1),
            lambda v: c.read_holding_registers(0, v),
            lambda v: c.read_input_registers(v, 1),
            lambda v: c.read_device_identification(v),
            lambda v: c.read_device_identification(1, v),
            lambda v: c.write_single_coil(v, True),
            lambda v: c.write_single_register(v, 0),
            lambda v: c.write_single_register(0, v),
            lambda v: c.write_multiple_coils(v, [True]),
            lambda v: c.write_multiple_registers(v, [0]),
            lambda v: c.write_multiple_registers(0, [v]),
            lambda v: c.write_read_multiple_registers(v, [0], 0),
            lambda v: c.write_read_multiple_registers(0, [v], 0),
            lambda v: c.write_read_multiple_registers(0, [0], v)]
        for call in calls:
            for bad_value in (1.0, '1', None):
                with self.assertRaises(TypeError):
                    call(bad_value)
        # out of range values still raise ValueError
        self.assertRaises(ValueError, c.read_holding_registers, 0, 126)
        self.assertRaises(ValueError, c.write_multiple_registers, 0, [0x10000])
        self.assertRaises(ValueError, c.write_multiple_registers, 0xffff, [0, 0])
        self.assertRaises(ValueError, c.write_read_multiple_registers, 0, [-1], 0)
        # an empty list of values is out of range too
        self.assertRaises(ValueError, c.write_multiple_registers, 0, [])
        self.assertRaises(ValueError, c.write_multiple_coils, 0, [])
        self.assertRaises(ValueError, c.write_read_multiple_registers, 0, [], 0)

    def test_custom_request_params(self) -> None:
        """custom_request() rejects a PDU that can't be a modbus request (before any network I/O)."""
        c = ModbusClient()
        with mock.patch.object(c, '_req_pdu', side_effect=AssertionError('no request expected')):
            # bad values are typed Any: they are deliberately not of the annotated type
            bad_types: tuple[Any, ...] = ('\x03\x00', [3, 0], None, 3)
            for bad_type in bad_types:
                with self.assertRaises(TypeError):
                    c.custom_request(bad_type)
            # empty or too long (max PDU size is 253 bytes)
            for bad_value in (b'', bytes(254), ):
                with self.assertRaises(ValueError):
                    c.custom_request(bad_value)
        # bytes-like objects are accepted and sent as bytes
        with mock.patch.object(c, '_req_pdu', return_value=b'\x03\x02\x00\x01') as req_pdu:
            self.assertEqual(c.custom_request(b'\x03\x00\x00\x00\x01'), b'\x03\x02\x00\x01')
            self.assertEqual(c.custom_request(bytearray(b'\x03\x00\x00\x00\x01')), b'\x03\x02\x00\x01')
            for call in req_pdu.call_args_list:
                self.assertIs(type(call.args[0]), bytes)

    def test_transaction_id(self) -> None:
        """The MBAP transaction ID is a 16 bits counter: two consecutive requests never have the same ID."""
        c = ModbusClient()
        start = c._transaction_id
        self.assertTrue(0 <= start <= 0xffff)
        tids = [struct.unpack('>H', c._add_mbap(b'\x03\x00\x00\x00\x01')[:2])[0] for _ in range(3)]
        self.assertEqual(tids, [(start + i) & 0xffff for i in (1, 2, 3)])
        # wrap around after 0xffff
        c._transaction_id = 0xfffe
        tids = [struct.unpack('>H', c._add_mbap(b'\x03\x00\x00\x00\x01')[:2])[0] for _ in range(3)]
        self.assertEqual(tids, [0xffff, 0x0000, 0x0001])

    def test_decode_helpers(self) -> None:
        """Compare bits/regs decoding helpers with a naive reference implementation."""
        rnd = random.Random(42)
        for nb in list(range(1, 40)) + [125, 1999, 2000]:
            # bits: rx frame can have more bytes than requested and unused bits set to 1
            raw = bytes(rnd.randrange(256) for _ in range((nb + 7) // 8))
            ref_bits = [bool((raw[i // 8] >> i % 8) & 0x01) for i in range(nb)]
            self.assertEqual(_unpack_bits(raw, nb), ref_bits)
        for nb in list(range(1, 126)):
            raw = bytes(rnd.randrange(256) for _ in range(2 * nb + 2))
            ref_regs = [raw[2 * i] << 8 | raw[2 * i + 1] for i in range(nb)]
            self.assertEqual(_decode_regs(raw, nb), ref_regs)

    def test_host_resolution_error(self) -> None:
        """A name resolution failure must be reported like any connect error (no socket.gaierror raised)."""
        c = ModbusClient('localhost', 502, auto_open=True)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertIsNone(c.read_coils(0))
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertFalse(c.is_open)

    def test_host_idna_encode_error(self) -> None:
        """A name that the IDNA codec can't encode (UnicodeError) is a connect error too, never an exception."""
        c = ModbusClient('localhost', 502, auto_open=True)
        with mock.patch('socket.getaddrinfo', side_effect=UnicodeError('label too long')):
            self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertIsNone(c.read_holding_registers(0))
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertFalse(c.write_single_coil(0, True))
            self.assertEqual(c.last_error, MB_CONNECT_ERR)

    def test_open_resets_last_error(self) -> None:
        """A successful open() clears the error of a previous failed open()."""
        port = self._start_fake_server(lambda req: b'')
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
        self.assertEqual(c.last_error, MB_CONNECT_ERR)
        self.assertTrue(c.open())
        self.assertEqual(c.last_error, MB_NO_ERR)

    def _start_fake_server(self, make_response: Callable[[bytes], bytes]) -> int:
        """Start a one-shot fake modbus server: one connection, one request, make_response(request) is sent back.

        :returns: the TCP port of the server
        """
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()

        def serve() -> None:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5.0)
                    req = conn.recv(256)
                    if req:
                        conn.sendall(make_response(req))
                        # wait for the client to close the connection
                        conn.recv(256)
                except OSError:
                    pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5.0)
        port: int = listener.getsockname()[1]
        return port

    def _start_slow_server(self, byte_delay: float) -> int:
        """Start a fake modbus server which answers "read 1 holding register" (value 42) one byte at a time."""
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()

        def serve() -> None:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                req = conn.recv(256)
                resp = req[:2] + b'\x00\x00\x00\x05\x01\x03\x02\x00\x2a'
                try:
                    for byte in resp:
                        time.sleep(byte_delay)
                        conn.sendall(bytes([byte]))
                except OSError:
                    pass  # client has closed the connection (timeout)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5.0)
        port: int = listener.getsockname()[1]
        return port

    def test_timeout_is_for_the_whole_request(self) -> None:
        """A server that sends its response byte by byte must not hold the client longer than timeout."""
        port = self._start_slow_server(byte_delay=0.2)  # 11 bytes: 2.2 s for the complete response
        c = ModbusClient('127.0.0.1', port, timeout=0.5)
        self.addCleanup(c.close)
        start = time.monotonic()
        self.assertIsNone(c.read_holding_registers(0, 1))
        elapsed = time.monotonic() - start
        self.assertEqual(c.last_error, MB_TIMEOUT_ERR)
        self.assertFalse(c.is_open)
        self.assertLess(elapsed, 1.5, 'timeout is applied to each recv() instead of the whole request')

    def test_split_response_within_timeout(self) -> None:
        """A response split in many TCP segments is still accepted if the whole request fit in timeout."""
        port = self._start_slow_server(byte_delay=0.02)  # 0.22 s for the complete response
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0, 1), [42])

    def test_reply_function_code_mismatch(self) -> None:
        """A reply with another function code than the request is an error, even an exception reply."""
        replies = {
            # a valid "read input registers" reply to a "read holding registers" request
            'other function': b'\x00\x00\x00\x05\x01\x04\x02\x00\x2a',
            # an exception for another function
            'other exception': b'\x00\x00\x00\x03\x01\x84\x02',
        }
        for name, reply in replies.items():
            with self.subTest(name):
                def make_response(req: bytes, reply: bytes = reply) -> bytes:
                    return req[:2] + reply
                port = self._start_fake_server(make_response)
                c = ModbusClient('127.0.0.1', port, timeout=2.0)
                self.addCleanup(c.close)
                self.assertIsNone(c.read_holding_registers(0))
                self.assertEqual(c.last_error, MB_RECV_ERR)
                self.assertEqual(c.last_except, EXP_NONE)

    def test_mbap_length_out_of_range(self) -> None:
        """An MBAP length field out of the modbus range (2 to 254) is an error and closes the connection."""
        for length in (0, 1, 255, 0xffff):
            with self.subTest(length=length):
                def make_response(req: bytes, length: int = length) -> bytes:
                    return req[:2] + struct.pack('>HHB', 0, length, 1) + b'\x03\x02\x00\x2a'
                port = self._start_fake_server(make_response)
                c = ModbusClient('127.0.0.1', port, timeout=2.0)
                self.addCleanup(c.close)
                self.assertIsNone(c.read_holding_registers(0))
                self.assertEqual(c.last_error, MB_RECV_ERR)
                self.assertFalse(c.is_open)

    def test_modbus_exception_log(self) -> None:
        """The debug message of a modbus exception gives the exception name (not the error name)."""
        port = self._start_fake_server(lambda req: req[:2] + b'\x00\x00\x00\x03\x01\x83\x02')
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        with self.assertLogs('pyModbusTCP.client', level='DEBUG') as logs:
            self.assertIsNone(c.read_holding_registers(0))
        self.assertEqual(c.last_error, MB_EXCEPT_ERR)
        self.assertEqual(c.last_except, EXP_DATA_ADDRESS)
        self.assertTrue(any('illegal data address' in line for line in logs.output), logs.output)

    def test_read_device_identification_bad_reply(self) -> None:
        """A device identification reply with a bad header or a truncated object is an error."""
        bad_replies = {
            # MEI type is not 0x0E
            'mei type': b'\x2b\x0d\x01\x83\x00\x00\x00',
            # read device id code is not the requested one (1)
            'read code': b'\x2b\x0e\x02\x83\x00\x00\x00',
            # one object announced, header truncated (no length byte)
            'object header': b'\x2b\x0e\x01\x83\x00\x00\x01\x00',
            # one object announced, value shorter than its length
            'object value': b'\x2b\x0e\x01\x83\x00\x00\x01\x00\x04ab',
        }
        for name, rx_pdu in bad_replies.items():
            with self.subTest(name):
                c = ModbusClient()
                with mock.patch.object(c, '_req_pdu', return_value=rx_pdu):
                    self.assertIsNone(c.read_device_identification())
                self.assertEqual(c.last_error, MB_RECV_ERR)
        # a valid reply for comparison
        c = ModbusClient()
        with mock.patch.object(c, '_req_pdu', return_value=b'\x2b\x0e\x01\x83\x00\x00\x01\x00\x02ab'):
            resp = c.read_device_identification()
        if resp is None:
            self.fail('ModbusClient.read_device_identification() failed unexpectedly')
        self.assertEqual(resp.vendor_name, b'ab')

    def test_write_multiple_pdu(self) -> None:
        """The PDU sent by write_multiple_coils()/write_multiple_registers() must keep the modbus format."""
        rnd = random.Random(99)
        c = ModbusClient()
        sent: list[bytes] = []

        def fake_req_pdu(tx_pdu: bytes, rx_min_len: int) -> bytes:
            sent.append(tx_pdu)
            # echo address and quantity (the response of write multiple coils/registers)
            return struct.pack('>BHH', tx_pdu[0], *struct.unpack('>HH', tx_pdu[1:5]))

        with mock.patch.object(c, '_req_pdu', side_effect=fake_req_pdu):
            for nb in (1, 2, 7, 8, 9, 16, 17, 100, 1968):
                bits = [rnd.random() < 0.5 for _ in range(nb)]
                self.assertTrue(c.write_multiple_coils(10, bits))
                ref = bytearray((nb + 7) // 8)
                for i, bit in enumerate(bits):
                    if bit:
                        ref[i // 8] = set_bit(ref[i // 8], i % 8)
                self.assertEqual(sent[-1], struct.pack('>BHHB', 0x0f, 10, nb, len(ref)) + bytes(ref))
            for nb in (1, 2, 50, 123):
                regs = [rnd.randrange(0x10000) for _ in range(nb)]
                self.assertTrue(c.write_multiple_registers(20, regs))
                self.assertEqual(sent[-1], struct.pack('>BHHB', 0x10, 20, nb, 2 * nb) + struct.pack(f'>{nb}H', *regs))
            # int-like items (bool) and edge values are accepted
            self.assertTrue(c.write_multiple_registers(0, [0, 0xffff, True, False]))
            self.assertEqual(sent[-1][6:], struct.pack('>4H', 0, 0xffff, 1, 0))
            # a tuple is a valid sequence too
            self.assertTrue(c.write_multiple_coils(0, (True, False, True)))
            self.assertEqual(sent[-1][-1:], b'\x05')
            # and any iterable (range, generator)
            self.assertTrue(c.write_multiple_registers(0, range(3)))
            self.assertEqual(sent[-1][6:], struct.pack('>3H', 0, 1, 2))
            self.assertTrue(c.write_multiple_registers(0, (v for v in (7, 8))))
            self.assertEqual(sent[-1][6:], struct.pack('>2H', 7, 8))
            self.assertTrue(c.write_multiple_coils(0, (b for b in (True, True))))
            self.assertEqual(sent[-1][-1:], b'\x03')
        # invalid items never reach the network
        with mock.patch.object(c, '_req_pdu', side_effect=AssertionError('no request expected')):
            self.assertRaises(ValueError, c.write_multiple_registers, 0, [1, -1])
            self.assertRaises(ValueError, c.write_multiple_registers, 0, [1, 0x10000])
            self.assertRaises(TypeError, c.write_multiple_registers, 0, [1, 2.0])
            self.assertRaises(TypeError, c.write_multiple_registers, 0, [1, '2'])
            self.assertRaises(TypeError, c.write_read_multiple_registers, 0, [1, None], 0)
            self.assertRaises(ValueError, c.write_read_multiple_registers, 0, [1, -1], 0)
            # coils items must be bool or integers (like write_single_coil)
            self.assertRaises(TypeError, c.write_multiple_coils, 0, [True, 'a'])
            self.assertRaises(TypeError, c.write_multiple_coils, 0, [None])
            self.assertRaises(TypeError, c.write_multiple_coils, 0, [1.5])
            self.assertRaises(TypeError, c.write_multiple_coils, 0, [[]])

    def test_write_multiple_reply_mismatch(self) -> None:
        """
        A reply that doesn't echo the address/quantity of the request is an error (last_error), not a silent False.
        """
        def reply(addr_delta: int, qty_delta: int) -> Callable[[bytes, int], bytes]:
            def fake_req_pdu(tx_pdu: bytes, rx_min_len: int) -> bytes:
                addr, qty = struct.unpack('>HH', tx_pdu[1:5])
                return struct.pack('>BHH', tx_pdu[0], addr + addr_delta, qty + qty_delta)
            return fake_req_pdu

        calls: dict[str, Callable[[ModbusClient], bool]] = {
            'write_multiple_coils': lambda c: c.write_multiple_coils(10, [True, False, True]),
            'write_multiple_registers': lambda c: c.write_multiple_registers(10, [1, 2, 3])}
        for name, call in calls.items():
            for addr_delta, qty_delta in ((0, 0), (1, 0), (0, 1), (0, -1)):
                with self.subTest(method=name, addr_delta=addr_delta, qty_delta=qty_delta):
                    # a new client each time: with _req_pdu mocked, last_error is not reset between two calls
                    c = ModbusClient()
                    with mock.patch.object(c, '_req_pdu', side_effect=reply(addr_delta, qty_delta)):
                        result = call(c)
                    if (addr_delta, qty_delta) == (0, 0):
                        self.assertIs(result, True)
                        self.assertEqual(c.last_error, MB_NO_ERR)
                    else:
                        self.assertIs(result, False)
                        self.assertEqual(c.last_error, MB_RECV_ERR)


if __name__ == '__main__':
    unittest.main()


class FakeGateway:
    """A Modbus/TCP server for the broadcast tests: records the frames, replies as told by reply(frame)."""

    def __init__(self, reply: Callable[[bytes], bytes]) -> None:
        self.reply = reply
        self.frames: list[bytes] = []
        self.connections = 0
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(4)
        self.port: int = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        with conn:
            try:
                while True:
                    header = conn.recv(7)
                    if len(header) < 7:
                        return
                    frame = header + conn.recv(struct.unpack('>H', header[4:6])[0] - 1)
                    self.frames.append(frame)
                    conn.sendall(self.reply(frame))
            except OSError:
                return

    def close(self) -> None:
        self.sock.close()


def echo_unicast(frame: bytes) -> bytes:
    """A gateway that never answers a broadcast (unit id 0), and echoes the other write requests."""
    return b'' if frame[6] == 0 else frame


class TestBroadcast(unittest.TestCase):
    """Broadcast requests (unit id 0, through a TCP to RTU gateway): sent without waiting for a reply."""

    def gateway(self, reply: Callable[[bytes], bytes]) -> FakeGateway:
        gw = FakeGateway(reply)
        self.addCleanup(gw.close)
        return gw

    def test_no_wait(self) -> None:
        gw = self.gateway(echo_unicast)
        # 127.0.0.1, not localhost: on Windows, a refused connection to ::1 (the gateway listens on IPv4 only) takes
        # about 2 s before the client tries 127.0.0.1
        c = ModbusClient(host='127.0.0.1', port=gw.port, unit_id=5, timeout=2.0)
        self.addCleanup(c.close)
        # the connection is opened before the measure: only the broadcasts are timed
        self.assertTrue(c.open())
        start = time.monotonic()
        self.assertTrue(c.write_single_register(10, 42, broadcast=True))
        self.assertTrue(c.write_single_coil(1, True, broadcast=True))
        self.assertTrue(c.write_multiple_coils(0, [1, 0, 1], broadcast=True))
        self.assertTrue(c.write_multiple_registers(0, [1, 2], broadcast=True))
        self.assertLess(time.monotonic() - start, 1.0, 'a broadcast never waits for a reply')
        self.assertEqual(c.last_error, MB_NO_ERR)
        # unit id 0 in the frames, whatever the unit id of the client (kept for the other requests)
        deadline = time.monotonic() + 2.0
        while len(gw.frames) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual([frame[6] for frame in gw.frames], [0, 0, 0, 0])
        self.assertEqual(gw.frames[0][7:], bytes.fromhex('06000a002a'))
        self.assertEqual(c.unit_id, 5)
        self.assertTrue(c.write_single_register(10, 42))
        self.assertEqual(gw.frames[-1][6], 5)

    def test_late_reply_is_ignored(self) -> None:
        """A gateway that answers a broadcast anyway (an echo, an exception): the next request still succeeds."""
        def echo(frame: bytes) -> bytes:
            return frame

        def exception_0x0b(frame: bytes) -> bytes:
            return frame[:4] + b'\x00\x03' + bytes([frame[6], frame[7] | 0x80, 0x0B])

        for broadcast_reply in (echo, exception_0x0b):
            def reply(frame: bytes, broadcast_reply: Callable[[bytes], bytes] = broadcast_reply) -> bytes:
                return broadcast_reply(frame) if frame[6] == 0 else frame

            gw = self.gateway(reply)
            c = ModbusClient(host='127.0.0.1', port=gw.port, unit_id=1, timeout=2.0)
            self.addCleanup(c.close)
            self.assertTrue(c.write_single_register(10, 42, broadcast=True))
            self.assertTrue(c.write_single_register(11, 43, broadcast=True))
            time.sleep(0.1)
            self.assertTrue(c.write_single_register(12, 44))
            self.assertTrue(c.is_open, 'the connection stays open')
            self.assertEqual(gw.connections, 1)

    def test_burst_of_broadcasts(self) -> None:
        """30 broadcasts answered late (more than the 16 of the first version): the next request still succeeds."""
        gw = self.gateway(lambda frame: frame)
        c = ModbusClient(host='127.0.0.1', port=gw.port, unit_id=1, timeout=2.0, broadcast_interval=0)
        self.addCleanup(c.close)
        for value in range(30):
            self.assertTrue(c.write_single_register(10, value, broadcast=True))
        time.sleep(0.2)
        self.assertTrue(c.write_single_register(12, 44))
        self.assertTrue(c.is_open)
        self.assertEqual(c._broadcast_ids, {}, 'every late reply consumed')

    def test_old_broadcast_is_forgotten(self) -> None:
        """A reply to a broadcast sent too long ago (or beyond the last 64) is not ignored: the connection is closed."""
        from pyModbusTCP.client import _transport
        gw = self.gateway(lambda frame: frame)
        c = ModbusClient(host='127.0.0.1', port=gw.port, unit_id=1, timeout=2.0)
        self.addCleanup(c.close)
        self.assertTrue(c.write_single_register(10, 1, broadcast=True))
        # as if it was sent long ago
        for tid in c._broadcast_ids:
            c._broadcast_ids[tid] -= _transport.BROADCAST_REPLY_DELAY + 1.0
        time.sleep(0.1)
        self.assertFalse(c.write_single_register(12, 44))
        self.assertEqual(c.last_error, MB_RECV_ERR)
        self.assertEqual(c._broadcast_ids, {}, 'cleared by close()')
        # at most BROADCAST_IDS_MAX ids are kept, the oldest are dropped
        gw = self.gateway(echo_unicast)
        c = ModbusClient(host='127.0.0.1', port=gw.port, timeout=2.0, broadcast_interval=0)
        self.addCleanup(c.close)
        for _ in range(_transport.BROADCAST_IDS_MAX + 10):
            c.write_single_register(0, 1, broadcast=True)
        self.assertEqual(len(c._broadcast_ids), _transport.BROADCAST_IDS_MAX)
        self.assertEqual(list(c._broadcast_ids)[-1], c._transaction_id)

    def test_broadcast_interval(self) -> None:
        """Two broadcasts are at least broadcast_interval apart (default 0.1 s), the other requests never wait."""
        gw = self.gateway(echo_unicast)
        c = ModbusClient(host='127.0.0.1', port=gw.port, timeout=2.0, broadcast_interval=0.2)
        self.addCleanup(c.close)
        self.assertTrue(c.open())
        start = time.monotonic()
        for _ in range(4):
            self.assertTrue(c.write_single_register(0, 1, broadcast=True))
        self.assertGreaterEqual(time.monotonic() - start, 0.6, '3 intervals between 4 broadcasts')
        start = time.monotonic()
        self.assertTrue(c.write_single_register(0, 1))
        self.assertLess(time.monotonic() - start, 0.15, 'a normal request doesn\'t wait')
        # settings
        self.assertEqual(ModbusClient().broadcast_interval, 0.15)
        c.broadcast_interval = 0
        self.assertIn('broadcast_interval=0', repr(c))
        for value in (-0.1, 11, float('nan')):
            with self.assertRaises(ValueError):
                c.broadcast_interval = value
        with self.assertRaises(TypeError):
            ModbusClient(broadcast_interval='1')  # type: ignore[arg-type]

    def test_unexpected_reply_still_closes(self) -> None:
        """A frame with an unknown transaction id is never ignored: the connection is closed, as before."""
        gw = self.gateway(lambda frame: struct.pack('>H', (struct.unpack('>H', frame[:2])[0] + 100) & 0xFFFF)
                          + frame[2:])
        c = ModbusClient(host='127.0.0.1', port=gw.port, unit_id=1, timeout=2.0)
        self.assertFalse(c.write_single_register(12, 44))
        self.assertEqual(c.last_error, MB_RECV_ERR)
        self.assertFalse(c.is_open)

    def test_custom_request(self) -> None:
        gw = self.gateway(echo_unicast)
        c = ModbusClient(host='127.0.0.1', port=gw.port, timeout=2.0)
        self.addCleanup(c.close)
        pdu = bytes.fromhex('16000a00ff0000')
        self.assertEqual(c.custom_request(pdu, broadcast=True), pdu)
        # a function that reads can't be broadcast
        for read_pdu in ('0300000001', '17000000010000000102002a', '2b0e0100'):
            with self.assertRaises(ValueError):
                c.custom_request(bytes.fromhex(read_pdu), broadcast=True)
        with self.assertRaises(TypeError):
            c.write_single_register(0, 1, broadcast='yes')  # type: ignore[arg-type]

    def test_auto_close_and_errors(self) -> None:
        gw = self.gateway(echo_unicast)
        c = ModbusClient(host='127.0.0.1', port=gw.port, timeout=2.0, auto_close=True)
        self.assertTrue(c.write_single_register(0, 1, broadcast=True))
        self.assertFalse(c.is_open)
        # nobody listening: False, with the error
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            free_port = sock.getsockname()[1]
        c = ModbusClient(host='127.0.0.1', port=free_port, timeout=0.5)
        self.assertFalse(c.write_single_register(0, 1, broadcast=True))
        self.assertEqual(c.last_error, MB_CONNECT_ERR)
