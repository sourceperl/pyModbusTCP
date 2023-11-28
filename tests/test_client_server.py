""" Test of pyModbusTCP client-server interaction """

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


class TestClientServer(unittest.TestCase):
    """ Client-server interaction test class. """

    def setUp(self):
        """Init client-server for test_xxx methods."""
        # modbus server
        self.server = ModbusServer(port=5020, no_block=True)
        self.server.start()
        # modbus client
        self.client = ModbusClient(port=5020)
        self.client.open()

    def tearDown(self):
        """Cleanning after test."""
        self.client.close()
        self.server.stop()

    def test_default_startup_values(self):
        """Some read at random address to test startup values."""
        for addr in [randint(0, 0xffff) for _ in range(100)]:
            self.assertEqual(self.client.read_coils(addr), [False])
            self.assertEqual(self.client.read_discrete_inputs(addr), [False])
            self.assertEqual(self.client.read_holding_registers(addr), [0])
            self.assertEqual(self.client.read_input_registers(addr), [0])

    def test_read_write_requests(self):
        """Test standard modbus functions."""
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
        """Test server responses to abnormal events."""
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
        """Test server device indentification function."""
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
        """Test client device indentification function."""
        # configure server
        vendor_name = ''.join(choice(ascii_letters) for _ in range(16)).encode()
        product_code = ''.join(choice(ascii_letters) for _ in range(32)).encode()
        maj_min_rev = b'v2.0'
        vendor_url = b'https://github.com/sourceperl/pyModbusTCP'
        self.server.device_id = DeviceIdentification(vendor_name=vendor_name, product_code=product_code,
                                                     major_minor_revision=maj_min_rev, vendor_url=vendor_url)
        # read_device_identification: read basic device identification (stream access)
        dev_id_resp = self.client.read_device_identification()
        if not dev_id_resp:
            self.fail('ModbusClient.read_device_identification() method failed unexpectedly')
        else:
            # return DeviceIdentificationResponse on success
            self.assertEqual(isinstance(dev_id_resp, DeviceIdentificationResponse), True)
            # check read data
            self.assertEqual(len(dev_id_resp.objects_by_id), 3)
            self.assertEqual(dev_id_resp.vendor_name, vendor_name)
            self.assertEqual(dev_id_resp.objects_by_id.get(0), vendor_name)
            self.assertEqual(dev_id_resp.product_code, product_code)
            self.assertEqual(dev_id_resp.objects_by_id.get(1), product_code)
            self.assertEqual(dev_id_resp.major_minor_revision, maj_min_rev)
            self.assertEqual(dev_id_resp.objects_by_id.get(2), maj_min_rev)
            self.assertEqual(dev_id_resp.vendor_url, None)
            self.assertEqual(dev_id_resp.product_name, None)
            self.assertEqual(dev_id_resp.model_name, None)
            self.assertEqual(dev_id_resp.user_application_name, None)
        # read_device_identification: read one specific identification object (individual access)
        dev_id_resp = self.client.read_device_identification(read_code=4, object_id=3)
        if not dev_id_resp:
            self.fail('ModbusClient.read_device_identification() method failed unexpectedly')
        else:
            # return DeviceIdentificationResponse on success
            self.assertEqual(isinstance(dev_id_resp, DeviceIdentificationResponse), True)
            # check read data
            self.assertEqual(len(dev_id_resp.objects_by_id), 1)
            self.assertEqual(dev_id_resp.vendor_url, vendor_url)
            self.assertEqual(dev_id_resp.objects_by_id.get(3), vendor_url)
        # restore default configuration
        self.server.device_id = None


if __name__ == '__main__':
    unittest.main()
