""" Test of pyModbusTCP.ModbusServer """

import socket
import time
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


class TestModbusServerLimits(unittest.TestCase):
    """Tests of request_timeout, idle_timeout and max_connections."""

    # read 1 holding register at address 0: MBAP (transaction 1, unit 1) + PDU
    REQ_FRAME = b'\x00\x01\x00\x00\x00\x06\x01\x03\x00\x00\x00\x01'
    RESP_LEN = 11

    def _start_server(self, port, **kwargs):
        server = ModbusServer(host='127.0.0.1', port=port, no_block=True, **kwargs)
        server.start()
        self.addCleanup(server.stop)
        return server

    def _connect(self, port):
        sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def _closed_after(self, sock, max_wait):
        """Return the delay (in s) before the server close the connection, None if it is still open."""
        sock.settimeout(max_wait)
        start = time.monotonic()
        try:
            data = sock.recv(1)
        except socket.timeout:
            return None
        except ConnectionError:
            data = b''
        return time.monotonic() - start if data == b'' else None

    def _request(self, sock):
        """Send a read request and return the response (b'' if the connection is closed)."""
        sock.settimeout(5)
        try:
            sock.sendall(self.REQ_FRAME)
            resp = b''
            while len(resp) < self.RESP_LEN:
                chunk = sock.recv(self.RESP_LEN - len(resp))
                if not chunk:
                    break
                resp += chunk
            return resp
        except ConnectionError:
            return b''

    def test_defaults(self):
        server = ModbusServer()
        self.assertEqual(server.request_timeout, 30.0)
        self.assertIsNone(server.idle_timeout)
        self.assertIsNone(server.max_connections)

    def test_params_check(self):
        for kwargs in ({'request_timeout': 0}, {'request_timeout': -1}, {'idle_timeout': 0}, {'idle_timeout': -2.5},
                       {'max_connections': 0}, {'max_connections': -1}):
            with self.assertRaises(ValueError, msg=repr(kwargs)):
                ModbusServer(**kwargs)
        for kwargs in ({'request_timeout': '1'}, {'idle_timeout': True}, {'max_connections': 1.5},
                       {'max_connections': True}, {'max_connections': '2'}):
            with self.assertRaises(TypeError, msg=repr(kwargs)):
                ModbusServer(**kwargs)
        # None disable the limits
        server = ModbusServer(request_timeout=None, idle_timeout=None, max_connections=None)
        self.assertIsNone(server.request_timeout)

    def test_request_timeout(self):
        """A request that is never completed (partial MBAP header, or header without PDU) closes the session."""
        self._start_server(5031, request_timeout=0.25)
        for sent_len in (3, 7):
            sock = self._connect(5031)
            sock.sendall(self.REQ_FRAME[:sent_len])
            delay = self._closed_after(sock, 3.0)
            self.assertIsNotNone(delay, 'stalled request (%d bytes sent): session is still open' % sent_len)
            self.assertLess(delay, 2.0)
        # server is still serving others
        self.assertEqual(len(self._request(self._connect(5031))), self.RESP_LEN)

    def test_request_timeout_disabled(self):
        """With request_timeout=None a stalled request is never closed (old behavior)."""
        self._start_server(5036, request_timeout=None)
        sock = self._connect(5036)
        sock.sendall(self.REQ_FRAME[:7])
        self.assertIsNone(self._closed_after(sock, 0.7))

    def test_request_timeout_not_for_a_complete_request(self):
        """The request_timeout don't apply to a session that wait for a request (see idle_timeout)."""
        self._start_server(5032, request_timeout=0.25)
        sock = self._connect(5032)
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        self.assertIsNone(self._closed_after(sock, 0.7), 'request_timeout close an idle session')
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_idle_timeout(self):
        """A session without request is closed after idle_timeout, an active one is kept."""
        self._start_server(5033, idle_timeout=0.25)
        sock = self._connect(5033)
        for _ in range(5):  # 0.5 s of activity (more than idle_timeout)
            self.assertEqual(len(self._request(sock)), self.RESP_LEN)
            time.sleep(0.1)
        delay = self._closed_after(sock, 3.0)
        self.assertIsNotNone(delay, 'idle session is still open')
        self.assertLess(delay, 2.0)

    def test_idle_timeout_stops_when_request_starts(self):
        """idle_timeout is for a session without any data: a slow request is a matter for request_timeout."""
        self._start_server(5037, idle_timeout=0.2)
        sock = self._connect(5037)
        sock.sendall(self.REQ_FRAME[:3])
        time.sleep(0.5)  # more than idle_timeout
        sock.sendall(self.REQ_FRAME[3:])
        sock.settimeout(5)
        resp = sock.recv(self.RESP_LEN)
        self.assertEqual(len(resp), self.RESP_LEN)

    def test_no_idle_timeout_by_default(self):
        self._start_server(5034)
        sock = self._connect(5034)
        self.assertIsNone(self._closed_after(sock, 0.7))
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_max_connections(self):
        """Connections over max_connections are closed at once, slots are freed when a session end."""
        self._start_server(5035, max_connections=2)
        sock_1, sock_2 = self._connect(5035), self._connect(5035)
        self.assertEqual(len(self._request(sock_1)), self.RESP_LEN)
        self.assertEqual(len(self._request(sock_2)), self.RESP_LEN)
        # third connection is rejected
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            for _ in range(3):
                sock_3 = self._connect(5035)
                self.assertIsNotNone(self._closed_after(sock_3, 2.0), 'connection over max_connections is not closed')
        # a connection flood must not flood the log: only the first rejection is logged
        self.assertEqual(len(logs.records), 1)
        # existing sessions are not impacted
        self.assertEqual(len(self._request(sock_1)), self.RESP_LEN)
        # free a slot: a new connection is accepted (the session end is detected by the server thread)
        sock_1.close()
        deadline = time.monotonic() + 5.0
        accepted = False
        while time.monotonic() < deadline and not accepted:
            sock_4 = self._connect(5035)
            accepted = len(self._request(sock_4)) == self.RESP_LEN
            sock_4.close()
            time.sleep(0.05)
        self.assertTrue(accepted, 'slot of a closed session is never freed')
        self.assertEqual(len(self._request(sock_2)), self.RESP_LEN)


if __name__ == '__main__':
    unittest.main()
