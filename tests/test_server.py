""" Test of pyModbusTCP.ModbusServer """

import signal
import socket
import struct
import subprocess
import sys
import time
import unittest
from unittest import mock

from pyModbusTCP.constants import EXP_NONE
from pyModbusTCP.server import DataBank, DataHandler, DeviceIdentification, ModbusServer
from pyModbusTCP.utils import _to_bool_list


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
            device_id['obj_name'] = 'anything'  # type: ignore
        with self.assertRaises(TypeError):
            device_id[0] = 42  # type: ignore
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
        # "in" test for an object id (never raise a KeyError)
        self.assertIn(0x80, device_id)
        self.assertIn(0x06, device_id)
        self.assertNotIn(0x07, device_id)
        self.assertNotIn(0x81, device_id)
        self.assertNotIn('vendor_name', device_id)
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

        # ModbusRequestHandler __init__ would start handling a request, so bypass it (with __new__)
        service = ModbusServer.ModbusRequestHandler.__new__(ModbusServer.ModbusRequestHandler)
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
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
        server.start()
        try:
            self.assertFalse(ThreadingTCPServer.daemon_threads)
            self.assertEqual(ThreadingTCPServer.address_family, socket.AF_INET)
        finally:
            server.stop()

    def test_failed_start_close_socket(self):
        """A failed start (bind error) must not leave a listening socket open."""
        # 192.0.2.1 is reserved for documentation (TEST-NET-1): not a local address, so bind() fails on all systems
        server = ModbusServer(host='192.0.2.1', port=0, no_block=True)
        with self.assertRaises(ModbusServer.NetworkError):
            server.start()
        self.assertFalse(server.is_running)


