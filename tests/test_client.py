""" Test of pyModbusTCP.ModbusClient """

import random
import socket
import struct
import threading
import time
import unittest
from typing import Any, Tuple
from unittest import mock

from pyModbusTCP.client import ModbusClient, _decode_regs
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

    def test_host(self):
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

    def test_port(self):
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

    def test_unit_id(self):
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

    def test_timeout(self):
        """Test of timeout property."""
        # default value
        self.assertEqual(ModbusClient().timeout, 30.0)
        # an int is accepted and stored as a float
        self.assertEqual(ModbusClient(timeout=5).timeout, 5.0)
        self.assertIsInstance(ModbusClient(timeout=5).timeout, float)
        # bad types: the message is about timeout (not about another param)
        # bad values are typed Any: they are deliberately not of the annotated type
        bad_types: Tuple[Any, ...] = ('1', None, [1.0])
        for bad_type in bad_types:
            with self.assertRaisesRegex(TypeError, 'timeout'):
                ModbusClient(timeout=bad_type)
        # bad values: out of range or not finite
        for bad_value in (0, -1.0, 3600, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                ModbusClient(timeout=bad_value)

    def test_bool_is_not_an_int_param(self):
        """A bool is not accepted where a number is expected (port=True would silently be port 1)."""
        self.assertRaises(TypeError, ModbusClient, port=True)
        self.assertRaises(TypeError, ModbusClient, unit_id=False)
        self.assertRaises(TypeError, ModbusClient, timeout=True)
        c = ModbusClient()
        with self.assertRaises(TypeError):
            c.port = True
        with self.assertRaises(TypeError):
            c.unit_id = True

    def test_misc(self):
        """Check of misc default values."""
        self.assertEqual(ModbusClient().auto_open, True)
        self.assertEqual(ModbusClient().auto_close, False)

    def test_is_open(self):
        """Test of is_open property (no connection: must be closed)."""
        c = ModbusClient()
        self.assertFalse(c.is_open)
        c.close()
        self.assertFalse(c.is_open)

    def test_context_manager(self):
        """The with statement closes the connection on exit (also on an exception)."""
        port = self._start_fake_server(lambda req: req[:2] + b'\x00\x00\x00\x05\x01\x03\x02\x00\x2a')
        with ModbusClient('127.0.0.1', port, timeout=2.0) as c:
            self.assertEqual(c.read_holding_registers(0), [42])
            self.assertTrue(c.is_open)
        self.assertFalse(c.is_open)
        c2 = ModbusClient()
        with self.assertRaises(RuntimeError):
            with c2:
                raise RuntimeError('boom')
        self.assertFalse(c2.is_open)

    def test_params_type_check(self):
        """Non-integer params must raise TypeError (not struct.error), bad ranges ValueError."""
        c = ModbusClient()
        calls = [lambda v: c.read_coils(v, 1),
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

    def test_custom_request_params(self):
        """custom_request() rejects a PDU that can't be a modbus request (before any network I/O)."""
        c = ModbusClient()
        with mock.patch.object(c, '_req_pdu', side_effect=AssertionError('no request expected')):
            # bad values are typed Any: they are deliberately not of the annotated type
            bad_types: Tuple[Any, ...] = ('\x03\x00', [3, 0], None, 3)
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

    def test_transaction_id(self):
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

    def test_decode_helpers(self):
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

    def test_host_resolution_error(self):
        """A name resolution failure must be reported like any connect error (no socket.gaierror raised)."""
        c = ModbusClient('localhost', 502, auto_open=True)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertIsNone(c.read_coils(0))
            self.assertEqual(c.last_error, MB_CONNECT_ERR)
            self.assertFalse(c.is_open)

    def test_open_resets_last_error(self):
        """A successful open() clears the error of a previous failed open()."""
        port = self._start_fake_server(lambda req: b'')
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(-2, 'Name or service not known')):
            self.assertFalse(c.open())
        self.assertEqual(c.last_error, MB_CONNECT_ERR)
        self.assertTrue(c.open())
        self.assertEqual(c.last_error, MB_NO_ERR)

    def _start_fake_server(self, make_response):
        """Start a one-shot fake modbus server: one connection, one request, make_response(request) is sent back.

        :returns: the TCP port of the server
        """
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()

        def serve():
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
        return listener.getsockname()[1]

    def _start_slow_server(self, byte_delay):
        """Start a fake modbus server which answers "read 1 holding register" (value 42) one byte at a time."""
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()

        def serve():
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
        return listener.getsockname()[1]

    def test_timeout_is_for_the_whole_request(self):
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

    def test_split_response_within_timeout(self):
        """A response split in many TCP segments is still accepted if the whole request fit in timeout."""
        port = self._start_slow_server(byte_delay=0.02)  # 0.22 s for the complete response
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        self.assertEqual(c.read_holding_registers(0, 1), [42])

    def test_reply_function_code_mismatch(self):
        """A reply with another function code than the request is an error, even an exception reply."""
        replies = {
            # a valid "read input registers" reply to a "read holding registers" request
            'other function': b'\x00\x00\x00\x05\x01\x04\x02\x00\x2a',
            # an exception for another function
            'other exception': b'\x00\x00\x00\x03\x01\x84\x02',
        }
        for name, reply in replies.items():
            with self.subTest(name):
                port = self._start_fake_server(lambda req, reply=reply: req[:2] + reply)
                c = ModbusClient('127.0.0.1', port, timeout=2.0)
                self.addCleanup(c.close)
                self.assertIsNone(c.read_holding_registers(0))
                self.assertEqual(c.last_error, MB_RECV_ERR)
                self.assertEqual(c.last_except, EXP_NONE)

    def test_mbap_length_out_of_range(self):
        """An MBAP length field out of the modbus range (2 to 254) is an error and closes the connection."""
        for length in (0, 1, 255, 0xffff):
            with self.subTest(length=length):
                port = self._start_fake_server(
                    lambda req, length=length: req[:2] + struct.pack('>HHB', 0, length, 1) + b'\x03\x02\x00\x2a')
                c = ModbusClient('127.0.0.1', port, timeout=2.0)
                self.addCleanup(c.close)
                self.assertIsNone(c.read_holding_registers(0))
                self.assertEqual(c.last_error, MB_RECV_ERR)
                self.assertFalse(c.is_open)

    def test_modbus_exception_log(self):
        """The debug message of a modbus exception gives the exception name (not the error name)."""
        port = self._start_fake_server(lambda req: req[:2] + b'\x00\x00\x00\x03\x01\x83\x02')
        c = ModbusClient('127.0.0.1', port, timeout=2.0)
        self.addCleanup(c.close)
        with self.assertLogs('pyModbusTCP.client', level='DEBUG') as logs:
            self.assertIsNone(c.read_holding_registers(0))
        self.assertEqual(c.last_error, MB_EXCEPT_ERR)
        self.assertEqual(c.last_except, EXP_DATA_ADDRESS)
        self.assertTrue(any('illegal data address' in line for line in logs.output), logs.output)

    def test_read_device_identification_bad_reply(self):
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

    def test_write_multiple_pdu(self):
        """The PDU sent by write_multiple_coils()/write_multiple_registers() must keep the modbus format."""
        rnd = random.Random(99)
        c = ModbusClient()
        sent = []

        def fake_req_pdu(tx_pdu, rx_min_len):
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
                self.assertEqual(sent[-1], struct.pack('>BHHB', 0x10, 20, nb, 2 * nb) + struct.pack('>%dH' % nb, *regs))
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

    def test_write_multiple_reply_mismatch(self):
        """
        A reply that doesn't echo the address/quantity of the request is an error (last_error), not a silent False.
        """
        def reply(addr_delta, qty_delta):
            def fake_req_pdu(tx_pdu, rx_min_len):
                addr, qty = struct.unpack('>HH', tx_pdu[1:5])
                return struct.pack('>BHH', tx_pdu[0], addr + addr_delta, qty + qty_delta)
            return fake_req_pdu

        calls = {'write_multiple_coils': lambda c: c.write_multiple_coils(10, [True, False, True]),
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
