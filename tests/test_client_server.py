# -*- coding: utf-8 -*-

import unittest
from random import randint, getrandbits
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.client import ModbusClient


class TestModbusClient(unittest.TestCase):
    def test_except_init_host(self):
        # should raise an exception for bad hostname
        self.assertRaises(ValueError, ModbusClient, host='wrong@host')

    def test_except_init_port(self):
        # should raise an exception for bad port
        self.assertRaises(ValueError, ModbusClient, port=-1)

    def test_except_unit_id(self):
        # should raise an exception for bad unit_id
        self.assertRaises(ValueError, ModbusClient, unit_id=420)

    def test_host(self):
        # test valid/invalid cases for host()
        c = ModbusClient()
        self.assertEqual(c.host(), 'localhost', 'default host is localhost')
        self.assertEqual(c.host('wrong@host'), None)
        self.assertEqual(c.host('my.good.host'), 'my.good.host')
        self.assertEqual(c.host('127.0.0.1'), '127.0.0.1')
        self.assertEqual(c.host('::1'), '::1')

    def test_port(self):
        # test valid/invalid cases for port()
        c = ModbusClient()
        self.assertEqual(c.port(), 502, 'default modbus/TCP port is 502')
        self.assertEqual(c.port(-1), None)
        self.assertEqual(c.port(42), 42)

    def test_debug(self):
        # test valid/invalid cases for debug()
        c = ModbusClient()
        self.assertEqual(c.debug(), False, 'debug default is off')
        self.assertEqual(c.debug(False), False)
        self.assertEqual(c.debug(True), True)

    def test_unit_id(self):
        # test valid/invalid cases for debug()
        c = ModbusClient()
        self.assertEqual(c.unit_id(), 1, 'default unit_id is 1')
        self.assertEqual(c.unit_id(42), 42)
        self.assertEqual(c.unit_id(0), 0)
        self.assertEqual(c.unit_id(420), None)


# TODO improve this basic test
class TestClientServer(unittest.TestCase):

    def setUp(self):
        # modbus server
        self.server = ModbusServer(port=5020, no_block=True)
        self.server.start()
        # modbus client
        self.client = ModbusClient(port=5020)
        self.client.open()

    def tearDown(self):
        self.client.close()

    def test_read_and_write(self):
        # word space
        self.assertEqual(self.client.read_holding_registers(0), [0], 'Default value is 0 when server start')
        self.assertEqual(self.client.read_input_registers(0), [0], 'Default value is 0 when server start')
        # single read/write
        self.assertEqual(self.client.write_single_register(0, 0xffff), True)
        self.assertEqual(self.client.read_input_registers(0), [0xffff])
        # multi-write at max size
        words_l = [randint(0, 0xffff)] * 0x7b
        self.assertEqual(self.client.write_multiple_registers(0, words_l), True)
        self.assertEqual(self.client.read_holding_registers(0, len(words_l)), words_l)
        self.assertEqual(self.client.read_input_registers(0, len(words_l)), words_l)
        # write over sized
        words_l = [randint(0, 0xffff)] * 0x7c
        self.assertEqual(self.client.write_multiple_registers(0, words_l), None)
        # bit space
        self.assertEqual(self.client.read_coils(0), [False], 'Default value is False when server start')
        self.assertEqual(self.client.read_discrete_inputs(0), [False], 'Default value is False when server start')
        # single read/write
        self.assertEqual(self.client.write_single_coil(0, True), True)
        self.assertEqual(self.client.read_coils(0), [True])
        self.assertEqual(self.client.read_discrete_inputs(0), [True])
        # multi-write at min size
        bits_l = [getrandbits(1)] * 0x1
        self.assertEqual(self.client.write_multiple_coils(0, bits_l), True)
        self.assertEqual(self.client.read_coils(0, len(bits_l)), bits_l)
        self.assertEqual(self.client.read_discrete_inputs(0, len(bits_l)), bits_l)
        # multi-write at max size
        bits_l = [getrandbits(1)] * 0x7b0
        self.assertEqual(self.client.write_multiple_coils(0, bits_l), True)
        self.assertEqual(self.client.read_coils(0, len(bits_l)), bits_l)
        self.assertEqual(self.client.read_discrete_inputs(0, len(bits_l)), bits_l)
        # multi-write over sized
        bits_l = [getrandbits(1)] * 0x7b1
        self.assertEqual(self.client.write_multiple_coils(0, bits_l), None)


if __name__ == '__main__':
    unittest.main()
