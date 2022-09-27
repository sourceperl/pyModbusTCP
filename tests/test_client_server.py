import unittest
from random import randint, getrandbits
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.constants import SUPPORTED_FUNCTION_CODES, EXP_NONE, EXP_ILLEGAL_FUNCTION, MB_NO_ERR, MB_EXCEPT_ERR


# some const
MAX_READABLE_REGS = 125
MAX_WRITABLE_REGS = 123
MAX_READABLE_BITS = 2000
MAX_WRITABLE_BITS = 1968


class TestModbusClient(unittest.TestCase):
    def test_host(self):
        # default value
        self.assertEqual(ModbusClient().host, 'localhost')
        # should raise ValueError for bad value
        self.assertRaises(ValueError, ModbusClient, host='wrong@host')
        self.assertRaises(ValueError, ModbusClient, host='192.168.2.bad')
        self.assertRaises(ValueError, ModbusClient, host='::notip:1')
        # shouldn't raise ValueError for valid value
        try:
            [ModbusClient(host=h) for h in ['CamelCaseHost', 'my.good.host', '127.0.0.1', '::1']]
        except ValueError:
            self.fail('ModbusClient.host property raised ValueError unexpectedly')

    def test_port(self):
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
        # misc default values
        self.assertEqual(ModbusClient().debug, False)
        self.assertEqual(ModbusClient().auto_open, True)
        self.assertEqual(ModbusClient().auto_close, False)


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

    def test_default_startup_values(self):
        # some read at random address to test startup values
        for addr in [randint(0, 0xffff) for _ in range(100)]:
            self.assertEqual(self.client.read_coils(addr), [False])
            self.assertEqual(self.client.read_discrete_inputs(addr), [False])
            self.assertEqual(self.client.read_holding_registers(addr), [0])
            self.assertEqual(self.client.read_input_registers(addr), [0])

    def test_read_write_requests(self):
        # coils
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_WRITABLE_BITS]:
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
            bits_l.append(bool(getrandbits(1)))
            self.assertRaises(ValueError, self.client.write_multiple_coils, addr, bits_l)
        # coils space: read/write over limit
        self.assertRaises(ValueError, self.client.read_coils, 0xfffe, 3)
        self.assertRaises(ValueError, self.client.write_single_coil, 0x10000, False)
        self.assertRaises(ValueError, self.client.write_multiple_coils, 0xfff0, [False] * 17)

        # discrete inputs
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_READABLE_BITS]:
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
            bits_l.append(bool(getrandbits(1)))
            self.server.data_bank.set_discrete_inputs(addr, bits_l)
            self.assertRaises(ValueError, self.client.read_discrete_inputs, addr, len(bits_l))
        # discrete inputs space: read/write over limit
        self.assertRaises(ValueError, self.client.read_discrete_inputs, 0xffff, 2)

        # holding registers
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_WRITABLE_REGS]:
            # holding registers space: single read/write
            word = randint(0, 0xffff)
            self.assertEqual(self.client.write_single_register(addr, word), True)
            self.assertEqual(self.client.read_holding_registers(addr), [word])
            # holding registers space: multi-write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_WRITABLE_REGS)]
            self.assertEqual(self.client.write_multiple_registers(addr, words_l), True)
            self.assertEqual(self.client.read_holding_registers(addr, len(words_l)), words_l)
        # holding registers space: read/write over limit
        self.assertRaises(ValueError, self.client.read_holding_registers, 0xfff0, 17)
        self.assertRaises(ValueError, self.client.write_single_register, 0, 0x10000)
        self.assertRaises(ValueError, self.client.write_single_register, 0x10000, 0)
        self.assertRaises(ValueError, self.client.write_multiple_registers, 0x1000, [0x10000])
        self.assertRaises(ValueError, self.client.write_multiple_registers, 0xfff0, [0] * 17)

        # input registers
        for addr in [0x0000, 0x1234, 0x2345, 0x10000 - MAX_READABLE_REGS]:
            # input registers space: single read/write
            word = randint(0, 0xffff)
            self.server.data_bank.set_input_registers(addr, [word])
            self.assertEqual(self.client.read_input_registers(addr), [word])
            # input registers space: multiple read/write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_READABLE_REGS)]
            self.server.data_bank.set_input_registers(addr, words_l)
            self.assertEqual(self.client.read_input_registers(addr, len(words_l)), words_l)
            # input registers space: multiple read/write over sized
            words_l.append(randint(0, 0xffff))
            self.server.data_bank.set_input_registers(addr, words_l)
            self.assertRaises(ValueError, self.client.read_input_registers, addr, len(words_l))
        # input registers space: read/write over limit
        self.assertRaises(ValueError, self.client.read_input_registers, 0xfff0, 17)

    def test_server_strength(self):
        # test server responses to abnormal events
        # unsupported function codes must return except EXP_ILLEGAL_FUNCTION
        for func_code in range(0x80):
            if func_code not in SUPPORTED_FUNCTION_CODES:
                # test with a min PDU length of 2 bytes (avoid short frame error)
                self.assertEqual(self.client.custom_request(bytes([func_code, 0x00])), None)
                self.assertEqual(self.client.last_error, MB_EXCEPT_ERR)
                self.assertEqual(self.client.last_except, EXP_ILLEGAL_FUNCTION)
        # check a regular request status: no error, no except
        self.assertEqual(self.client.read_coils(0), [False])
        self.assertEqual(self.client.last_error, MB_NO_ERR)
        self.assertEqual(self.client.last_except, EXP_NONE)


if __name__ == '__main__':
    unittest.main()
