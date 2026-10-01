""" Test of pyModbusTCP.ModbusClient """

import random
import socket
import unittest
from unittest import mock

from pyModbusTCP.client import ModbusClient, _decode_bits, _decode_regs
from pyModbusTCP.constants import MB_CONNECT_ERR


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
            self.assertEqual(_decode_bits(raw, nb), ref_bits)
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


if __name__ == '__main__':
    unittest.main()
