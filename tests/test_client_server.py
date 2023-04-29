import unittest
from random import randint, getrandbits, choice
from string import ascii_letters
from pyModbusTCP.server import ModbusServer, DeviceIdentification
from pyModbusTCP.client import ModbusClient, DeviceIdentificationResponse
from pyModbusTCP.constants import SUPPORTED_FUNCTION_CODES, \
    EXP_NONE, EXP_ILLEGAL_FUNCTION, EXP_DATA_ADDRESS, EXP_DATA_VALUE, MB_NO_ERR, MB_EXCEPT_ERR


# some const
MAX_READABLE_REGS = 125
MAX_WRITABLE_REGS = 123
MAX_WRITE_READ_REGS = 121
MAX_READABLE_BITS = 2000
MAX_WRITABLE_BITS = 1968


class TestModbusClient(unittest.TestCase):
    def test_host(self):
        # default value
        self.assertEqual(ModbusClient().host, 'localhost')
        # should raise ValueError for bad value
        self.assertRaises(ValueError, ModbusClient, host='wrong@host')
        self.assertRaises(ValueError, ModbusClient, host='my.bad_name.host')
        self.assertRaises(ValueError, ModbusClient, host='::notip:1')
        # shouldn't raise ValueError for valid value
        try:
            [ModbusClient(host=h) for h in ['CamelCaseHost', 'plc-1.net', 'my.good.host',
                                            '42.example.com', '127.0.0.1', '::1']]
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
        # default values
        self.assertEqual(ModbusClient().debug, False)
        self.assertEqual(ModbusClient().auto_open, True)
        self.assertEqual(ModbusClient().auto_close, False)


class TestModbusServer(unittest.TestCase):
    def test_modbus_server(self):
        # should raise exception
        self.assertRaises(TypeError, ModbusServer, device_id=object())
        # shouldn't raise exception
        try:
            ModbusServer(device_id=DeviceIdentification())
        except Exception as e:
            self.fail('ModbusServer raised exception "%r" unexpectedly' % e)

    def test_device_identification_class(self):
        device_id = DeviceIdentification()
        # should raise exception
        with self.assertRaises(TypeError):
            device_id['obj_name'] = 'anything'
        with self.assertRaises(TypeError):
            device_id[0] = 42
        # shouldn't raise exception
        try:
            device_id.vendor_name = b'me'
            device_id.user_application_name = b'unittest'
            device_id[0x80] = b'feed'
        except Exception as e:
            self.fail('DeviceIdentification raised exception "%r" unexpectedly' % e)
        # check access by shortcut name (str) or object id (int) return same value
        self.assertEqual(device_id.vendor_name, device_id[0x00])
        self.assertEqual(device_id.user_application_name, device_id[0x06])
        # test __repr__
        device_id = DeviceIdentification(product_name=b'server', objects_id={42: b'this'})
        self.assertEqual(repr(device_id), "DeviceIdentification(product_name=b'server', objects_id={42: b'this'})")


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
            # coils space: oversized multi-write
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
            # holding registers space: multi-write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_WRITE_READ_REGS)]
            self.assertEqual(self.client.write_read_multiple_registers(addr, words_l, addr, len(words_l)), words_l)
            self.assertEqual(self.client.read_holding_registers(addr, len(words_l)), words_l)
        # holding registers space: read/write over limit
        self.assertRaises(ValueError, self.client.read_holding_registers, 0xfff0, 17)
        self.assertRaises(ValueError, self.client.write_single_register, 0, 0x10000)
        self.assertRaises(ValueError, self.client.write_single_register, 0x10000, 0)
        self.assertRaises(ValueError, self.client.write_multiple_registers, 0x1000, [0x10000])
        self.assertRaises(ValueError, self.client.write_multiple_registers, 0xfff0, [0] * 17)
        self.assertRaises(ValueError, self.client.write_read_multiple_registers, 0x1000, [0x10000], 0x1000, 1)
        self.assertRaises(ValueError, self.client.write_read_multiple_registers, 0xfff0, [0] * 17, 0xfff0, 1)
        self.assertRaises(ValueError, self.client.write_read_multiple_registers, 0xfff0, [0] * 1, 0xfff0, 17)

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

    def test_server_read_identification(self):
        # forge a basic read identification on unconfigured server (return a data address except)
        self.assertEqual(self.client.custom_request(b'\x2b\x0e\x01\x00'), None)
        self.assertEqual(self.client.last_error, MB_EXCEPT_ERR)
        self.assertEqual(self.client.last_except, EXP_DATA_ADDRESS)
        # configure server
        self.server.device_id = DeviceIdentification()
        self.server.device_id.vendor_name = b'me'
        self.server.device_id[0x80] = b'\xc0\xde'
        # forge a basic read identification on a configured server (return a valid pdu)
        self.assertNotEqual(self.client.custom_request(b'\x2b\x0e\x01\x00'), None)
        # forge a read identifaction request with a bad read device id code (return except 3)
        self.assertEqual(self.client.custom_request(b'\x2b\x0e\x05\x00'), None)
        self.assertEqual(self.client.last_error, MB_EXCEPT_ERR)
        self.assertEqual(self.client.last_except, EXP_DATA_VALUE)
        # read VendorName str object(id #0) with individual access (read device id = 4)
        ret_pdu = self.client.custom_request(b'\x2b\x0e\x04\x00')
        self.assertEqual(ret_pdu, b'\x2b\x0e\x04\x83\x00\x00\x01\x00\x02me')
        # read private int object(id #0x80) with individual access (read device id = 4)
        ret_pdu = self.client.custom_request(b'\x2b\x0e\x04\x80')
        self.assertEqual(ret_pdu, b'\x2b\x0e\x04\x83\x00\x00\x01\x80\x02\xc0\xde')
        # restore default configuration
        self.server.device_id = None

    def test_client_read_identification(self):
        # configure server
        name = ''.join(choice(ascii_letters) for _ in range(16)).encode()
        p_code = ''.join(choice(ascii_letters) for _ in range(32)).encode()
        rev = b'v2.0'
        url = b'https://github.com/sourceperl/pyModbusTCP'
        self.server.device_id = DeviceIdentification(vendor_name=name, product_code=p_code,
                                                     major_minor_revision=rev, vendor_url=url)
        # read_device_identification: read basic device identification (stream access)
        req = self.client.read_device_identification()
        if not req:
            self.fail('ModbusClient.read_device_identification() method failed unexpectedly')
        else:
            # return DeviceIdentificationRequest on success
            self.assertEqual(isinstance(req, DeviceIdentificationResponse), True)
            # check read data
            self.assertEqual(len(req.objs_by_id), 3)
            self.assertEqual(req.objs_by_id.get(0), name)
            self.assertEqual(req.objs_by_id.get(1), p_code)
            self.assertEqual(req.objs_by_id.get(2), rev)
        # read_device_identification: read one specific identification object (individual access)
        req = self.client.read_device_identification(read_id=4, object_id=3)
        if not req:
            self.fail('ModbusClient.read_device_identification() method failed unexpectedly')
        else:
            # return DeviceIdentificationRequest on success
            self.assertEqual(isinstance(req, DeviceIdentificationResponse), True)
            # check read data
            self.assertEqual(len(req.objs_by_id), 1)
            self.assertEqual(req.objs_by_id.get(3), url)
        # restore default configuration
        self.server.device_id = None


if __name__ == '__main__':
    unittest.main()
