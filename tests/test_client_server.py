import unittest
from random import randint, getrandbits
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.client import ModbusClient


# some const
MAX_READABLE_REGS = 125
MAX_WRITABLE_REGS = 123
MAX_READABLE_BITS = 2000
MAX_WRITABLE_BITS = 1968


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
        self.server.stop()

    def test_coils_space(self):
        #### coils ####
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_WRITABLE_BITS]:
            # coils space: default value at startup
            self.assertEqual(self.client.read_coils(addr), [False], 'Default value is False when server start')
            # coils space: single read/write
            bit = bool(getrandbits(1))
            self.assertEqual(self.client.write_single_coil(addr, bit), True)
            self.assertEqual(self.client.read_coils(addr), [bit])
            # coils space: multiple read/write at min size
            bits_l = [bool(getrandbits(1))]
            self.assertEqual(self.client.write_multiple_coils(addr, bits_l), True)
            self.assertEqual(self.client.read_coils(addr, len(bits_l)), bits_l)
            # coils space: multiple read/write at max size
            bits_l = [bool(getrandbits(1)) for _ in range(MAX_WRITABLE_BITS)]
            self.assertEqual(self.client.write_multiple_coils(addr, bits_l), True)
            self.assertEqual(self.client.read_coils(addr, len(bits_l)), bits_l)
            # coils space: multi-write over sized
            bits_l = [bool(getrandbits(1)) for _ in range(MAX_WRITABLE_BITS + 1)]
            self.assertEqual(self.client.write_multiple_coils(addr, bits_l), None)
        # coils space: read/write over limit
        self.assertEqual(self.client.read_coils(0xfffe, 3), None)
        self.assertEqual(self.client.write_single_coil(0x10000, 0), None)
        self.assertEqual(self.client.write_multiple_coils(0xfff0, [0] * 17), None)
        #### discrete inputs ####
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_READABLE_BITS]:
            # discrete inputs space: default value at startup
            self.assertEqual(self.client.read_discrete_inputs(addr), [False], 'Default value is False when server start')
            # discrete inputs space: single read/write
            bit = bool(getrandbits(1))
            self.server.data_bank.set_discrete_inputs(addr, [bit])
            self.assertEqual(self.client.read_discrete_inputs(addr), [bit])
            # discrete inputs space: multiple read/write at min size
            bits_l = [bool(getrandbits(1))]
            self.server.data_bank.set_discrete_inputs(addr, bits_l)
            self.assertEqual(self.client.read_discrete_inputs(addr, len(bits_l)), bits_l)
            # discrete inputs space: multiple read/write at max size
            bits_l = [bool(getrandbits(1)) for _ in range(MAX_READABLE_BITS)]
            self.server.data_bank.set_discrete_inputs(addr, bits_l)
            self.assertEqual(self.client.read_discrete_inputs(addr, len(bits_l)), bits_l)
            # discrete inputs space: multiple read/write at max size
            bits_l = [bool(getrandbits(1)) for _ in range(MAX_READABLE_BITS + 1)]
            self.server.data_bank.set_discrete_inputs(addr, bits_l)
            self.assertEqual(self.client.read_discrete_inputs(addr, len(bits_l)), None)
        # discrete inputs space: read/write over limit
        self.assertEqual(self.client.read_discrete_inputs(0xffff, 2), None)
        #### holding registers ####
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_WRITABLE_REGS]:
            # holding registers space: default value at startup
            self.assertEqual(self.client.read_holding_registers(addr), [0], 'Default value is 0 when server start')
            # holding registers space: single read/write
            word = randint(0, 0xffff)
            self.assertEqual(self.client.write_single_register(addr, word), True)
            self.assertEqual(self.client.read_holding_registers(addr), [word])
            # holding registers space: multi-write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_WRITABLE_REGS)]
            self.assertEqual(self.client.write_multiple_registers(addr, words_l), True)
            self.assertEqual(self.client.read_holding_registers(addr, len(words_l)), words_l)
            # holding registers space: write over sized
            words_l = [randint(0, 0xffff) for _ in range(MAX_WRITABLE_REGS + 1)]
            self.assertEqual(self.client.write_multiple_registers(addr, words_l), None)
        # holding registers space: read/write over limit
        self.assertEqual(self.client.read_holding_registers(0xfff0, 17), None)
        self.assertEqual(self.client.write_single_register(0x10000, 0), None)
        self.assertEqual(self.client.write_multiple_registers(0xfff0, [0] * 17), None)
        #### input registers ####
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_READABLE_REGS]:
            # input registers space: default value at startup
            self.assertEqual(self.client.read_input_registers(addr), [0], 'Default value is 0 when server start')
            # input registers space: single read/write
            word = randint(0, 0xffff)
            self.server.data_bank.set_input_registers(addr, [word])
            self.assertEqual(self.client.read_input_registers(addr), [word])
            # input registers space: multiple read/write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_READABLE_REGS)]
            self.server.data_bank.set_input_registers(addr, words_l)
            self.assertEqual(self.client.read_input_registers(addr, len(words_l)), words_l)
            # input registers space: multiple read/write over sized
            words_l = [randint(0, 0xffff) for _ in range(MAX_READABLE_REGS + 1)]
            self.server.data_bank.set_input_registers(addr, words_l)
            self.assertEqual(self.client.read_input_registers(addr, len(words_l)), None)
        # input registers space: read/write over limit
        self.assertEqual(self.client.read_input_registers(0xfff0, 17), None)

if __name__ == '__main__':
    unittest.main()
