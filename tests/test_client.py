""" Test of pyModbusTCP.ModbusClient """

import random
import socket
import struct
import threading
import time
import unittest
from unittest import mock

from pyModbusTCP.client import ModbusClient, _decode_regs
from pyModbusTCP.constants import MB_CONNECT_ERR, MB_TIMEOUT_ERR
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
            ModbusClient(port=5020)
        except ValueError:
            self.fail('ModbusClient.port property raised ValueError unexpectedly')

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
        # invalid items never reach the network
        with mock.patch.object(c, '_req_pdu', side_effect=AssertionError('no request expected')):
            self.assertRaises(ValueError, c.write_multiple_registers, 0, [1, -1])
            self.assertRaises(ValueError, c.write_multiple_registers, 0, [1, 0x10000])
            self.assertRaises(TypeError, c.write_multiple_registers, 0, [1, 2.0])
            self.assertRaises(TypeError, c.write_multiple_registers, 0, [1, '2'])
            self.assertRaises(TypeError, c.write_read_multiple_registers, 0, [1, None], 0)
            self.assertRaises(ValueError, c.write_read_multiple_registers, 0, [1, -1], 0)


if __name__ == '__main__':
    unittest.main()