class TestModbusServerLimits(unittest.TestCase):
    """Tests of request_timeout, idle_timeout, max_connections and allowed_hosts."""

    # read 1 holding register at address 0: MBAP (transaction 1, unit 1) + PDU
    REQ_FRAME = b'\x00\x01\x00\x00\x00\x06\x01\x03\x00\x00\x00\x01'
    RESP_LEN = 11

    def _start_server(self, **kwargs):
        """Start a server on a free TCP port chosen by the OS (port=0), it's stored in self.port."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **kwargs)
        host, port = server.start()
        self.addCleanup(server.stop)
        self.port = port
        return server

    def _connect(self):
        """Open a new client connection to the server started by _start_server()."""
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
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
        self.assertEqual(server.idle_timeout, 300.0)
        self.assertEqual(server.max_connections, 64)
        self.assertEqual(server.max_connections_per_host, 16)

    def test_params_check(self):
        for kwargs in ({'request_timeout': 0}, {'request_timeout': -1}, {'idle_timeout': 0}, {'idle_timeout': -2.5},
                       {'max_connections': 0}, {'max_connections': -1}, {'max_connections_per_host': 0}):
            with self.assertRaises(ValueError, msg=repr(kwargs)):
                ModbusServer(**kwargs)  # type: ignore
        for kwargs in ({'request_timeout': '1'}, {'idle_timeout': True}, {'max_connections': 1.5},
                       {'max_connections': True}, {'max_connections': '2'}, {'max_connections_per_host': 1.0}):
            with self.assertRaises(TypeError, msg=repr(kwargs)):
                ModbusServer(**kwargs)  # type: ignore
        # None disable the limits
        server = ModbusServer(request_timeout=None, idle_timeout=None, max_connections=None,
                              max_connections_per_host=None)
        self.assertIsNone(server.request_timeout)
        self.assertIsNone(server.max_connections_per_host)

    def test_request_timeout(self):
        """A request that is never completed (partial MBAP header, or header without PDU) closes the session."""
        self._start_server(request_timeout=0.25)
        for sent_len in (3, 7):
            sock = self._connect()
            sock.sendall(self.REQ_FRAME[:sent_len])
            delay = self._closed_after(sock, 3.0)
            self.assertIsNotNone(delay, 'stalled request (%d bytes sent): session is still open' % sent_len)
            self.assertLess(delay, 2.0)  # type: ignore
        # server is still serving others
        self.assertEqual(len(self._request(self._connect())), self.RESP_LEN)

    def test_request_timeout_disabled(self):
        """With request_timeout=None a stalled request is never closed (old behavior)."""
        self._start_server(request_timeout=None)
        sock = self._connect()
        sock.sendall(self.REQ_FRAME[:7])
        self.assertIsNone(self._closed_after(sock, 0.7))

    def test_request_timeout_not_for_a_complete_request(self):
        """The request_timeout don't apply to a session that wait for a request (see idle_timeout)."""
        self._start_server(request_timeout=0.25)
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        self.assertIsNone(self._closed_after(sock, 0.7), 'request_timeout close an idle session')
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_idle_timeout(self):
        """A session without request is closed after idle_timeout, an active one is kept."""
        # keep a wide margin between the pause between requests (0.1 s) and idle_timeout: on a loaded CI runner
        # (macOS especially) a 0.1 s sleep plus a request round trip can take more than 0.25 s
        idle_timeout = 1.0
        self._start_server(idle_timeout=idle_timeout)
        sock = self._connect()
        for i in range(15):  # about 1.5 s of activity (more than idle_timeout)
            self.assertEqual(len(self._request(sock)), self.RESP_LEN, 'active session closed (request %d)' % i)
            time.sleep(0.1)
        delay = self._closed_after(sock, idle_timeout + 4.0)
        self.assertIsNotNone(delay, 'idle session is still open')
        self.assertLess(delay, idle_timeout + 3.0)  # type: ignore

    def test_idle_timeout_stops_when_request_starts(self):
        """idle_timeout is for a session without any data: a slow request is a matter for request_timeout."""
        self._start_server(idle_timeout=0.2)
        sock = self._connect()
        sock.sendall(self.REQ_FRAME[:3])
        time.sleep(0.5)  # more than idle_timeout
        sock.sendall(self.REQ_FRAME[3:])
        sock.settimeout(5)
        resp = sock.recv(self.RESP_LEN)
        self.assertEqual(len(resp), self.RESP_LEN)

    def test_connection_burst(self):
        """A burst of simultaneous connections must not wait for a TCP SYN retransmission (listen backlog)."""
        self._start_server()
        start = time.monotonic()
        for _ in range(60):
            self._connect()
        self.assertLess(time.monotonic() - start, 3.0)

    def test_no_idle_timeout_by_default(self):
        self._start_server()
        sock = self._connect()
        self.assertIsNone(self._closed_after(sock, 0.7))
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_max_connections(self):
        """Connections over max_connections are closed at once, slots are freed when a session end."""
        self._start_server(max_connections=2)
        sock_1, sock_2 = self._connect(), self._connect()
        self.assertEqual(len(self._request(sock_1)), self.RESP_LEN)
        self.assertEqual(len(self._request(sock_2)), self.RESP_LEN)
        # third connection is rejected
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            for _ in range(3):
                sock_3 = self._connect()
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
            sock_4 = self._connect()
            accepted = len(self._request(sock_4)) == self.RESP_LEN
            sock_4.close()
            time.sleep(0.05)
        self.assertTrue(accepted, 'slot of a closed session is never freed')
        self.assertEqual(len(self._request(sock_2)), self.RESP_LEN)

    def _connect_from(self, source_ip):
        """Open a client connection from another loopback address (skip the test if the OS doesn't allow it)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        try:
            sock.bind((source_ip, 0))
        except OSError:
            self.skipTest('loopback address %s not available on this machine' % source_ip)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', self.port))
        return sock

    def test_max_connections_per_host(self):
        """A host can't open more than max_connections_per_host connections, other hosts are still accepted."""
        server = self._start_server(max_connections=10, max_connections_per_host=2)
        sock_1, sock_2 = self._connect(), self._connect()
        self.assertEqual(len(self._request(sock_1)), self.RESP_LEN)
        self.assertEqual(len(self._request(sock_2)), self.RESP_LEN)
        # next connections of the same host are rejected, and logged once
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            for _ in range(3):
                sock_3 = self._connect()
                self.assertIsNotNone(self._closed_after(sock_3, 2.0), 'connection over the host limit is not closed')
        self.assertEqual(len(logs.records), 1)
        self.assertIn('per host', logs.records[0].getMessage())
        self.assertEqual(len(server.active_sessions), 2)
        # another host is accepted
        self.assertEqual(len(self._request(self._connect_from('127.0.0.2'))), self.RESP_LEN)
        # free a slot of the host: a new connection of this host is accepted
        sock_1.close()
        deadline = time.monotonic() + 5.0
        accepted = False
        while time.monotonic() < deadline and not accepted:
            sock_4 = self._connect()
            accepted = len(self._request(sock_4)) == self.RESP_LEN
            sock_4.close()
            time.sleep(0.05)
        self.assertTrue(accepted, 'slot of a closed session is never freed for its host')

    def test_max_connections_per_host_disabled(self):
        self._start_server(max_connections_per_host=None)
        socks = [self._connect() for _ in range(20)]
        for sock in socks:
            self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_host_key(self):
        """Connections are counted by IPv4 address, by /64 network for IPv6, an IPv4-mapped address is IPv4."""
        from pyModbusTCP.server._common import host_key
        self.assertEqual(host_key(('10.1.2.3', 502)), '10.1.2.3')
        self.assertEqual(host_key(('::ffff:10.1.2.3', 502, 0, 0)), '10.1.2.3')
        self.assertEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), host_key(('2001:db8:1:2:ffff::9', 1, 0, 0)))
        self.assertEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), '2001:db8:1:2::/64')
        self.assertNotEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), host_key(('2001:db8:1:3::1', 502, 0, 0)))
        self.assertEqual(host_key(('fe80::1%eth0', 502, 0, 2)), 'fe80::/64')

    def test_port_hijacking(self):
        """Another socket can't bind the port of a running server, even with SO_REUSEADDR (port hijacking)."""
        server = self._start_server()
        assert server._tcp_server is not None
        srv_sock = server._tcp_server.socket
        if sys.platform == 'win32':
            # Windows: SO_REUSEADDR would allow the hijacking, the port is reserved with SO_EXCLUSIVEADDRUSE
            self.assertEqual(srv_sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
            self.assertNotEqual(srv_sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 0)
        else:
            # POSIX: SO_REUSEADDR only allows a quick restart (connections of a previous server in TIME_WAIT)
            self.assertNotEqual(srv_sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
        for address in ('127.0.0.1', '0.0.0.0'):
            with self.subTest(address=address):
                thief = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.addCleanup(thief.close)
                thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with self.assertRaises(OSError):
                    thief.bind((address, self.port))
        # the server still gets the connections
        self.assertEqual(len(self._request(self._connect())), self.RESP_LEN)

    def test_quick_restart_on_the_same_port(self):
        """A server stopped after serving a client can be started again at once on the same port (POSIX)."""
        if sys.platform == 'win32':
            self.skipTest('no quick restart on Windows: the port is reserved with SO_EXCLUSIVEADDRUSE')
        server = self._start_server()
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        # the server closes the session first: its side of the connection goes to TIME_WAIT
        server.stop()
        sock.close()
        restarted = ModbusServer(host='127.0.0.1', port=self.port, no_block=True)
        restarted.start()
        self.addCleanup(restarted.stop)
        self.assertEqual(len(self._request(self._connect())), self.RESP_LEN)

    def test_allowed_hosts(self):
        """Only the clients of the allowed_hosts whitelist are served."""
        for hosts in (['127.0.0.1'], ['127.0.0.0/8'], ['10.1.2.3', '127.0.0.1'], ('10.0.0.0/8', '127.0.0.0/8')):
            with self.subTest(allowed_hosts=hosts):
                server = self._start_server(allowed_hosts=hosts)
                sock = self._connect()
                self.assertEqual(len(self._request(sock)), self.RESP_LEN)
                self.assertEqual(len(server.active_sessions), 1)
                server.stop()

    def test_allowed_hosts_refusal(self):
        """A client out of allowed_hosts is closed at once and never gets a session (the refusal is logged)."""
        server = self._start_server(allowed_hosts=['10.1.2.3', '192.168.0.0/16'])
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            for _ in range(3):
                sock = self._connect()
                self.assertIsNotNone(self._closed_after(sock, 2.0), 'host out of allowed_hosts is not closed')
        # a connection flood must not flood the log: only the first refusal is logged
        self.assertEqual(len(logs.records), 1)
        self.assertIn('127.0.0.1', logs.records[0].getMessage())
        self.assertEqual(server.active_sessions, [])
        self.assertTrue(server.is_running)

    def test_allowed_hosts_after_restart(self):
        """The whitelist is still applied after a stop/start."""
        server = self._start_server(allowed_hosts=['10.1.2.3'])
        for _ in range(2):
            with self.assertLogs('pyModbusTCP.server', level='WARNING'):
                sock = self._connect()
                self.assertIsNotNone(self._closed_after(sock, 2.0), 'host out of allowed_hosts is not closed')
            server.stop()
            self.port = server.start()[1]

    def test_allowed_hosts_with_external_engine(self):
        """The whitelist is checked before any engine: an external engine is never called for a refused client."""
        calls = []
        self._start_server(allowed_hosts=['10.1.2.3'], ext_engine=lambda session_data: calls.append(session_data))
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertIsNotNone(self._closed_after(self._connect(), 2.0), 'host out of allowed_hosts is not closed')
        self.assertEqual(calls, [])


class TestDataBankSetters(unittest.TestCase):
    """Tests of the DataBank set_xxx() methods."""

    class RecordingDataBank(DataBank):
        """A data bank that records the changes notified by the set_coils() and set_holding_registers() methods."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.changes = []

        def on_coils_change(self, address, from_value, to_value, srv_info):
            self.changes.append(('coil', address, from_value, to_value))

        def on_holding_registers_change(self, address, from_value, to_value, srv_info):
            self.changes.append(('reg', address, from_value, to_value))

    def test_set_coils(self):
        bank = TestDataBankSetters.RecordingDataBank()
        srv_info = ModbusServer.ServerInfo()
        # any true value: the data bank is called by the server
        self.assertTrue(bank.set_coils(10, [True, False, True, False, True]))
        self.assertEqual(bank.changes, [], 'no change notification without srv_info')
        # same values: no change
        self.assertTrue(bank.set_coils(10, [True, False, True, False, True], srv_info))
        self.assertEqual(bank.changes, [])
        # partial change: only the modified coils are notified, with their address and previous value
        self.assertTrue(bank.set_coils(10, [True, True, True, False, False], srv_info))
        self.assertEqual(bank.changes, [('coil', 11, False, True), ('coil', 14, True, False)])
        self.assertEqual(bank.get_coils(8, 9), [False, False, True, True, True, False, False, False, False])
        # items are converted to bool
        self.assertTrue(bank.set_coils(0, [1, 0, 1, 0]))  # type: ignore
        self.assertEqual(bank.get_coils(0, 4), [True, False, True, False])

    def test_set_holding_registers(self):
        bank = TestDataBankSetters.RecordingDataBank()
        srv_info = ModbusServer.ServerInfo()
        self.assertTrue(bank.set_holding_registers(100, [1, 2, 3, 4], srv_info))
        self.assertEqual(bank.changes, [('reg', 100, 0, 1), ('reg', 101, 0, 2), ('reg', 102, 0, 3), ('reg', 103, 0, 4)])
        bank.changes.clear()
        self.assertTrue(bank.set_holding_registers(100, [1, 2, 3, 4], srv_info))
        self.assertEqual(bank.changes, [])
        self.assertTrue(bank.set_holding_registers(101, [2, 30, 3], srv_info))
        self.assertEqual(bank.changes, [('reg', 102, 3, 30), ('reg', 103, 4, 3)])
        self.assertEqual(bank.get_holding_registers(99, 6), [0, 1, 2, 30, 3, 0])
        # items are converted to int, with a max size of 16 bits
        self.assertTrue(bank.set_holding_registers(0, [0xffff, True, 1]))  # type: ignore
        self.assertEqual(bank.get_holding_registers(0, 3), [0xffff, 1, 1])

    def test_set_discrete_inputs_and_input_registers(self):
        bank = DataBank()
        self.assertTrue(bank.set_discrete_inputs(5, [True, False, True]))
        self.assertEqual(bank.get_discrete_inputs(4, 5), [False, True, False, True, False])
        self.assertTrue(bank.set_input_registers(5, [7, 0x1, 9]))
        self.assertEqual(bank.get_input_registers(4, 5), [0, 7, 1, 9, 0])

    def test_set_out_of_range(self):
        """Out of range writes return None and must not modify anything (even partially)."""
        bank = TestDataBankSetters.RecordingDataBank()
        srv_info = ModbusServer.ServerInfo()
        self.assertTrue(bank.set_coils(0xffff, [True], srv_info))
        self.assertTrue(bank.set_holding_registers(0xffff, [5], srv_info))
        bank.changes.clear()
        self.assertIsNone(bank.set_coils(0xffff, [False, True], srv_info))
        self.assertIsNone(bank.set_coils(-1, [True], srv_info))
        self.assertIsNone(bank.set_holding_registers(0xffff, [1, 2], srv_info))
        self.assertIsNone(bank.set_holding_registers(-1, [1], srv_info))
        self.assertIsNone(bank.set_discrete_inputs(0xffff, [True, True]))
        self.assertIsNone(bank.set_input_registers(0xffff, [1, 2]))
        self.assertEqual(bank.changes, [])
        self.assertEqual(bank.get_coils(0xffff), [True])
        self.assertEqual(bank.get_holding_registers(0xffff), [5])
        self.assertEqual(bank.get_discrete_inputs(0xffff), [False])
        self.assertEqual(bank.get_input_registers(0xffff), [0])


class TestDataBankValidation(unittest.TestCase):
    def setUp(self):
        self.db = DataBank(coils_size=16, d_inputs_size=16, h_regs_size=16, i_regs_size=16)

    def test_empty_lists_are_a_noop(self):
        # an empty write is valid and must succeed, whatever the table
        self.assertIs(self.db.set_coils(0, []), True)
        self.assertIs(self.db.set_discrete_inputs(0, []), True)
        self.assertIs(self.db.set_holding_registers(0, []), True)
        self.assertIs(self.db.set_input_registers(0, []), True)

    def test_registers_range_and_type(self):
        for method in (self.db.set_holding_registers, self.db.set_input_registers):
            with self.subTest(method=method.__name__):
                self.assertIs(method(0, [0, 0xffff]), True)
                with self.assertRaises(ValueError):
                    method(0, [-1])
                with self.assertRaises(ValueError):
                    method(0, [0x10000])
                for bad_value in (1.0, '5', None):
                    with self.assertRaises(TypeError):
                        method(0, [bad_value])  # type: ignore

    def test_out_of_bank_returns_none(self):
        self.assertIsNone(self.db.set_holding_registers(14, [1, 2, 3]))
        self.assertIsNone(self.db.set_coils(15, [True, False]))
        self.assertIsNone(self.db.set_coils(-1, [True]))

    def test_bool_list_accepts_bool_like(self):
        self.assertEqual(_to_bool_list([True, 0, 2], 'x'), [True, False, True])
        self.assertEqual(_to_bool_list((1, 0), 'x'), [True, False])
        # one-shot iterators must not be lost by the validation fallback
        self.assertEqual(_to_bool_list(iter([1, 0, 1]), 'x'), [True, False, True])  # type: ignore
        for bad_value in (1.0, 'a', None):
            with self.assertRaises(TypeError):
                _to_bool_list([True, bad_value], 'x')

    def test_numpy_booleans_are_accepted(self):
        np = None
        try:
            import numpy as np
        except ImportError:
            self.skipTest('numpy not installed')
        self.assertIs(self.db.set_coils(0, np.array([True, False, True])), True)  # type: ignore
        self.assertEqual(self.db.get_coils(0, 3), [True, False, True])
        self.assertIs(self.db.set_discrete_inputs(0, [np.True_, np.False_]), True)  # type: ignore
        self.assertIs(self.db.set_holding_registers(0, np.array([1, 2], dtype=np.uint16)), True)  # type: ignore
        self.assertEqual(self.db.get_holding_registers(0, 2), [1, 2])
        # numpy floats are still rejected
        with self.assertRaises(TypeError):
            self.db.set_coils(0, np.array([1.0]))  # type: ignore


class TestModbusServerSpecFrames(unittest.TestCase):
    """The examples of the Modbus Application Protocol specification, on raw frames: an independent reference."""

    def setUp(self):
        self.bank = DataBank()
        # port=0: a free TCP port chosen by the OS
        self.server = ModbusServer(host='127.0.0.1', port=0, no_block=True, data_bank=self.bank)
        host, port = self.server.start()
        self.addCleanup(self.server.stop)
        self.port = port
        self.sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.addCleanup(self.sock.close)

    def _recv(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex):
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    @staticmethod
    def bits_of(data):
        return [bool(data[i // 8] >> i % 8 & 1) for i in range(len(data) * 8)]

    def test_read_coils(self):
        # coils 20 to 38 of the spec (address 19 to 37, 19 coils) are CD 6B 05
        self.bank.set_coils(19, self.bits_of(bytes.fromhex('cd6b05'))[:19])
        self.assertEqual(self.exchange('01 00 13 00 13'), '0103cd6b05')
        # same bits, but the quantity is not a multiple of 8 and padding bits must be zero
        self.bank.set_coils(19 + 19, [True] * 5)
        self.assertEqual(self.exchange('01 00 13 00 13'), '0103cd6b05')

    def test_read_discrete_inputs(self):
        # spec: inputs 197 to 218 (address 196 to 217, 22 inputs) are AC DB 35
        self.bank.set_discrete_inputs(196, self.bits_of(bytes.fromhex('acdb35'))[:22])
        self.assertEqual(self.exchange('02 00 c4 00 16'), '0203acdb35')

    def test_write_multiple_coils(self):
        # spec: write 10 coils from address 19: CD 01
        self.assertEqual(self.exchange('0f 00 13 00 0a 02 cd 01'), '0f0013000a')
        self.assertEqual(self.bank.get_coils(19, 10), self.bits_of(bytes.fromhex('cd01'))[:10])
        self.assertEqual(self.bank.get_coils(29, 1), [False], 'only 10 coils must be written')

    def test_read_holding_registers(self):
        # spec: read registers 108 to 110, values 022B 0000 0064
        self.bank.set_holding_registers(107, [0x022b, 0, 0x64])
        self.assertEqual(self.exchange('03 00 6b 00 03'), '030602 2b0000 0064'.replace(' ', ''))

    def test_write_multiple_registers(self):
        # spec: write 2 registers from address 1: 000A 0102
        self.assertEqual(self.exchange('10 00 01 00 02 04 00 0a 01 02'), '1000010002')
        self.assertEqual(self.bank.get_holding_registers(0, 4), [0, 0x000a, 0x0102, 0])

    def test_read_write_multiple_registers(self):
        # spec: read 6 registers from address 3 and write 3 registers (00FF) from address 14
        self.bank.set_holding_registers(3, [0x00fe, 0x0acd, 0x0001, 0x0003, 0x000d, 0x00ff])
        resp = self.exchange('17 00 03 00 06 00 0e 00 03 06 00 ff 00 ff 00 ff')
        self.assertEqual(resp, '170c00fe0acd00010003000d00ff')
        self.assertEqual(self.bank.get_holding_registers(13, 5), [0, 0xff, 0xff, 0xff, 0])

    def test_write_single_coil_values(self):
        # the only legal values are 0x0000 (off) and 0xFF00 (on), any other one is an ILLEGAL DATA VALUE (03)
        self.bank.set_coils(10, [True])
        for value in ('0001', '00ff', '8000', 'ff01', 'ffff'):
            self.assertEqual(self.exchange(f'05 000a {value}'), '8503', value)
            self.assertEqual(self.bank.get_coils(10, 1), [True], 'a refused request must not write')
        self.assertEqual(self.exchange('05 000a 0000'), '05000a0000')
        self.assertEqual(self.bank.get_coils(10, 1), [False])
        self.assertEqual(self.exchange('05 000a ff00'), '05000aff00')
        self.assertEqual(self.bank.get_coils(10, 1), [True])

    def test_read_write_multiple_registers_refused_request(self):
        # a refused request (exception response) must not write anything: here the read area is out of the table
        self.assertEqual(self.exchange('17 ffff 0002 0032 0001 02 0457'), '9702')
        self.assertEqual(self.bank.get_holding_registers(50, 1), [0])
        # quantities: read from 1 to 125 (0x7D), write from 1 to 121 (0x79)
        self.assertEqual(self.exchange('17 0000 007e 0000 0001 02 0007'), '9703')
        self.assertEqual(self.bank.get_holding_registers(0, 1), [0])
        resp = self.exchange('17 0000 007d 0000 0001 02 0007')
        self.assertEqual(resp[:4], '17fa')
        self.assertEqual(len(resp), 2 * (2 + 250))
        self.assertEqual(self.exchange('17 0000 0001 0000 0079 f2' + '0000' * 121), '17020000')

    def test_pdu_length(self):
        """Missing bytes or a byte count that doesn't match the quantity: ILLEGAL DATA VALUE (03)."""
        bad_requests = {
            'read coils, missing byte': '01 0000 00',
            'write single register, missing byte': '06 0000 12',
            'write multiple coils, byte count too high': '0f 0000 000a 03 ff 03 00',
            'write multiple coils, byte count too low': '0f 0000 000a 01 ff 03',
            'write multiple coils, missing byte': '0f 0000 000a 02 ff',
            'write multiple registers, byte count too high': '10 0000 0001 04 1234 5678',
            'write multiple registers, missing byte': '10 0000 0002 04 1234 56',
            'write/read multiple registers, missing byte': '17 0000 0001 0000 0002 04 1234',
            'read device identification, missing byte': '2b 0e 01',
        }
        for name, pdu in bad_requests.items():
            with self.subTest(name):
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}03')
        # nothing is written and the session is still in sync
        self.assertEqual(self.bank.get_coils(0, 16), [False] * 16)
        self.assertEqual(self.bank.get_holding_registers(0, 2), [0, 0])
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_extra_bytes_are_ignored(self):
        """Bytes after a complete and consistent request are ignored (the MBAP length keeps the stream in sync)."""
        self.bank.set_holding_registers(0, [0x1234])
        self.assertEqual(self.exchange('03 0000 0001 ffff'), '03021234')
        self.assertEqual(self.exchange('06 0001 abcd 00'), '060001abcd')
        self.assertEqual(self.exchange('0f 0010 000a 02 ff 03 ee'), '0f0010000a')
        self.assertEqual(self.bank.get_coils(16, 11), [True] * 10 + [False])
        self.assertEqual(self.exchange('10 0002 0001 02 5678 9abc'), '1000020001')
        self.assertEqual(self.exchange('17 0000 0003 0003 0001 02 0042 ff'), '17061234abcd5678')
        self.assertEqual(self.bank.get_holding_registers(0, 5), [0x1234, 0xabcd, 0x5678, 0x42, 0])

    def test_handler_ok_without_data(self):
        """A data handler that returns "ok" without data is a SLAVE DEVICE FAILURE (04), never an exception code 0."""
        with mock.patch.object(self.server.data_hdl, 'read_h_regs', return_value=DataHandler.Return(EXP_NONE)):
            for pdu in ('03 0000 0001', '17 0000 0001 0005 0001 02 1234'):
                with self.subTest(pdu), self.assertLogs('pyModbusTCP.server', level='ERROR'):
                    self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}04')
        # the write of function 0x17 is not done
        self.assertEqual(self.bank.get_holding_registers(5, 1), [0])

    def test_handler_bad_data_count(self):
        """The first read of function 0x17 must also check the number of registers returned by the data handler."""
        with mock.patch.object(self.server.data_hdl, 'read_h_regs', return_value=DataHandler.Return(EXP_NONE, [1])):
            with self.assertLogs('pyModbusTCP.server', level='ERROR'):
                self.assertEqual(self.exchange('17 0000 0002 0005 0001 02 1234'), '9704')
        self.assertEqual(self.bank.get_holding_registers(5, 1), [0])

    def test_unsupported_function_without_parameter(self):
        # a request can be a function code only (like 0x07, 0x0B, 0x0C or 0x11): ILLEGAL FUNCTION, not a closed session
        for func in (0x07, 0x0b, 0x0c, 0x11):
            self.assertEqual(self.exchange(f'{func:02x}'), f'{func | 0x80:02x}01')

    def _closed_by_server(self, frame):
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
            # a connection closed by the server may be reported as a reset (POSIX) or as an abort (WinError 10053
            # on Windows) instead of an end of stream: ConnectionError covers both (and a broken pipe on send)
            try:
                sock.sendall(frame)
                return sock.recv(16) == b''
            except ConnectionError:
                return True

    def test_malformed_requests_close_the_session(self):
        # no function code at all (MBAP length 1)
        self.assertTrue(self._closed_by_server(b'\x00\x01\x00\x00\x00\x01\x01'))


