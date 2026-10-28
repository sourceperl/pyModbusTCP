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
    MB_FRAME_ERR,
    MB_NO_ERR,
    MB_RECV_ERR,
    MB_RESOLVE_ERR,
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
        # a link-local IPv6 address: with its interface only (the message gives the form), never on another address
        self.assertEqual(ModbusClient(host='fe80::1%eth0').host, 'fe80::1%eth0')
        self.assertRaisesRegex(ValueError, "link-local IPv6 address: give its interface, like 'fe80::1%eth0'",
                               ModbusClient, host='fe80::1')
        self.assertRaises(ValueError, ModbusClient, host='2001:db8::1%eth0')
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

    def test_constructor_args(self) -> None:
        """The 6 first arguments can be positional (as in 0.3.x), the next ones are keyword-only."""
        c = ModbusClient('127.0.0.1', 5020, 2, 5.0, False, True)
        self.assertEqual((c.host, c.port, c.unit_id, c.timeout, c.auto_open, c.auto_close),
                         ('127.0.0.1', 5020, 2, 5.0, False, True))
        with self.assertRaises(TypeError):
            ModbusClient('127.0.0.1', 5020, 2, 5.0, False, True, 3)  # type: ignore[call-arg]
        c = ModbusClient(connect_retries=3, connect_retry_delay=1.0, tcp_no_delay=False, broadcast_interval=0.5)
        self.assertEqual((c.connect_retries, c.connect_retry_delay, c.tcp_no_delay, c.broadcast_interval),
                         (3, 1.0, False, 0.5))
        self.assertTrue(repr(c).endswith('auto_open=True, auto_close=False, connect_retries=3, '
                                         'connect_retry_delay=1, tcp_no_delay=False, broadcast_interval=0.5)'))
        # the old name is gone
        with self.assertRaises(TypeError):
            ModbusClient(no_delay=False)  # type: ignore[call-arg]
        self.assertRaises(TypeError, ModbusClient, tcp_no_delay='yes')
        # a flag is a bool only, like the flags of ModbusServer (audit C10): no 0/1, no None
        for name in ('auto_open', 'auto_close', 'tcp_no_delay'):
            bad_values: tuple[Any, ...] = (1, 0, None, 'yes')
            for bad in bad_values:
                kwargs: dict[str, Any] = {name: bad}
                with self.subTest(name=name, value=bad), self.assertRaises(TypeError):
                    ModbusClient(**kwargs)
                c = ModbusClient()
                with self.subTest(name=name, value=bad, setter=True), self.assertRaises(TypeError):
                    setattr(c, name, bad)
        c = ModbusClient(port=1)
        bad_broadcast: Any = 1
        with self.assertRaises(TypeError):
            c.write_single_register(0, 1, broadcast=bad_broadcast)
        self.assertRaises(TypeError, c.write_single_coil, 0, True, broadcast=None)
        # coil values are data, not flags: integers still accepted (no server here: the request fails, no TypeError)
        self.assertFalse(c.write_single_coil(0, 1))  # type: ignore[arg-type]

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
        """A name resolution failure is MB_RESOLVE_ERR (no socket.gaierror raised), not MB_CONNECT_ERR."""
        c = ModbusClient('localhost', 502, auto_open=True)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)
            self.assertIsNone(c.read_coils(0))
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)
            self.assertFalse(c.is_open)

    def test_host_idna_encode_error(self) -> None:
        """A name that the IDNA codec can't encode (UnicodeError) is a resolution error too, never an exception."""
        c = ModbusClient('localhost', 502, auto_open=True)
        with mock.patch('socket.getaddrinfo', side_effect=UnicodeError('label too long')):
            self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)
            self.assertIsNone(c.read_holding_registers(0))
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)
            self.assertFalse(c.write_single_coil(0, True))
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)

    def test_open_resets_last_error(self) -> None:
        """A successful open() clears the error of a previous failed open()."""
        port = self._start_fake_server(lambda req: b'')
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
        self.assertEqual(c.last_error, MB_RESOLVE_ERR)
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
                self.assertEqual(c.last_error, MB_FRAME_ERR)
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
                self.assertEqual(c.last_error, MB_FRAME_ERR)
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
        """A device identification reply with a bad header, a truncated object or data after the last object is an
        error."""
        bad_replies = {
            # MEI type is not 0x0E
            'mei type': b'\x2b\x0d\x01\x83\x00\x00\x00',
            # read device id code is not the requested one (1)
            'read code': b'\x2b\x0e\x02\x83\x00\x00\x00',
            # one object announced, header truncated (no length byte)
            'object header': b'\x2b\x0e\x01\x83\x00\x00\x01\x00',
            # one object announced, value shorter than its length
            'object value': b'\x2b\x0e\x01\x83\x00\x00\x01\x00\x04ab',
            # one object announced, more bytes after it (S7: they were ignored)
            'trailing bytes': b'\x2b\x0e\x01\x83\x00\x00\x01\x00\x02abGARBAGE',
            # no object announced, but some data
            'data without object': b'\x2b\x0e\x01\x83\x00\x00\x00\x00',
        }
        for name, rx_pdu in bad_replies.items():
            with self.subTest(name):
                c = ModbusClient()
                with mock.patch.object(c, '_req_pdu', return_value=rx_pdu):
                    self.assertIsNone(c.read_device_identification())
                self.assertEqual(c.last_error, MB_FRAME_ERR)
        # a valid reply for comparison
        c = ModbusClient()
        with mock.patch.object(c, '_req_pdu', return_value=b'\x2b\x0e\x01\x83\x00\x00\x01\x00\x02ab'):
            resp = c.read_device_identification()
        if resp is None:
            self.fail('ModbusClient.read_device_identification() failed unexpectedly')
        self.assertEqual(resp.vendor_name, b'ab')

    def test_read_reply_with_another_quantity(self) -> None:
        """A read reply must have exactly the data requested: more (or less) is an error, never truncated (S-06)."""
        replies = [
            # 3 registers for 1 (and the same with a byte count that doesn't match the buffer)
            ('read_holding_registers', (100, 1), b'\x03\x06\xde\xad\xbe\xef\xca\xfe'),
            ('read_input_registers', (100, 2), b'\x04\x06\xde\xad\xbe\xef\xca\xfe'),
            ('read_holding_registers', (0, 2), b'\x03\x02\x00\x01'),
            # 24 bits for 4 (1 byte expected), 8 bits for 9 (2 bytes expected)
            ('read_coils', (0, 4), b'\x01\x03\xff\xff\xff'),
            ('read_discrete_inputs', (0, 9), b'\x02\x01\xff'),
            # 0x17: 4 registers read for 2
            ('write_read_multiple_registers', (0, [7], 0, 2), b'\x17\x08\x00\x01\x00\x02\x00\x03\x00\x04'),
        ]
        for method, args, rx_pdu in replies:
            with self.subTest(method=method, args=args):
                c = ModbusClient()
                with mock.patch.object(c, '_req_pdu', return_value=rx_pdu):
                    self.assertIsNone(getattr(c, method)(*args))
                self.assertEqual(c.last_error, MB_FRAME_ERR)
        # the exact size is accepted
        good = [('read_holding_registers', (100, 1), b'\x03\x02\xde\xad', [0xDEAD]),
                ('read_coils', (0, 9), b'\x01\x02\xff\x01', [True] * 9),
                ('write_read_multiple_registers', (0, [7], 0, 2), b'\x17\x04\x00\x01\x00\x02', [1, 2])]
        for method, args, rx_pdu, expected in good:
            c = ModbusClient()
            with mock.patch.object(c, '_req_pdu', return_value=rx_pdu):
                self.assertEqual(getattr(c, method)(*args), expected)

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
                        self.assertEqual(c.last_error, MB_FRAME_ERR)


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
        self.assertEqual(c.last_error, MB_FRAME_ERR)
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
        """Two broadcasts are at least broadcast_interval apart (default 0.15 s), the other requests never wait."""
        gw = self.gateway(echo_unicast)
        c = ModbusClient(host='127.0.0.1', port=gw.port, timeout=2.0, broadcast_interval=0.2)
        self.addCleanup(c.close)
        self.assertTrue(c.open())
        # perf_counter(): precise on every OS (monotonic() has a 15.6 ms step on Windows before Python 3.13)
        start = time.perf_counter()
        for _ in range(4):
            self.assertTrue(c.write_single_register(0, 1, broadcast=True))
        self.assertGreaterEqual(time.perf_counter() - start, 0.6, '3 intervals between 4 broadcasts')
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
        self.assertEqual(c.last_error, MB_FRAME_ERR)
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


