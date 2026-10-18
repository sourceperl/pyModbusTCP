""" Test of pyModbusTCP client-server interaction """

import socket
import unittest
from collections.abc import Callable
from random import choice, getrandbits, randint
from socket import IPPROTO_TCP, TCP_NODELAY
from string import ascii_letters
from typing import Any
from unittest.mock import Mock, patch

from pyModbusTCP.client import DeviceIdentificationResponse, ModbusClient
from pyModbusTCP.constants import (
    EXP_DATA_ADDRESS,
    EXP_DATA_VALUE,
    EXP_ILLEGAL_FUNCTION,
    EXP_NONE,
    EXP_SLAVE_DEVICE_FAILURE,
    MB_EXCEPT_ERR,
    MB_NO_ERR,
    MB_RECV_ERR,
    MB_SEND_ERR,
    MB_TIMEOUT_ERR,
    SUPPORTED_FUNCTION_CODES,
)
from pyModbusTCP.server import DeviceIdentification, ModbusServer

# some const
MAX_READABLE_REGS = 125
MAX_WRITABLE_REGS = 123
MAX_WRITE_READ_REGS = 121
MAX_READABLE_BITS = 2000
MAX_WRITABLE_BITS = 1968


class TestClientServer(unittest.TestCase):
    """ Client-server interaction test class. """

    def setUp(self) -> None:
        """Init client-server for test_xxx methods."""
        # modbus server: port=0 for a free TCP port chosen by the OS (never a fixed port of the host)
        self.server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
        host, port = self.server.start()
        # modbus client: connect to the port actually allocated to the server
        self.client = ModbusClient(host=host, port=port)
        self.client.open()

    def tearDown(self) -> None:
        """Cleaning after test."""
        self.client.close()
        self.server.stop()

    def test_client_is_open(self) -> None:
        """Test that the client's is_open property correctly reflects connection state."""
        # initial state: socket should be open
        self.assertTrue(self.client.is_open, "Client should be open at startup")
        # test manual close
        self.client.close()
        self.assertFalse(self.client.is_open, "Client should be closed after manual close")
        # test manual open
        self.client.open()
        self.assertTrue(self.client.is_open, "Client should be open after manual open")
        # test server side close
        self.server.stop()
        self.assertTrue(self.client.is_open, "Client should still report as open until I/O operation")
        # test I/O operation updates status
        self.client.read_coils(0)
        self.assertFalse(self.client.is_open, "Client should report as closed after I/O operation on closed server")

    def test_tcp_nodelay_sockopt(self) -> None:
        """On socket TCP_NODELAY option must reflect the no_delay mode."""
        # configure no_delay on and off and check getsockopt status
        for no_delay, expected_status in ((True, 1), (False, 0)):
            self.client.no_delay = no_delay
            # restart socket and apply no_delay
            self.client.open()
            # check _sock type
            if not isinstance(self.client._sock, socket.socket):
                raise TypeError("Expected self.client._sock to be socket")
            # enforce bool: macOS can return non-zero values other than 1 (e.g., 4) when enabled
            no_delay_status = bool(self.client._sock.getsockopt(IPPROTO_TCP, TCP_NODELAY))
            self.assertEqual(no_delay_status, expected_status)
            self.client.close()

    def test_listener_exception(self) -> None:
        """An exception raised by a change listener is logged: the write is committed, the client gets its response."""
        for value, exc in enumerate((RuntimeError('boom'), KeyError('key'), TypeError('type')), start=1):
            unsubscribe = self.server.store.subscribe(Mock(side_effect=exc))
            # use a new value at each loop: the listener is only called when the value change
            with self.assertLogs('pyModbusTCP.server', level='ERROR'):
                self.assertTrue(self.client.write_single_register(0, value))
            self.assertEqual(self.client.read_holding_registers(0), [value])
            self.assertTrue(self.client.is_open)
            unsubscribe()

    def test_handler_exception(self) -> None:
        """An exception raised by a function handler must not drop the session: SLAVE DEVICE FAILURE."""
        self.server.functions[0x41] = Mock(side_effect=RuntimeError('boom'))
        with self.assertLogs('pyModbusTCP.server', level='ERROR'):
            self.assertIsNone(self.client.custom_request(b'\x41\x00'))
        self.assertEqual(self.client.last_error, MB_EXCEPT_ERR)
        self.assertEqual(self.client.last_except, EXP_SLAVE_DEVICE_FAILURE)
        self.assertTrue(self.client.is_open, 'session must stay open after a device failure')
        del self.server.functions[0x41]
        self.assertTrue(self.client.write_single_register(0, 10))
        self.assertEqual(self.client.read_holding_registers(0), [10])

    def test_unknown_function_still_illegal(self) -> None:
        """A function code without handler still gives an ILLEGAL_FUNCTION exception."""
        self.assertIsNone(self.client.custom_request(b'\x55\x00'))
        self.assertEqual(self.client.last_except, EXP_ILLEGAL_FUNCTION)
        self.assertTrue(self.client.is_open)

    def test_late_reply_is_never_taken_for_the_next_one(self) -> None:
        """A reply left on the socket (on_tx_rx raised after the send) is detected by the next request."""
        self.server.store.app.holding_registers.write(0, [1])
        # the user callback raises an exception just after the send: the reply of the server stays on the socket
        self.client.on_tx_rx = Mock(side_effect=[RuntimeError('boom'), None, None, None])  # type: ignore
        # worst case for a random transaction ID: the same value drawn twice
        with patch('pyModbusTCP.client.modbus_client.random.randint', return_value=42):
            with self.assertRaises(RuntimeError):
                self.client.read_holding_registers(0)
            self.server.store.app.holding_registers.write(0, [2])
            # the next request receives the late reply (value 1): it must be rejected, not returned as the current one
            self.assertIsNone(self.client.read_holding_registers(0))
        self.assertEqual(self.client.last_error, MB_RECV_ERR)
        self.assertFalse(self.client.is_open)
        # the connection is reopened (auto_open) and in sync
        self.client.on_tx_rx = Mock()  # type: ignore
        self.assertEqual(self.client.read_holding_registers(0), [2])

    def test_request_with_short_send(self) -> None:
        """Complete requests when a socket send accepts only a prefix."""
        self.client.timeout = 1.0
        self.assertTrue(self.client.open())
        # check _sock type
        if not isinstance(self.client._sock, socket.socket):
            raise TypeError("Expected self.client._sock to be socket")
        sock = self.client._sock
        mock_sock = Mock(wraps=sock)
        mock_sock.send.side_effect = lambda data: sock.send(data[:3])
        self.client._sock = mock_sock
        self.assertEqual(self.client.read_holding_registers(0), [0])
        registers = list(range(MAX_WRITABLE_REGS))
        self.assertTrue(self.client.write_multiple_registers(0, registers))
        self.assertEqual(self.client.read_holding_registers(0, len(registers)), registers)

    def test_request_send_errors(self) -> None:
        """Send failures keep their error codes and close the connection."""
        for error, code in [(TimeoutError(), MB_TIMEOUT_ERR), (OSError(), MB_SEND_ERR)]:
            with self.subTest(error=type(error).__name__):
                self.assertTrue(self.client.open())
                sock = Mock(wraps=self.client._sock)
                sock.send.side_effect = error
                sock.sendall.side_effect = error
                self.client._sock = sock
                self.assertIsNone(self.client.read_holding_registers(0))
                self.assertEqual(self.client.last_error, code)
                self.assertFalse(self.client.is_open)
                self.assertEqual(sock.send.call_count + sock.sendall.call_count, 1)

    def test_default_startup_values(self) -> None:
        """Some read at random address to test startup values."""
        for addr in [randint(0, 0xffff) for _ in range(100)]:
            self.assertEqual(self.client.read_coils(addr), [False])
            self.assertEqual(self.client.read_discrete_inputs(addr), [False])
            self.assertEqual(self.client.read_holding_registers(addr), [0])
            self.assertEqual(self.client.read_input_registers(addr), [0])

    def test_read_write_requests(self) -> None:
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
            self.server.store.app.discrete_inputs.write(addr, [bit])
            self.assertEqual(self.client.read_discrete_inputs(addr), [bit])
            # discrete inputs space: multiple read/write at min size
            bits_l = [bool(getrandbits(1))]
            self.server.store.app.discrete_inputs.write(addr, bits_l)
            self.assertEqual(self.client.read_discrete_inputs(addr, len(bits_l)), bits_l)
            # discrete inputs space: multiple read/write at max size
            bits_l = [bool(getrandbits(1)) for _ in range(MAX_READABLE_BITS)]
            self.server.store.app.discrete_inputs.write(addr, bits_l)
            self.assertEqual(self.client.read_discrete_inputs(addr, len(bits_l)), bits_l)
            # discrete inputs space: multiple read/write at max size
            bits_l.append(bool(getrandbits(1)))
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
            self.server.store.app.input_registers.write(addr, [word])
            self.assertEqual(self.client.read_input_registers(addr), [word])
            # input registers space: multiple read/write at max size
            words_l = [randint(0, 0xffff) for _ in range(MAX_READABLE_REGS)]
            self.server.store.app.input_registers.write(addr, words_l)
            self.assertEqual(self.client.read_input_registers(addr, len(words_l)), words_l)
            # input registers space: multiple read/write over sized
            words_l.append(randint(0, 0xffff))
            self.assertRaises(ValueError, self.client.read_input_registers, addr, len(words_l))
        # input registers space: read/write over limit
        self.assertRaises(ValueError, self.client.read_input_registers, 0xfff0, 17)

    def test_server_strength(self) -> None:
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

    def test_server_read_identification(self) -> None:
        """Test server device identification function."""
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
        # forge a read identification request with a bad read device id code (return except 3)
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

    def test_client_read_identification(self) -> None:
        """Test client device identification function."""
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