class TestReadDeviceIdentification(unittest.TestCase):
    """Read Device Identification (function 0x2B, MEI type 0x0E) on raw frames."""

    def setUp(self):
        # basic objects 0 to 2, regular objects 3 to 6 (empty by default), extended objects 0x80 and 0x82
        self.device_id = DeviceIdentification(vendor_name=b'v', product_code=b'p', major_minor_revision=b'1',
                                              objects_id={0x80: b'x', 0x82: b'z'})
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, device_id=self.device_id)
        host, port = server.start()
        self.addCleanup(server.stop)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.addCleanup(self.sock.close)

    def _recv(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex):
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    def objects(self, pdu_hex):
        """Send a request, check the response format and return (object ids, more follows, next object id)."""
        resp = bytes.fromhex(self.exchange(pdu_hex))
        self.assertEqual(resp[:3], bytes.fromhex(pdu_hex)[:3], 'not a regular response')
        ids = []
        offset = 7
        for _ in range(resp[6]):
            ids.append(resp[offset])
            offset += 2 + resp[offset + 1]
        self.assertEqual(offset, len(resp))
        return ids, resp[4], resp[5]

    def test_stream_access(self):
        self.assertEqual(self.objects('2b 0e 01 00'), ([0, 1, 2], 0, 0))
        self.assertEqual(self.objects('2b 0e 01 01'), ([1, 2], 0, 0))
        self.assertEqual(self.objects('2b 0e 02 03'), ([3, 4, 5, 6], 0, 0))
        self.assertEqual(self.objects('2b 0e 02 05'), ([5, 6], 0, 0))
        self.assertEqual(self.objects('2b 0e 03 80'), ([0x80, 0x82], 0, 0))
        self.assertEqual(self.objects('2b 0e 03 82'), ([0x82], 0, 0))

    def test_stream_access_restart(self):
        """An object id that isn't a known object of the requested category: the stream restarts at its first one."""
        for pdu, ids in (('2b 0e 01 05', [0, 1, 2]), ('2b 0e 01 ff', [0, 1, 2]),
                         ('2b 0e 02 00', [3, 4, 5, 6]), ('2b 0e 02 50', [3, 4, 5, 6]),
                         ('2b 0e 03 00', [0x80, 0x82]), ('2b 0e 03 81', [0x80, 0x82])):
            with self.subTest(pdu):
                self.assertEqual(self.objects(pdu), (ids, 0, 0))

    def test_stream_access_more_follows(self):
        """A stream that doesn't fit in a response is continued with the next object id given by the server."""
        self.device_id[0x81] = b'a' * 200
        self.device_id[0x82] = b'b' * 200
        self.assertEqual(self.objects('2b 0e 03 00'), ([0x80, 0x81], 0xff, 0x82))
        self.assertEqual(self.objects('2b 0e 03 82'), ([0x82], 0, 0))

    def test_individual_access(self):
        self.assertEqual(self.exchange('2b 0e 04 82'), '2b0e048300000182017a')
        self.assertEqual(self.objects('2b 0e 04 05'), ([5], 0, 0))
        # an unknown object is an ILLEGAL DATA ADDRESS (02)
        for obj_id in ('07', '7f', '81', 'ff'):
            with self.subTest(obj_id=obj_id):
                self.assertEqual(self.exchange(f'2b 0e 04 {obj_id}'), 'ab02')
        # the session is still in sync
        self.assertEqual(self.objects('2b 0e 04 00'), ([0], 0, 0))

    def test_bad_requests(self):
        # unknown ReadDevId code: ILLEGAL DATA VALUE (03)
        for code in ('00', '05', 'ff'):
            self.assertEqual(self.exchange(f'2b 0e {code} 00'), 'ab03')
        # another MEI type: ILLEGAL DATA ADDRESS (02), as before
        self.assertEqual(self.exchange('2b 0d 01 00'), 'ab02')