class TestConnectRetry(unittest.TestCase):
    """ Connect retry (connect_retries, connect_retry_delay). """

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port: int = sock.getsockname()[1]
            return port

    def test_settings(self) -> None:
        c = ModbusClient()
        self.assertEqual(c.connect_retries, 0)
        self.assertEqual(c.connect_retry_delay, 0.5)
        c = ModbusClient(connect_retries=3, connect_retry_delay=1)
        self.assertEqual(c.connect_retries, 3)
        self.assertIsInstance(c.connect_retry_delay, float)
        self.assertIn('connect_retries=3, connect_retry_delay=1', repr(c))
        for bad_type in (True, 1.0, '1', None):
            with self.assertRaises(TypeError):
                ModbusClient(connect_retries=bad_type)  # type: ignore[arg-type]
        for bad_type in (True, '1', None):
            with self.assertRaises(TypeError):
                ModbusClient(connect_retry_delay=bad_type)  # type: ignore[arg-type]
        for bad_value in (-1, 1001):
            with self.assertRaises(ValueError):
                ModbusClient(connect_retries=bad_value)
        for bad_float in (-0.1, 60.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                ModbusClient(connect_retry_delay=bad_float)
        c.connect_retries = 0
        c.connect_retry_delay = 0
        self.assertEqual((c.connect_retries, c.connect_retry_delay), (0, 0.0))

    def test_no_retry_by_default(self) -> None:
        c = ModbusClient('127.0.0.1', self._free_port(), timeout=0.5)
        with mock.patch('pyModbusTCP.client._transport.time.sleep') as sleep:
            self.assertFalse(c.open())
        self.assertEqual(c.last_error, MB_CONNECT_ERR)
        sleep.assert_not_called()

    def test_retries_exhausted(self) -> None:
        """Every attempt fails: connect_retries + 1 attempts, an exponential backoff between them."""
        c = ModbusClient('127.0.0.1', self._free_port(), timeout=0.5, connect_retries=4, connect_retry_delay=0.2)
        with mock.patch('pyModbusTCP.client._transport.time.sleep') as sleep, \
                mock.patch.object(c, '_open_once', wraps=c._open_once) as open_once:
            self.assertFalse(c.open())
        self.assertEqual(c.last_error, MB_CONNECT_ERR)
        self.assertEqual(open_once.call_count, 5)
        delays = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(len(delays), 4)
        # base delays 0.2, 0.4, 0.8, 1.6: the wait is between half and all of it
        for n, delay in enumerate(delays):
            base = 0.2 * 2 ** n
            self.assertTrue(base / 2 <= delay <= base, f'retry {n + 1}: {delay} not in [{base / 2}, {base}]')

    def test_backoff_cap(self) -> None:
        c = ModbusClient(connect_retry_delay=10.0)
        for attempt in (3, 10, 100):
            self.assertLessEqual(c._connect_retry_wait(attempt), 60.0)
            self.assertGreaterEqual(c._connect_retry_wait(attempt), 30.0)
        self.assertEqual(ModbusClient(connect_retry_delay=0)._connect_retry_wait(5), 0.0)

    def test_server_up_after_a_while(self) -> None:
        """The server starts listening after the first attempts: a request with auto_open succeeds."""
        port = self._free_port()
        listening = threading.Event()

        def late_server() -> None:
            time.sleep(0.3)
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', port))
                listener.listen()
                listening.set()
                listener.settimeout(5.0)
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(5.0)
                    req = conn.recv(256)
                    conn.sendall(req[:2] + b'\x00\x00\x00\x05\x01\x03\x02\x00\x2a')
                    conn.recv(256)

        thread = threading.Thread(target=late_server, daemon=True)
        thread.start()
        c = ModbusClient('127.0.0.1', port, timeout=2.0, connect_retries=10, connect_retry_delay=0.05)
        self.addCleanup(thread.join, 5.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0), [42])
        self.assertTrue(listening.is_set())
        self.assertEqual(c.last_error, MB_NO_ERR)

    def test_name_resolution(self) -> None:
        """An unknown name is not retried, a temporary DNS failure (EAI_AGAIN) is."""
        c = ModbusClient('plc.invalid', connect_retries=3, connect_retry_delay=0)
        for errno, attempts in ((socket.EAI_NONAME, 1), (socket.EAI_AGAIN, 4)):
            with mock.patch('pyModbusTCP.client._transport.socket.getaddrinfo',
                            side_effect=socket.gaierror(errno, 'dns error')) as getaddrinfo:
                self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_RESOLVE_ERR)
            self.assertEqual(c.last_error_as_txt, 'name resolve error')
            self.assertEqual(getaddrinfo.call_count, attempts)

    def test_request_is_never_resent(self) -> None:
        """A request that fails after the connect (recv error) is not sent again."""
        requests: list[bytes] = []

        def make_response(req: bytes) -> bytes:
            requests.append(req)
            return b''

        port = TestModbusClient._start_fake_server(self, make_response)  # type: ignore[arg-type]
        c = ModbusClient('127.0.0.1', port, timeout=1.0, connect_retries=3, connect_retry_delay=0)
        self.addCleanup(c.close)
        self.assertFalse(c.write_single_register(0, 1))
        self.assertEqual(len(requests), 1)


