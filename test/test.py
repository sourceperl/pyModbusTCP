import unittest
from pyModbusTCP.client import ModbusClient

class TestModbusClient(unittest.TestCase):

    def test_except_init_host(self):
        # should raise an exception for bad hostname
        self.assertRaises(ValueError, ModbusClient, host="wrong@host")

    def test_except_init_port(self):
        # should raise an exception for bad port
        self.assertRaises(ValueError, ModbusClient, port=-1)

    def test_host(self):
        # test valid/invalid cases for host()
        c = ModbusClient()
        self.assertEqual(c.host(), "localhost", "default host is localhost")
        self.assertEqual(c.host("wrong@host"), None)
        self.assertEqual(c.host("my.good.host"), "my.good.host")
        self.assertEqual(c.host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(c.host("::1"), "::1")

    def test_port(self):
        # test valid/invalid cases for port()
        c = ModbusClient()
        self.assertEqual(c.port(), 502, "default modbus/TCP port is 502")
        self.assertEqual(c.port(-1), None)
        self.assertEqual(c.port(42), 42)

    def test_debug(self):
        # test valid/invalid cases for debug()
        c = ModbusClient()
        self.assertEqual(c.debug(), False, "debug default is off")
        self.assertEqual(c.debug(False), False)
        self.assertEqual(c.debug(True), True)