"""Test safe start/stop sequences."""


class TestStopFromSignalHandler(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'needs SIGALRM (not available on Windows)')
    def test_stop_from_signal_handler_in_blocking_mode(self):
        code = (
            'import signal\n'
            'from pyModbusTCP.server import ModbusServer\n'
            'srv = ModbusServer(host="127.0.0.1", port=0)\n'
            'signal.signal(signal.SIGALRM, lambda *_: srv.stop())\n'
            'signal.setitimer(signal.ITIMER_REAL, 0.3)\n'
            'srv.start()\n'
            'assert not srv.is_running\n'
        )
        # in a subprocess: a deadlock is a clean test failure (timeout), not a hanging test suite
        try:
            res = subprocess.run([sys.executable, '-c', code], timeout=15, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            self.fail('stop() called from a signal handler deadlocks')
        self.assertEqual(res.returncode, 0, res.stderr)


"""Tests for the ModbusServer `ipv6` option."""


def ipv6_available() -> bool:
    """True if this machine can really bind an IPv6 loopback socket."""
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(('::1', 0))
        return True
    except OSError:
        return False


def first_socket_family(**server_kwargs) -> int:
    """Start a server and return the family requested for the first socket it creates.

    start() may fail afterwards (no IPv6 on this machine, invalid address...): only the family matters here.
    """
    real_socket = socket.socket
    families = []

    def spy(family=-1, *args, **kwargs):
        families.append(family)
        return real_socket(family, *args, **kwargs)

    # never the default port (502) by accident: free port chosen by the OS unless told otherwise
    server_kwargs.setdefault('port', 0)
    server = ModbusServer(no_block=True, **server_kwargs)
    with mock.patch('socket.socket', side_effect=spy):
        try:
            server.start()
        except (ModbusServer.NetworkError, OSError):
            pass
    server.stop()
    return families[0]


class TestIPv6Option(unittest.TestCase):
    def test_ipv4_is_the_default_family(self):
        self.assertEqual(first_socket_family(host='127.0.0.1', port=0), socket.AF_INET)

    def test_ipv6_option_requests_an_ipv6_socket(self):
        # fails if address_family is set after TCPServer.__init__() (the socket is already AF_INET)
        self.assertEqual(first_socket_family(host='::1', port=0, ipv6=True), socket.AF_INET6)

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_ipv6_real_connection(self):
        server = ModbusServer(host='::1', port=0, ipv6=True, no_block=True)
        server.start()
        try:
            assert server._tcp_server is not None
            self.assertEqual(server._tcp_server.socket.family, socket.AF_INET6)
            host, port = server._tcp_server.server_address[:2]
            self.assertEqual(host, '::1')
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as c:
                c.settimeout(2)
                c.connect(('::1', port))
                # read 1 holding register at address 0: MBAP (tid 1, pid 0, len 6, unit 1) + FC 3
                c.sendall(struct.pack('>HHHBBHH', 1, 0, 6, 1, 3, 0, 1))
                resp = c.recv(64)
            self.assertEqual(resp, struct.pack('>HHHBBBH', 1, 0, 5, 1, 3, 2, 0))
        finally:
            server.stop()

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_ipv6_server_stop_and_restart(self):
        server = ModbusServer(host='::1', port=0, ipv6=True, no_block=True)
        for _ in range(3):
            server.start()
            self.assertTrue(server.is_running)
            assert server._tcp_server is not None
            self.assertEqual(server._tcp_server.socket.family, socket.AF_INET6)
            server.stop()
            self.assertFalse(server.is_running)

    def test_ipv4_real_connection(self):
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
        server.start()
        try:
            assert server._tcp_server is not None
            self.assertEqual(server._tcp_server.socket.family, socket.AF_INET)
            port = server._tcp_server.server_address[1]
            with socket.create_connection(('127.0.0.1', port), timeout=2) as c:
                c.sendall(struct.pack('>HHHBBHH', 1, 0, 6, 1, 3, 0, 1))
                resp = c.recv(64)
            self.assertEqual(resp, struct.pack('>HHHBBBH', 1, 0, 5, 1, 3, 2, 0))
        finally:
            server.stop()


"""Tests for the ModbusServer `read_only` option."""


class TestReadOnlyOption(unittest.TestCase):
    """The requests that write data (0x05, 0x06, 0x0F, 0x10 and 0x17) are refused with an ILLEGAL FUNCTION exception."""

    # request PDU (as hex string) of every function that writes data
    WRITE_REQUESTS = {
        'write single coil': '05 0001 ff00',
        'write single register': '06 0005 002a',
        'write multiple coils': '0f 0000 0002 01 03',
        'write multiple registers': '10 0000 0001 02 0007',
        'write/read multiple registers': '17 0000 0001 0000 0001 02 0007',
    }

    def _start_server(self, **kwargs):
        """Start a server on a free TCP port (port=0) and open a raw connection to it (self.sock)."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **kwargs)
        host, port = server.start()
        self.addCleanup(server.stop)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.addCleanup(self.sock.close)
        return server

    def _recv(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex):
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    def test_params_check(self):
        self.assertIs(ModbusServer().read_only, False)
        self.assertIs(ModbusServer(read_only=True).read_only, True)
        for value in (1, 0, 'yes', None, [True]):
            with self.assertRaises(TypeError, msg=repr(value)):
                ModbusServer(read_only=value)  # type: ignore
        self.assertIn('read_only=True', repr(ModbusServer(read_only=True)))
        self.assertIn('read_only=False', repr(ModbusServer()))

    def test_default_is_read_write(self):
        bank = TestDataBankSetters.RecordingDataBank()
        self._start_server(data_bank=bank)
        for name, pdu in self.WRITE_REQUESTS.items():
            self.assertEqual(self.exchange(pdu)[:2], pdu[:2], name)
        self.assertEqual(bank.get_holding_registers(5, 1), [0x2a])
        self.assertTrue(bank.changes, 'writes must be notified to the data bank')

    def test_read_functions_are_allowed(self):
        bank = DataBank()
        bank.set_holding_registers(0, [0x1234])
        bank.set_input_registers(0, [0x5678])
        bank.set_coils(0, [True] + [False] * 7)
        bank.set_discrete_inputs(1, [True])
        self._start_server(read_only=True, data_bank=bank)
        self.assertEqual(self.exchange('01 0000 0008'), '010101')
        self.assertEqual(self.exchange('02 0000 0008'), '020102')
        self.assertEqual(self.exchange('03 0000 0001'), '03021234')
        self.assertEqual(self.exchange('04 0000 0001'), '04025678')

    def test_write_functions_are_refused(self):
        bank = TestDataBankSetters.RecordingDataBank()
        self._start_server(read_only=True, data_bank=bank)
        for name, pdu in self.WRITE_REQUESTS.items():
            with self.subTest(name):
                # exception response: function code + 0x80, then the exception code 1 (ILLEGAL FUNCTION)
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}01')
        # nothing is written, nothing is notified
        self.assertEqual(bank.get_coils(0, 16), [False] * 16)
        self.assertEqual(bank.get_holding_registers(0, 16), [0] * 16)
        self.assertEqual(bank.changes, [])
        # the session is still open and in sync
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_server_side_writes_are_still_possible(self):
        """read_only is for the requests of the clients, not for the application that owns the data bank."""
        server = self._start_server(read_only=True)
        self.assertTrue(server.data_bank.set_holding_registers(0, [9]))
        self.assertEqual(self.exchange('03 0000 0001'), '03020009')

    def test_other_functions_are_not_concerned(self):
        self._start_server(read_only=True, device_id=DeviceIdentification(vendor_name=b'me'))
        # read device identification (individual access to VendorName)
        self.assertEqual(self.exchange('2b 0e 04 00'), '2b0e048300000100026d65')
        # an unknown function keeps its own exception
        self.assertEqual(self.exchange('55 00'), 'd501')

    def test_not_applied_to_an_external_engine(self):
        """With an ext_engine the requests are handled by user code: read_only has no effect on them."""
        received = []

        def engine(session_data):
            received.append(session_data.request.pdu.func_code)
            session_data.response.pdu.raw = session_data.request.pdu.raw

        self._start_server(read_only=True, ext_engine=engine)
        self.assertEqual(self.exchange('06 0005 002a'), '06 0005 002a'.replace(' ', ''))
        self.assertEqual(received, [0x06])

    def test_with_allowed_hosts(self):
        self._start_server(read_only=True, allowed_hosts=['127.0.0.1'])
        self.assertEqual(self.exchange('06 0005 002a'), '8601')
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')


"""Tests for the ModbusServer `allowed_hosts` option."""


class TestAllowedHostsOption(unittest.TestCase):
    """Check of the allowed_hosts parameter and match of the client addresses (without any connection)."""

    def _tcp_server(self, hosts):
        """Return a (not bound) TCP server of ModbusServer with a whitelist."""
        tcp_server = ModbusServer.CustomThreadingTCPServer(('127.0.0.1', 0), ModbusServer.ModbusRequestHandler,
                                                           bind_and_activate=False)
        self.addCleanup(tcp_server.server_close)
        tcp_server.allowed_networks = ModbusServer._validate_allowed_hosts(hosts)
        return tcp_server

    def test_default(self):
        server = ModbusServer()
        self.assertIsNone(server.allowed_hosts)
        self.assertIn('allowed_hosts=None', repr(server))

    def test_hosts_are_normalized(self):
        server = ModbusServer(allowed_hosts=['192.168.1.10', '10.0.0.0/24', '::1', 'fd00::/8', ' 127.0.0.1 '])
        self.assertEqual(server.allowed_hosts,
                         ['192.168.1.10/32', '10.0.0.0/24', '::1/128', 'fd00::/8', '127.0.0.1/32'])
        self.assertIn("allowed_hosts=['192.168.1.10/32'", repr(server))
        # host bits of a network are ignored
        self.assertEqual(ModbusServer(allowed_hosts=['192.168.1.5/24']).allowed_hosts, ['192.168.1.0/24'])

    def test_any_iterable_is_accepted(self):
        for hosts in (('127.0.0.1',), iter(['127.0.0.1']), {'127.0.0.1'}, (host for host in ['127.0.0.1'])):
            self.assertEqual(ModbusServer(allowed_hosts=hosts).allowed_hosts, ['127.0.0.1/32'])

    def test_params_check(self):
        # an empty whitelist is an error (None is for "no whitelist"), and hostnames are not supported
        for hosts in ([], (), set(), ['localhost'], ['example.com'], [''], ['999.1.1.1'], ['1.2.3'], ['10.0.0.0/33'],
                      ['::1/129'], ['10.0.0.0/a'], ['127.0.0.1', 'localhost']):
            with self.assertRaises(ValueError, msg=repr(hosts)):
                ModbusServer(allowed_hosts=hosts)
        # a lone string must not be read char by char, an int is a valid address for ipaddress but not here
        for hosts in ('127.0.0.1', b'127.0.0.1', '', 42, 1.5, [3232235777], [None], [1.5], [b'127.0.0.1'],
                      [('127.0.0.1',)]):
            with self.assertRaises(TypeError, msg=repr(hosts)):
                ModbusServer(allowed_hosts=hosts)  # type: ignore
        # the error message names the faulty item
        with self.assertRaises(ValueError) as ctx:
            ModbusServer(allowed_hosts=['127.0.0.1', 'localhost'])
        self.assertIn("'localhost'", str(ctx.exception))

    def test_no_whitelist_allows_everything(self):
        tcp_server = self._tcp_server(None)
        for address in ('127.0.0.1', '8.8.8.8', '::1', 'not an ip'):
            self.assertTrue(tcp_server.is_allowed((address, 1234)), address)

    def test_ipv4_match(self):
        tcp_server = self._tcp_server(['10.1.2.3', '192.168.0.0/16', '172.16.0.0/24'])
        for address, expected in (('10.1.2.3', True), ('10.1.2.4', False), ('192.168.0.1', True),
                                  ('192.168.255.254', True), ('192.169.0.1', False), ('127.0.0.1', False),
                                  ('172.15.255.255', False), ('172.16.0.0', True), ('172.16.0.255', True),
                                  ('172.16.1.0', False)):
            self.assertEqual(tcp_server.is_allowed((address, 1234)), expected, address)

    def test_ipv6_match(self):
        tcp_server = self._tcp_server(['::1', 'fd00::/8'])
        for address, expected in (('::1', True), ('::2', False), ('fd12:3456::1', True), ('fe80::1', False)):
            self.assertEqual(tcp_server.is_allowed((address, 1234, 0, 0)), expected, address)
        # the IP versions are never mixed
        self.assertFalse(tcp_server.is_allowed(('127.0.0.1', 1234)))
        self.assertFalse(self._tcp_server(['127.0.0.1']).is_allowed(('::1', 1234, 0, 0)))

    def test_ipv4_mapped_address(self):
        """An IPv4 client of a dual-stack IPv6 socket is seen as ::ffff:a.b.c.d."""
        tcp_server = self._tcp_server(['192.168.1.0/24'])
        self.assertTrue(tcp_server.is_allowed(('::ffff:192.168.1.7', 1234, 0, 0)))
        self.assertFalse(tcp_server.is_allowed(('::ffff:10.0.0.1', 1234, 0, 0)))

    def test_link_local_zone_id(self):
        self.assertTrue(self._tcp_server(['fe80::/10']).is_allowed(('fe80::1%eth0', 1234, 0, 0)))
        self.assertFalse(self._tcp_server(['::1']).is_allowed(('fe80::1%eth0', 1234, 0, 0)))

    def test_invalid_client_address_is_refused(self):
        tcp_server = self._tcp_server(['127.0.0.1'])
        for address in ('', 'not an ip', '1.2.3', '127.0.0.1\n'):
            self.assertFalse(tcp_server.is_allowed((address, 1234)), repr(address))


class TestAllowedHostsIPv6(unittest.TestCase):
    """Tests of allowed_hosts on a real IPv6 connection."""

    def _connect(self, **server_kwargs):
        server = ModbusServer(host='::1', port=0, ipv6=True, no_block=True, **server_kwargs)
        host, port = server.start()
        self.addCleanup(server.stop)
        sock = socket.create_connection(('::1', port), timeout=5)
        self.addCleanup(sock.close)
        # read 1 holding register at address 0: MBAP (tid 1, pid 0, len 6, unit 1) + FC 3
        # a refused client may see a reset (POSIX), an abort (WinError 10053 on Windows) or a broken pipe on send,
        # depending on the OS and on timing: ConnectionError covers them all, they all mean "closed by the server"
        try:
            sock.sendall(struct.pack('>HHHBBHH', 1, 0, 6, 1, 3, 0, 1))
            return sock.recv(64)
        except ConnectionError:
            return b''

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_listed_client_is_served(self):
        self.assertEqual(self._connect(allowed_hosts=['::1']), struct.pack('>HHHBBBH', 1, 0, 5, 1, 3, 2, 0))

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_unlisted_client_is_closed(self):
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._connect(allowed_hosts=['fd00::/8', '127.0.0.1']), b'')


if __name__ == '__main__':
    unittest.main()