class TestClientServerAccessControl(unittest.TestCase):
    """ Client behavior with a server that uses the read_only or allowed_hosts options. """

    def _start(self, **server_kwargs: Any) -> tuple[ModbusServer, ModbusClient]:
        """Start a server on a free port with some options and return it with a client connected to it."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **server_kwargs)
        host, port = server.start()
        self.addCleanup(server.stop)
        client = ModbusClient(host=host, port=port)
        self.addCleanup(client.close)
        return server, client

    def test_read_only(self) -> None:
        """A read_only server serves the reads and answers ILLEGAL FUNCTION to every write."""
        server, client = self._start(read_only=True)
        server.store.app.holding_registers.write(0, [42])
        self.assertEqual(client.read_holding_registers(0), [42])
        writes: dict[str, Callable[[], object]] = {
            'write_single_coil': lambda: client.write_single_coil(0, True),
            'write_single_register': lambda: client.write_single_register(0, 1),
            'write_multiple_coils': lambda: client.write_multiple_coils(0, [True, False]),
            'write_multiple_registers': lambda: client.write_multiple_registers(0, [1, 2]),
            'write_read_multiple_registers': lambda: client.write_read_multiple_registers(0, [1], 0, 1)}
        for name, write in writes.items():
            with self.subTest(name):
                self.assertFalse(write())
                self.assertEqual(client.last_error, MB_EXCEPT_ERR)
                self.assertEqual(client.last_except, EXP_ILLEGAL_FUNCTION)
                self.assertTrue(client.is_open, 'session must stay open after a refused write')
        # nothing is written, and the same session is still usable
        self.assertEqual(server.store.app.holding_registers.read(0, 1), [42])
        self.assertEqual(server.store.app.coils.read(0, 2), [False, False])
        self.assertEqual(client.read_holding_registers(0), [42])
        self.assertEqual(client.last_error, MB_NO_ERR)

    def test_allowed_hosts(self) -> None:
        """A client in the whitelist is served."""
        _server, client = self._start(allowed_hosts=['127.0.0.1'])
        self.assertTrue(client.write_single_register(0, 7))
        self.assertEqual(client.read_holding_registers(0), [7])

    def test_not_allowed_host(self) -> None:
        """A client out of the whitelist is disconnected: its requests fail."""
        server, client = self._start(allowed_hosts=['10.1.2.3'])
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertIsNone(client.read_holding_registers(0))
        self.assertNotEqual(client.last_error, MB_NO_ERR)
        self.assertFalse(client.is_open)
        self.assertEqual(server.active_sessions, [])


if __name__ == '__main__':
    unittest.main()