class TestArgumentNames(unittest.TestCase):
    """ The request methods name their arguments the same way: address, count, value(s) (C-14). """

    def setUp(self) -> None:
        self.c = ModbusClient()

    def test_new_names(self) -> None:
        """Keyword calls with the new names: checked before any network I/O (no server here, the check fails first)."""
        calls: list[tuple[Any, dict[str, Any], str]] = [
            (self.c.read_coils, {'address': 0, 'count': 0}, 'count'),
            (self.c.read_discrete_inputs, {'address': -1, 'count': 1}, 'address'),
            (self.c.read_holding_registers, {'address': 0, 'count': 126}, 'count'),
            (self.c.read_input_registers, {'address': 70000}, 'address'),
            (self.c.write_single_coil, {'address': -1, 'value': True}, 'address'),
            (self.c.write_single_register, {'address': 0, 'value': 70000}, 'value'),
            (self.c.write_multiple_coils, {'address': 0, 'values': []}, 'number of values'),
            (self.c.write_multiple_registers, {'address': 0, 'values': [70000]}, 'values'),
            (self.c.write_read_multiple_registers,
             {'write_address': 0, 'write_values': [1], 'read_address': 0, 'read_count': 0}, 'read_count'),
        ]
        for method, kwargs, name in calls:
            with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, name):
                method(**kwargs)

    def test_old_names_refused(self) -> None:
        old: list[tuple[Any, dict[str, Any]]] = [
            (self.c.read_coils, {'bit_addr': 0, 'bit_nb': 1}),
            (self.c.read_holding_registers, {'reg_addr': 0, 'reg_nb': 1}),
            (self.c.write_single_register, {'reg_addr': 0, 'reg_value': 1}),
            (self.c.write_multiple_coils, {'bits_addr': 0, 'bits_value': [True]}),
            (self.c.write_multiple_registers, {'regs_addr': 0, 'regs_value': [1]}),
            (self.c.write_read_multiple_registers, {'write_addr': 0, 'write_values': [1], 'read_addr': 0}),
        ]
        for method, kwargs in old:
            with self.subTest(method=method.__name__), self.assertRaises(TypeError):
                method(**kwargs)


