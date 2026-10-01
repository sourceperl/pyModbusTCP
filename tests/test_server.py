""" Test of pyModbusTCP.ModbusServer """

import socket
import unittest

from pyModbusTCP.server import DeviceIdentification, ModbusServer


class TestModbusServer(unittest.TestCase):
    """ ModbusServer tests class. """

    def test_device_identification(self):
        """Some tests around modbus device identification."""
        # should raise exception
        self.assertRaises(TypeError, ModbusServer, device_id=object())
        # shouldn't raise exception
        try:
            ModbusServer(device_id=DeviceIdentification())
        except Exception as e:
            self.fail('ModbusServer raised exception "%r" unexpectedly' % e)
        # init a DeviceIdentification class for test it
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
        device_id = DeviceIdentification(
            product_name=b'server', objects_id={42: b'this'})
        self.assertEqual(repr(device_id), "DeviceIdentification(product_name=b'server', objects_id={42: b'this'})")

    def test_pdu_is_valid(self):
        """PDU min length is 2 bytes (function code + at least one data/except byte)."""
        self.assertFalse(ModbusServer.PDU(b'').is_valid)
        self.assertFalse(ModbusServer.PDU(b'\x03').is_valid)
        self.assertTrue(ModbusServer.PDU(b'\x83\x02').is_valid)

    def test_send_timeout_close_session(self):
        """A timeout on send can leave a partial frame on the wire: session must be closed."""
        class TimeoutSock:
            def sendall(self, _data):
                raise socket.timeout()

        class OkSock:
            def sendall(self, _data):
                pass

        # ModbusService __init__ would start handling a request, so bypass it (with __new__)
        service = ModbusServer.ModbusService.__new__(ModbusServer.ModbusService)
        # raise ModbusServer.NetworkError
        service.request = TimeoutSock()
        with self.assertRaises(ModbusServer.NetworkError):
            service._send_all(b'frame')
        # don't raise ModbusServer.NetworkError
        service.request = OkSock()
        try:
            service._send_all(b'frame')
        except ModbusServer.NetworkError:
            self.fail("NetworkError was unexpectedly raised!")

    def test_start_doesnt_alter_socketserver_classes(self):
        """ModbusServer must not change class attributes of the stdlib socketserver classes."""
        from socketserver import ThreadingTCPServer
        server = ModbusServer(host='127.0.0.1', port=5021, no_block=True)
        server.start()
        try:
            self.assertFalse(ThreadingTCPServer.daemon_threads)
            self.assertEqual(ThreadingTCPServer.address_family, socket.AF_INET)
        finally:
            server.stop()

    def test_failed_start_close_socket(self):
        """A failed start (bind error) must not leave a listening socket open."""
        # 192.0.2.1 is reserved for documentation (TEST-NET-1): not a local address, so bind() fails on all systems
        server = ModbusServer(host='192.0.2.1', port=5022, no_block=True)
        with self.assertRaises(ModbusServer.NetworkError):
            server.start()
        self.assertFalse(server.is_run)
        self.assertEqual(server._service.socket.fileno(), -1)


if __name__ == '__main__':
    unittest.main()