class TestConnectionClosedByServer(unittest.TestCase):
    """ auto_open: a connection closed by the server is reopened by the request itself, not by the next one. """

    REPLY = b'\x00\x00\x00\x05\x01\x03\x02\x00\x2a'  # MBAP (without transaction id) + read 1 register: 42

    def setUp(self) -> None:
        # set by the test once a reply is read: _rst() waits for it (see there)
        self.reply_read = threading.Event()

    def _server(self, on_conn: Callable[[socket.socket, list[bytes]], None]) -> tuple[int, list[bytes]]:
        """Start a server: on_conn(conn, requests) is called for each new connection (in a thread).

        :returns: the TCP port and the list of every request received
        """
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        requests: list[bytes] = []

        def serve() -> None:
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                conn.settimeout(5.0)
                try:
                    on_conn(conn, requests)
                except OSError:
                    pass

        threading.Thread(target=serve, daemon=True).start()
        port: int = listener.getsockname()[1]
        return port, requests

    def _reply_then(self, close: Callable[[socket.socket], None]) -> Callable[[socket.socket, list[bytes]], None]:
        """Connection handler: reply to the first request, then close(conn)."""
        def on_conn(conn: socket.socket, requests: list[bytes]) -> None:
            req = conn.recv(256)
            requests.append(req)
            # cleared before the reply is sent: a reply_read set for a previous reply (an earlier subtest, a FIN
            # close that doesn't wait for it) can't be taken for this one, the client sets it only once it's read
            self.reply_read.clear()
            conn.sendall(req[:2] + self.REPLY)
            close(conn)
        return on_conn

    def _rst(self, conn: socket.socket) -> None:
        """Close with a RST (SO_LINGER 0) instead of a FIN, once the client has read the reply.

        On Windows, a RST discards the received data not yet read by the application: a RST sent right after the
        reply could destroy it before the client reads it (Linux keeps it).
        """
        self.reply_read.wait(5.0)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        conn.close()

    def _check_every_request_ok(self, port: int, requests: list[bytes], closed_check: bool = True) -> None:
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        patcher = mock.patch.object(c, '_closed_by_peer', return_value=False) if not closed_check else None
        for n in range(3):
            self.assertEqual(c.read_holding_registers(0), [42], f'request {n}: {c.last_error_as_txt}')
            self.assertEqual(c.last_error, MB_NO_ERR)
            self.reply_read.set()
            # let the server close the connection
            time.sleep(0.1)
            if patcher and n == 0:
                # the close is not seen before the send (race): the request fails, then it is sent again
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_closed_by_server(self) -> None:
        """The server closes the connection (FIN) after each reply, like an idle timeout: every request is ok."""
        port, requests = self._server(self._reply_then(lambda conn: conn.close()))
        self._check_every_request_ok(port, requests)
        self.assertEqual(len(requests), 3)

    def test_reset_by_server(self) -> None:
        """The server resets the connection (RST) after each reply: every request is ok."""
        port, requests = self._server(self._reply_then(self._rst))
        self._check_every_request_ok(port, requests)
        self.assertEqual(len(requests), 3)

    def test_reset_not_seen_before_send(self) -> None:
        """The connection is reset but the check before the send doesn't see it (the server resets it at this very
        moment): the send fails with a reset, the server has not read the request: it is sent again once on a new
        connection."""
        port, requests = self._server(self._reply_then(self._rst))
        self._check_every_request_ok(port, requests, closed_check=False)
        # each request is received once: the first send, on the lost connection, never reached the server
        self.assertEqual(len(requests), 3)

    def test_close_not_seen_before_send(self) -> None:
        """The connection is closed (FIN) but the check before the send doesn't see it.

        The server, closed, never reads this request, it answers with a RST. What the client gets depends on the OS:
        - Linux/macOS: the end of stream first (b''), as for a server that reads the request then closes: it may
          have executed it, the request fails, not sent again;
        - Windows: the RST (ECONNRESET) before any reply byte: the client can't tell it from a server that executed
          the request then reset the connection, the request fails too, not sent again.
        Either way, a request is never sent twice: the server receives exactly the requests that succeeded.
        """
        port, requests = self._server(self._reply_then(lambda conn: conn.close()))
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0), [42])
        time.sleep(0.1)
        with mock.patch.object(c, '_closed_by_peer', return_value=False):
            result = c.read_holding_registers(0)
        if result is None:
            self.assertEqual(c.last_error, MB_RECV_ERR)
        else:
            self.assertEqual(result, [42])
        # let the server close the connection: on Windows the request above was sent again on a new connection, that
        # the server closes after its reply; without this wait, the next request could be sent before the close is
        # seen (the race of this test, but outside the mock): it would fail like the one above
        time.sleep(0.1)
        # the next request reconnects
        self.assertEqual(c.read_holding_registers(0), [42])
        # the server received exactly the requests that succeeded (2 or 3), never one more
        nb_ok = 2 + (result is not None)
        self.assertEqual(len(requests), nb_ok)

    def test_request_read_by_server_not_resent(self) -> None:
        """The server reads the request, then closes without reply: it may have executed it, no new send."""
        def on_conn(conn: socket.socket, requests: list[bytes]) -> None:
            if requests:
                req = conn.recv(256)
                requests.append(req)
                conn.close()
            else:
                self._reply_then(lambda c: None)(conn, requests)
                # keep the 1st connection open, the server reads the next request on it, then closes
                req = conn.recv(256)
                requests.append(req)
                conn.close()

        port, requests = self._server(on_conn)
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0), [42])
        self.assertFalse(c.write_single_register(0, 1))
        self.assertEqual(c.last_error, MB_RECV_ERR)
        time.sleep(0.1)
        self.assertEqual(len(requests), 2)

    def _execute_then_reset_server(self) -> tuple[int, list[bytes]]:
        """A server that answers every request (echo of a write, 42 for a read), except the 2nd request of the 1st
        connection: it reads (executes) it, then resets the connection (RST, SO_LINGER 0) without a reply."""
        def answer(conn: socket.socket, req: bytes) -> None:
            pdu = req[7:]
            if pdu[0] == 0x03:
                conn.sendall(req[:2] + self.REPLY)
            else:
                conn.sendall(req[:4] + struct.pack('>HB', len(pdu[:5]) + 1, req[6]) + pdu[:5])

        def on_conn(conn: socket.socket, requests: list[bytes]) -> None:
            first_conn = not requests
            while True:
                req = conn.recv(256)
                if not req:
                    conn.close()
                    return
                requests.append(req)
                if first_conn and len(requests) == 2:
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                    conn.close()
                    return
                answer(conn, req)

        return self._server(on_conn)

    def test_reset_after_execution_not_resent(self) -> None:
        """The server executes a request then resets the connection before its reply: it is never sent again (a write
        would be executed twice, a read can clear a counter on some devices), whatever the function."""
        requests_of: dict[str, Callable[[ModbusClient], object]] = {
            'read_holding_registers': lambda c: c.read_holding_registers(0),
            'read_device_identification': lambda c: c.read_device_identification(),
            'custom_request (read)': lambda c: c.custom_request(b'\x03\x00\x00\x00\x01'),
            'write_single_register': lambda c: c.write_single_register(1, 1234),
            'write_multiple_registers': lambda c: c.write_multiple_registers(1, [1, 2]),
            'write_read_multiple_registers': lambda c: c.write_read_multiple_registers(1, [1], 0, 1),
            'custom_request': lambda c: c.custom_request(b'\x41\x01'),
        }
        for name, request in requests_of.items():
            with self.subTest(request=name):
                port, requests = self._execute_then_reset_server()
                c = ModbusClient('127.0.0.1', port, timeout=2.0)
                self.addCleanup(c.close)
                self.assertEqual(c.read_holding_registers(0), [42])
                self.assertIn(request(c), (None, False))
                self.assertEqual(c.last_error, MB_RECV_ERR)
                time.sleep(0.1)
                # executed once: the 1st read, then the request (never a 2nd copy of it)
                self.assertEqual(len(requests), 2)
                # the next request reconnects
                self.assertEqual(c.read_holding_registers(0), [42])

    def test_write_send_failed_resent(self) -> None:
        """The connection is already reset when a write is sent (the check before the send doesn't see it): the
        send fails, the server never got the write, it is sent again once on a new connection and succeeds."""
        def on_conn(conn: socket.socket, requests: list[bytes]) -> None:
            req = conn.recv(256)
            requests.append(req)
            pdu = req[7:]
            self.reply_read.clear()
            conn.sendall(req[:4] + struct.pack('>HB', len(pdu[:5]) + 1, req[6]) + pdu[:5])
            self._rst(conn)

        port, requests = self._server(on_conn)
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertTrue(c.write_single_register(0, 1))
        self.reply_read.set()
        time.sleep(0.1)
        sends: list[Exception | None] = []
        real_send = c._send

        def spy_send(frame: bytes) -> None:
            try:
                real_send(frame)
            except Exception as e:
                sends.append(e)
                raise
            sends.append(None)

        with mock.patch.object(c, '_closed_by_peer', return_value=False), mock.patch.object(c, '_send', spy_send):
            ok = c.write_single_register(0, 2)
        if sends and sends[0] is not None:
            # the send failed (the usual case): the write never reached the server, sent again and executed once
            self.assertTrue(ok, c.last_error_as_txt)
            self.assertEqual(len(requests), 2)
        else:
            # the RST was not yet seen by the send (a race): the reset comes while waiting for the reply, the write
            # is not sent again (it fails), the server, closed, never read it
            self.assertFalse(ok)
            self.assertEqual(c.last_error, MB_RECV_ERR)
            self.assertEqual(len(requests), 1)

    def test_is_open(self) -> None:
        """is_open is False once the server has closed (FIN) or reset (RST) the connection, with no request."""
        for close in (lambda conn: conn.close(), self._rst):
            with self.subTest(close=close):
                port, _requests = self._server(self._reply_then(close))
                c = ModbusClient('127.0.0.1', port, timeout=2.0, auto_open=False)
                self.addCleanup(c.close)
                self.assertTrue(c.open())
                self.assertEqual(c.read_holding_registers(0), [42])
                self.reply_read.set()
                deadline = time.monotonic() + 2.0
                while c.is_open and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(c.is_open)
                # closed on the client side too: open() makes a new connection
                self.assertIsNone(c._sock)
                self.assertTrue(c.open())
                self.assertTrue(c.is_open)

    def test_no_reconnect_without_auto_open(self) -> None:
        """auto_open=False: the application manages the connection, no reconnect."""
        port, requests = self._server(self._reply_then(lambda conn: conn.close()))
        c = ModbusClient('127.0.0.1', port, timeout=2.0, auto_open=False)
        self.addCleanup(c.close)
        self.assertTrue(c.open())
        self.assertEqual(c.read_holding_registers(0), [42])
        time.sleep(0.1)
        self.assertIsNone(c.read_holding_registers(0))
        self.assertEqual(len(requests), 1)

    def test_late_data_is_not_a_close(self) -> None:
        """Bytes waiting on the connection are not taken for a close (the check only peeks)."""
        sent = threading.Event()

        def late_bytes(conn: socket.socket) -> None:
            conn.sendall(b'\x00\x01')
            sent.set()
            time.sleep(1.0)

        port, _requests = self._server(self._reply_then(late_bytes))
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0), [42])
        self.assertTrue(sent.wait(2.0))
        time.sleep(0.05)
        self.assertFalse(c._closed_by_peer())
        self.assertTrue(c.is_open)
        # the bytes are still there (only peeked)
        assert c._sock is not None
        self.assertEqual(c._sock.recv(16), b'\x00\x01')
