""" Test of pyModbusTCP.ModbusServer """

import gc
import ipaddress
import logging
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import warnings
from collections.abc import Iterable
from typing import Any, ClassVar, cast
from unittest import mock

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import (
    AccessControl,
    ChangeEvent,
    DeviceIdentification,
    MemoryStore,
    ModbusServer,
    Request,
    RequestContext,
    Space,
)
from pyModbusTCP.server._common import LogThrottle


class TestModbusServer(unittest.TestCase):
    """ ModbusServer tests class. """

    def test_device_identification(self) -> None:
        """Some tests around modbus device identification."""
        # should raise exception
        self.assertRaises(TypeError, ModbusServer, device_id=object())
        # shouldn't raise exception
        ModbusServer(device_id=DeviceIdentification())
        # init a DeviceIdentification class for test it
        device_id = DeviceIdentification()
        # should raise exception
        with self.assertRaises(TypeError):
            device_id['obj_name'] = 'anything'  # type: ignore
        with self.assertRaises(TypeError):
            device_id[0] = 42  # type: ignore
        # shouldn't raise exception
        device_id.vendor_name = b'me'
        device_id.user_application_name = b'unittest'
        device_id[0x80] = b'feed'
        # check access by shortcut name (str) or object id (int) return same value
        self.assertEqual(device_id.vendor_name, device_id[0x00])
        self.assertEqual(device_id.user_application_name, device_id[0x06])
        # "in" test for an object id (never raise a KeyError)
        self.assertIn(0x80, device_id)
        self.assertIn(0x06, device_id)
        self.assertNotIn(0x07, device_id)
        self.assertNotIn(0x81, device_id)
        self.assertNotIn('vendor_name', device_id)
        # an object id is never a bool (True was the object 1)
        for key in (True, False):
            with self.assertRaises(TypeError):
                device_id[key] = b'x'
            with self.assertRaises(TypeError):
                device_id[key]
            self.assertNotIn(key, device_id)
        # conformity level: the categories of the published objects (0x80: individual access supported)
        self.assertEqual(device_id.conformity_level, 0x83)
        self.assertEqual(DeviceIdentification().conformity_level, 0x82)
        self.assertEqual(DeviceIdentification(objects_by_id={0x7F: b'r'}).conformity_level, 0x82)
        self.assertEqual(DeviceIdentification(objects_by_id={0xFF: b'x'}).conformity_level, 0x83)
        # test __repr__
        device_id = DeviceIdentification(
            product_name=b'server', objects_by_id={42: b'this'})
        self.assertEqual(repr(device_id), "DeviceIdentification(product_name=b'server', objects_by_id={42: b'this'})")
        # objects_id: the deprecated name of objects_by_id (the name of DeviceIdentificationResponse on the client side)
        with self.assertWarns(DeprecationWarning):
            device_id = DeviceIdentification(objects_id={0x80: b'old'})
        self.assertEqual(device_id[0x80], b'old')
        with self.assertRaises(TypeError), warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            DeviceIdentification(objects_by_id={0x80: b'new'}, objects_id={0x80: b'old'})

    def test_pdu_is_valid(self) -> None:
        """PDU min length is 2 bytes (function code + at least one data/except byte)."""
        self.assertFalse(ModbusServer.PDU(b'').is_valid)
        self.assertFalse(ModbusServer.PDU(b'\x03').is_valid)
        self.assertTrue(ModbusServer.PDU(b'\x83\x02').is_valid)

    def test_send_timeout_close_session(self) -> None:
        """A timeout on send can leave a partial frame on the wire: session must be closed."""
        class TimeoutSock:
            def sendall(self, _data: bytes) -> None:
                raise TimeoutError()

        class OkSock:
            def sendall(self, _data: bytes) -> None:
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

    def test_start_doesnt_alter_socketserver_classes(self) -> None:
        """ModbusServer must not change class attributes of the stdlib socketserver classes."""
        from socketserver import ThreadingTCPServer
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
        server.start()
        try:
            self.assertFalse(ThreadingTCPServer.daemon_threads)
            self.assertEqual(ThreadingTCPServer.address_family, socket.AF_INET)
        finally:
            server.stop()

    def test_failed_start_close_socket(self) -> None:
        """A failed start (bind error) must not leave a listening socket open."""
        # 192.0.2.1 is reserved for documentation (TEST-NET-1): not a local address, so bind() fails on all systems
        server = ModbusServer(host='192.0.2.1', port=0, no_block=True)
        with self.assertRaises(ModbusServer.NetworkError):
            server.start()
        self.assertFalse(server.is_running)

    def test_host_and_port_check(self) -> None:
        """host and port are checked by the constructor (a bad type or value was only seen by start())."""
        # bad values are typed Any: they are deliberately not of the annotated type
        bad_types: tuple[dict[str, Any], ...] = (
            {'port': '502'}, {'port': 1.5}, {'port': True}, {'port': None}, {'host': None}, {'host': b'localhost'})
        for kwargs in bad_types:
            with self.subTest(kwargs), self.assertRaises(TypeError):
                ModbusServer(**kwargs)
        for port in (-1, 65536, 70000):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ModbusServer(port=port)
        # an empty host would listen on every interface (the wildcard address of bind()): refused (S-07)
        for host in ('', ' '):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "use '0.0.0.0'"):
                ModbusServer(host=host)
        # a legacy IPv4 form that bind() reads as another address: refused (S-08), "0" would be every interface
        for host in ('0', '00', '0x0', '0.0', '127.1', '0x7f.1', '2130706433', '192.168.010.1', '127.000.000.001',
                     '999.1.1.1', 'wrong@host', 'bad\x00host', '::notip:1', 'fe80::1%', 'fe80::1%eth 0',
                     '127.0.0.1%eth0'):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'standard form'):
                ModbusServer(host=host)
        # the wildcard address in another form than 0.0.0.0 or :: (like its IPv4-mapped form, every IPv4 interface on
        # a dual-stack socket): refused, on every Python release (IPv6Address.is_unspecified isn't used)
        for host in ('::ffff:0.0.0.0', '::ffff:0:0', '::FFFF:0.0.0.0', '::0', '0::0', '0:0:0:0:0:0:0:0', '::0.0.0.0'):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'wildcard address'):
                ModbusServer(host=host)
        # the standard forms, the wildcard addresses written on purpose, the hostnames, a link-local IPv6 address
        for host in ('127.0.0.1', '0.0.0.0', '::', '::1', 'localhost', 'plc-gw.local', 'fe80::1%eth0', 'fe80::1%2',
                     '::ffff:127.0.0.1'):
            with self.subTest(host=host):
                self.assertEqual(ModbusServer(host=host).host, host)
        # a link-local IPv6 address needs its interface (it tells the link to listen on), an interface on another
        # address is a configuration error
        with self.assertRaisesRegex(ValueError, 'link-local IPv6 address: give its interface'):
            ModbusServer(host='fe80::1')
        for host in ('2001:db8::1%eth0', '::1%lo', 'fe80::1%' + 'a' * 16):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'standard form'):
                ModbusServer(host=host)
        server = ModbusServer(host='127.0.0.1')
        for host in ('', '0'):
            with self.assertRaises(ValueError):
                server.host = host
        self.assertEqual(server.host, '127.0.0.1')
        server.host = '0.0.0.0'
        self.assertEqual(server.host, '0.0.0.0')
        self.assertEqual(ModbusServer(port=0).port, 0)
        self.assertEqual(ModbusServer(port=65535).port, 65535)

    def test_failed_start_with_invalid_port_close_socket(self) -> None:
        """A port made invalid after the constructor: start() raises and doesn't leave the listening socket open."""
        for port, exc in ((70000, OverflowError), ('abc', TypeError)):
            server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
            server.port = port  # type: ignore
            with self.subTest(port=port), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                with self.assertRaises(exc):
                    server.start()
                gc.collect()
                self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])
            self.assertFalse(server.is_running)


class _LiveServerTestCase(unittest.TestCase):
    """Helpers of the tests on a running server, with raw client sockets (no test of its own)."""

    # read 1 holding register at address 0: MBAP (transaction 1, unit 1) + PDU
    REQ_FRAME = b'\x00\x01\x00\x00\x00\x06\x01\x03\x00\x00\x00\x01'
    RESP_LEN = 11

    def _start_server(self, **kwargs: Any) -> ModbusServer:
        """Start a server on a free TCP port chosen by the OS (port=0), it's stored in self.port."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **kwargs)
        _host, port = server.start()
        self.addCleanup(server.stop)
        self.port = port
        return server

    def _connect(self) -> socket.socket:
        """Open a new client connection to the server started by _start_server()."""
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def _closed_after(self, sock: socket.socket, max_wait: float) -> float | None:
        """Return the delay (in s) before the server close the connection, None if it is still open."""
        sock.settimeout(max_wait)
        start = time.monotonic()
        try:
            data = sock.recv(1)
        except TimeoutError:
            return None
        except ConnectionError:
            data = b''
        return time.monotonic() - start if data == b'' else None

    def _request(self, sock: socket.socket) -> bytes:
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


class TestModbusServerLimits(_LiveServerTestCase):
    """Tests of request_timeout, idle_timeout, max_connections and allowed_hosts."""

    def test_defaults(self) -> None:
        server = ModbusServer()
        self.assertEqual(server.request_timeout, 30.0)
        self.assertEqual(server.idle_timeout, 300.0)
        self.assertEqual(server.max_connections, 128)
        self.assertIs(server.evict_idle, True)
        self.assertEqual(server.max_connections_per_host, 16)

    def test_params_check(self) -> None:
        bad_values: tuple[dict[str, Any], ...] = (
            {'request_timeout': 0}, {'request_timeout': -1}, {'idle_timeout': 0}, {'idle_timeout': -2.5},
            {'max_connections': 0}, {'max_connections': -1}, {'max_connections_per_host': 0})
        for kwargs in bad_values:
            with self.assertRaises(ValueError, msg=repr(kwargs)):
                ModbusServer(**kwargs)
        # bad values are typed Any: they are deliberately not of the annotated type
        bad_types: tuple[dict[str, Any], ...] = (
            {'request_timeout': '1'}, {'idle_timeout': True}, {'max_connections': 1.5},
            {'max_connections': True}, {'max_connections': '2'}, {'max_connections_per_host': 1.0})
        for kwargs in bad_types:
            with self.assertRaises(TypeError, msg=repr(kwargs)):
                ModbusServer(**kwargs)
        # None disable the limits
        server = ModbusServer(request_timeout=None, idle_timeout=None, max_connections=None,
                              max_connections_per_host=None)
        self.assertIsNone(server.request_timeout)
        self.assertIsNone(server.max_connections_per_host)

    def test_request_timeout(self) -> None:
        """A request that is never completed (partial MBAP header, or header without PDU) closes the session."""
        self._start_server(request_timeout=0.25)
        for sent_len in (3, 7):
            sock = self._connect()
            sock.sendall(self.REQ_FRAME[:sent_len])
            delay = self._closed_after(sock, 3.0)
            self.assertIsNotNone(delay, f'stalled request ({sent_len} bytes sent): session is still open')
            self.assertLess(delay, 2.0)  # type: ignore
        # server is still serving others
        self.assertEqual(len(self._request(self._connect())), self.RESP_LEN)

    def test_request_timeout_is_for_the_whole_request(self) -> None:
        """request_timeout limits the whole request from its first byte (MBAP header and PDU), not each part."""
        self._start_server(request_timeout=1.0)
        sock = self._connect()
        start = time.monotonic()
        # the MBAP header is complete just before the deadline, then the PDU never comes: with a new delay for the
        # PDU, the session would be closed after 1.8 s
        sock.sendall(self.REQ_FRAME[:3])
        time.sleep(0.8)
        sock.sendall(self.REQ_FRAME[3:8])
        self.assertIsNotNone(self._closed_after(sock, 3.0), 'stalled request: session is still open')
        self.assertLess(time.monotonic() - start, 1.6)
        # a request split in pieces but received in time is served
        sock = self._connect()
        sock.sendall(self.REQ_FRAME[:3])
        time.sleep(0.3)
        self.assertEqual(len(self._request_end(sock, self.REQ_FRAME[3:])), self.RESP_LEN)

    def _request_end(self, sock: socket.socket, end: bytes) -> bytes:
        """Send the end of a request and return the response (b'' if the connection is closed)."""
        sock.settimeout(5)
        sock.sendall(end)
        resp = b''
        while len(resp) < self.RESP_LEN:
            chunk = sock.recv(self.RESP_LEN - len(resp))
            if not chunk:
                break
            resp += chunk
        return resp

    def test_request_timeout_disabled(self) -> None:
        """With request_timeout=None a stalled request is never closed (old behavior)."""
        self._start_server(request_timeout=None)
        sock = self._connect()
        sock.sendall(self.REQ_FRAME[:7])
        self.assertIsNone(self._closed_after(sock, 0.7))

    def test_request_timeout_not_for_a_complete_request(self) -> None:
        """The request_timeout don't apply to a session that wait for a request (see idle_timeout)."""
        self._start_server(request_timeout=0.25)
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        self.assertIsNone(self._closed_after(sock, 0.7), 'request_timeout close an idle session')
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_idle_timeout(self) -> None:
        """A session without request is closed after idle_timeout, an active one is kept."""
        # keep a wide margin between the pause between requests (0.1 s) and idle_timeout: on a loaded CI runner
        # (macOS especially) a 0.1 s sleep plus a request round trip can take more than 0.25 s
        idle_timeout = 1.0
        self._start_server(idle_timeout=idle_timeout)
        sock = self._connect()
        for i in range(15):  # about 1.5 s of activity (more than idle_timeout)
            self.assertEqual(len(self._request(sock)), self.RESP_LEN, f'active session closed (request {i})')
            time.sleep(0.1)
        delay = self._closed_after(sock, idle_timeout + 4.0)
        self.assertIsNotNone(delay, 'idle session is still open')
        self.assertLess(delay, idle_timeout + 3.0)  # type: ignore

    def test_idle_timeout_stops_when_request_starts(self) -> None:
        """idle_timeout is for a session without any data: a slow request is a matter for request_timeout."""
        self._start_server(idle_timeout=0.2)
        sock = self._connect()
        sock.sendall(self.REQ_FRAME[:3])
        time.sleep(0.5)  # more than idle_timeout
        sock.sendall(self.REQ_FRAME[3:])
        sock.settimeout(5)
        resp = sock.recv(self.RESP_LEN)
        self.assertEqual(len(resp), self.RESP_LEN)

    def test_connection_burst(self) -> None:
        """A burst of simultaneous connections must not wait for a TCP SYN retransmission (listen backlog)."""
        self._start_server()
        start = time.monotonic()
        for _ in range(60):
            self._connect()
        self.assertLess(time.monotonic() - start, 3.0)

    def test_no_idle_timeout_by_default(self) -> None:
        self._start_server()
        sock = self._connect()
        self.assertIsNone(self._closed_after(sock, 0.7))
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_max_connections(self) -> None:
        """With evict_idle=False, connections over max_connections are closed at once, a session end frees a slot."""
        self._start_server(max_connections=2, evict_idle=False)
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

    def _connect_from(self, source_ip: str) -> socket.socket:
        """Open a client connection from another loopback address (skip the test if the OS doesn't allow it)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        try:
            sock.bind((source_ip, 0))
        except OSError:
            self.skipTest(f'loopback address {source_ip} not available on this machine')
        sock.settimeout(5)
        sock.connect(('127.0.0.1', self.port))
        return sock

    def test_max_connections_per_host(self) -> None:
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

    def test_max_connections_per_host_disabled(self) -> None:
        self._start_server(max_connections_per_host=None)
        socks = [self._connect() for _ in range(20)]
        for sock in socks:
            self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_host_key(self) -> None:
        """Connections are counted by IPv4 address, by /64 network for IPv6, an IPv4-mapped address is IPv4."""
        from pyModbusTCP.server._common import host_key
        self.assertEqual(host_key(('10.1.2.3', 502)), '10.1.2.3')
        self.assertEqual(host_key(('::ffff:10.1.2.3', 502, 0, 0)), '10.1.2.3')
        self.assertEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), host_key(('2001:db8:1:2:ffff::9', 1, 0, 0)))
        self.assertEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), '2001:db8:1:2::/64')
        self.assertNotEqual(host_key(('2001:db8:1:2::1', 502, 0, 0)), host_key(('2001:db8:1:3::1', 502, 0, 0)))
        self.assertEqual(host_key(('fe80::1%eth0', 502, 0, 2)), 'fe80::/64')
        # another prefix length: by address (128), or by a larger network (56); never for IPv4
        self.assertEqual(host_key(('2001:db8:1:2::1', 502, 0, 0), 128), '2001:db8:1:2::1/128')
        self.assertNotEqual(host_key(('2001:db8:1:2::1', 1, 0, 0), 128), host_key(('2001:db8:1:2::2', 1, 0, 0), 128))
        self.assertEqual(host_key(('2001:db8:1:2::1', 1, 0, 0), 56), host_key(('2001:db8:1:3::1', 1, 0, 0), 56))
        self.assertEqual(host_key(('2001:db8:1:2::1', 1, 0, 0), 56), '2001:db8:1::/56')
        self.assertEqual(host_key(('10.1.2.3', 502), 128), '10.1.2.3')
        self.assertEqual(host_key(('::ffff:10.1.2.3', 502, 0, 0), 56), '10.1.2.3')

    def test_ipv6_host_prefix(self) -> None:
        """max_connections_per_host counts the IPv6 clients by network of ipv6_host_prefix bits (64 by default)."""
        tcp_server = ModbusServer.CustomThreadingTCPServer(('127.0.0.1', 0), ModbusServer.ModbusRequestHandler,
                                                           bind_and_activate=False)
        self.addCleanup(tcp_server.server_close)
        tcp_server.max_connections_per_host = 2
        # sessions without a real socket: any hashable object is a key of the sessions
        sessions = [object(), object()]
        for i, session in enumerate(sessions, start=1):
            tcp_server._add_session(session, (f'2001:db8:1:2::{i}', 50000, 0, 0))
        third = ('2001:db8:1:2::3', 50000, 0, 0)
        # /64: the same host, its 2 connections are taken
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertFalse(tcp_server.verify_request(object(), third))
        # /128: each address is a host (the active sessions are counted again)
        tcp_server.ipv6_host_prefix = 128
        self.assertTrue(tcp_server.verify_request(object(), third))
        # /56: a larger network, other /64 of it are the same host
        tcp_server.ipv6_host_prefix = 56
        self.assertFalse(tcp_server.verify_request(object(), ('2001:db8:1:ff::1', 50000, 0, 0)))
        self.assertTrue(tcp_server.verify_request(object(), ('2001:db8:2::1', 50000, 0, 0)))
        # a session ends with the key of the prefix in effect, every counter goes back to 0
        for session in sessions:
            tcp_server._remove_session(session)
        self.assertEqual(tcp_server._host_sessions, {})
        self.assertEqual(tcp_server._session_keys, {})

    def test_ipv6_bind_address(self) -> None:
        """An IPv6 server binds a link-local address with its scope id (bind((host, port)) drops the zone: EINVAL)."""
        from pyModbusTCP.server._transport import ipv6_bind_address
        sockaddr = ('fe80::1111:2222:3333:4444%eth0', 5020, 0, 3)
        with mock.patch('socket.getaddrinfo', return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, '', sockaddr)]) \
                as getaddrinfo:
            self.assertEqual(ipv6_bind_address(('fe80::1111:2222:3333:4444%eth0', 5020)), sockaddr)
        getaddrinfo.assert_called_once_with('fe80::1111:2222:3333:4444%eth0', 5020, socket.AF_INET6,
                                            socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)
        # the other addresses are bound as given (no name resolution here)
        with mock.patch('socket.getaddrinfo', side_effect=AssertionError('no resolution expected')):
            for address in (('::1', 502), ('::', 0), ('localhost', 502), ('2001:db8::1', 502, 0, 0)):
                self.assertEqual(ipv6_bind_address(address), address)
        # an unknown interface: an OSError (socket.gaierror), start() raises NetworkError
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror(socket.EAI_NONAME, 'unknown')), \
                self.assertRaises(OSError):
            ipv6_bind_address(('fe80::1%nosuchif', 502))

    def test_named_host_resolved_once_never_the_wildcard(self) -> None:
        """S11 of the audit: a host given by name that resolves to 0.0.0.0 or :: is refused by start() (it listened on
        every interface); the name is resolved once, the address bound is the one checked."""
        from pyModbusTCP.server._transport import named_host_address
        for family, wildcard in ((socket.AF_INET, ('0.0.0.0', 502)), (socket.AF_INET6, ('::', 502, 0, 0)),
                                 (socket.AF_INET6, ('::ffff:0.0.0.0', 502, 0, 0))):
            with self.subTest(wildcard=wildcard[0]), \
                    mock.patch('socket.getaddrinfo', return_value=[(family, socket.SOCK_STREAM, 6, '', wildcard)]), \
                    self.assertRaisesRegex(OSError, 'wildcard address'):
                named_host_address(('plc-gw.example', 502), family)
        # the same with the ipaddress of Python 3.12.0 to 3.12.3, where ::ffff:0.0.0.0 is not "unspecified"
        mapped = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:0.0.0.0', 502, 0, 0))]
        with mock.patch('socket.getaddrinfo', return_value=mapped), \
                mock.patch.object(ipaddress.IPv6Address, 'is_unspecified', property(lambda ip: ip._ip == 0)), \
                self.assertRaisesRegex(OSError, 'wildcard address'):
            named_host_address(('plc-gw.example', 502), socket.AF_INET6)
        sockaddr = ('192.0.2.10', 502)
        with mock.patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', sockaddr)]):
            self.assertEqual(named_host_address(('plc-gw.example', 502), socket.AF_INET), sockaddr)
        # an IP address (the wildcard address written as such included) is bound as given, without resolution
        with mock.patch('socket.getaddrinfo', side_effect=AssertionError('no resolution expected')):
            for address in (('0.0.0.0', 502), ('127.0.0.1', 0), ('::', 502), ('fe80::1%2', 502, 0, 2)):
                self.assertEqual(named_host_address(address, socket.AF_INET), address)
        # start(): NetworkError, nothing listens
        server = ModbusServer('plc-gw.example', 0, no_block=True)
        wildcard_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('0.0.0.0', 0))]
        with mock.patch('socket.getaddrinfo', return_value=wildcard_info), \
                self.assertRaisesRegex(ModbusServer.NetworkError, 'wildcard address'):
            server.start()
        self.assertFalse(server.is_running)
        # a name of the host: its address
        server = ModbusServer('localhost', 0, no_block=True)
        host, _ = server.start()
        self.addCleanup(server.stop)
        self.assertEqual(host, '127.0.0.1')

    def test_ipv6_link_local_bind_uses_the_scope_id(self) -> None:
        """server_bind() of an IPv6 server gives the full link-local address (with its scope id) to bind()."""
        tcp_server = ModbusServer.CustomThreadingTCPServer.__new__(ModbusServer.CustomThreadingTCPServer)
        tcp_server.address_family = socket.AF_INET6
        tcp_server.server_address = ('fe80::1%eth0', 5020)
        tcp_server.socket = mock.Mock()
        tcp_server.socket.getsockname.return_value = ('fe80::1%eth0', 5020, 0, 3)
        sockaddr = ('fe80::1%eth0', 5020, 0, 3)
        with mock.patch('socket.getaddrinfo', return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, '', sockaddr)]):
            tcp_server.server_bind()
        tcp_server.socket.bind.assert_called_once_with(sockaddr)

    def test_ipv6_host_prefix_option(self) -> None:
        self.assertEqual(ModbusServer().ipv6_host_prefix, 64)
        self.assertIn('ipv6_host_prefix=64', repr(ModbusServer()))
        self.assertEqual(ModbusServer(ipv6_host_prefix=128).ipv6_host_prefix, 128)
        bad_values: tuple[tuple[Any, type[Exception]], ...] = (
            (0, ValueError), (129, ValueError), (-64, ValueError),
            (True, TypeError), (64.0, TypeError), ('64', TypeError))
        for value, exc in bad_values:
            with self.subTest(value=value), self.assertRaises(exc):
                # (bad values typed Any: they are deliberately not of the annotated type)
                ModbusServer(ipv6_host_prefix=cast(Any, value))
        # a property, applied at once to a running server
        server = self._start_server()
        server.ipv6_host_prefix = 128
        self.assertIsNotNone(server._tcp_server)
        assert server._tcp_server is not None
        self.assertEqual(server._tcp_server.ipv6_host_prefix, 128)
        with self.assertRaises(ValueError):
            server.ipv6_host_prefix = 0
        self.assertEqual(server.ipv6_host_prefix, 128)
        # kept by a restart
        server.stop()
        server.start()
        assert server._tcp_server is not None
        self.assertEqual(server._tcp_server.ipv6_host_prefix, 128)

    def _thief(self) -> socket.socket:
        """A socket with SO_REUSEADDR, like a local process that tries to take the port of the server."""
        thief = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(thief.close)
        thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return thief

    def _assert_server_gets_connections(self, thief: socket.socket) -> None:
        """The connections to 127.0.0.1 always go to the server, never to the thief socket (if it could listen)."""
        for _ in range(3):
            self.assertEqual(len(self._request(self._connect())), self.RESP_LEN)
        thief.setblocking(False)
        with self.assertRaises(OSError):
            thief.accept()

    def test_port_hijacking(self) -> None:
        """Another socket can't bind the address and port of a running server, even with SO_REUSEADDR."""
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
        # the same address and port: refused on every system
        with self.assertRaises(OSError):
            self._thief().bind(('127.0.0.1', self.port))
        # the wildcard address and the same port: refused on Linux. Windows and macOS allow it, but the wildcard
        # socket only gets the connections to the other addresses (the most specific binding wins): no hijacking
        thief = self._thief()
        if sys.platform == 'linux':
            with self.assertRaises(OSError):
                thief.bind(('0.0.0.0', self.port))
        else:
            try:
                thief.bind(('0.0.0.0', self.port))
                thief.listen(5)
            except OSError:
                pass
        self._assert_server_gets_connections(thief)

    def test_port_hijacking_of_a_wildcard_server(self) -> None:
        """A server on every interface (0.0.0.0): another socket can't take one of its addresses (127.0.0.1).

        This is the real hijacking (the most specific binding gets the connections): refused on Linux, and on
        Windows thanks to SO_EXCLUSIVEADDRUSE (SO_REUSEADDR allowed it, even to another user account). macOS allows
        it to a process of the same user only (see SECURITY.md).
        """
        server = ModbusServer(host='0.0.0.0', port=0, no_block=True)
        self.port = server.start()[1]
        self.addCleanup(server.stop)
        thief = self._thief()
        if sys.platform in ('linux', 'win32'):
            with self.assertRaises(OSError):
                thief.bind(('127.0.0.1', self.port))
            self._assert_server_gets_connections(thief)
        else:
            self.skipTest('BSD semantics: a socket of the same user can bind a more specific address')

    def test_quick_restart_on_the_same_port(self) -> None:
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

    def test_allowed_hosts(self) -> None:
        """Only the clients of the allowed_hosts whitelist are served."""
        for hosts in (['127.0.0.1'], ['127.0.0.0/8'], ['10.1.2.3', '127.0.0.1'], ('10.0.0.0/8', '127.0.0.0/8')):
            with self.subTest(allowed_hosts=hosts):
                server = self._start_server(allowed_hosts=hosts)
                sock = self._connect()
                self.assertEqual(len(self._request(sock)), self.RESP_LEN)
                self.assertEqual(len(server.active_sessions), 1)
                server.stop()

    def test_allowed_hosts_refusal(self) -> None:
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

    def test_allowed_hosts_after_restart(self) -> None:
        """The whitelist is still applied after a stop/start."""
        server = self._start_server(allowed_hosts=['10.1.2.3'])
        for _ in range(2):
            with self.assertLogs('pyModbusTCP.server', level='WARNING'):
                sock = self._connect()
                self.assertIsNotNone(self._closed_after(sock, 2.0), 'host out of allowed_hosts is not closed')
            server.stop()
            self.port = server.start()[1]

    def test_allowed_hosts_with_fallback(self) -> None:
        """The whitelist is checked before any function: a fallback handler is never called for a refused client."""
        calls: list[Request] = []

        def fallback(req: Request) -> bytes:
            calls.append(req)
            return req.pdu

        self._start_server(allowed_hosts=['10.1.2.3'], functions={}, fallback=fallback)
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertIsNotNone(self._closed_after(self._connect(), 2.0), 'host out of allowed_hosts is not closed')
        self.assertEqual(calls, [])


class TestRuntimeSettings(_LiveServerTestCase):
    """The access control and limit settings changed after the constructor: checked, and applied (audit S-01)."""

    def test_keyword_only_arguments(self) -> None:
        """host, port and no_block are the only positional arguments."""
        server = ModbusServer('127.0.0.1', 0, True)
        self.assertEqual((server.host, server.port, server.no_block), ('127.0.0.1', 0, True))
        args: tuple[Any, ...] = ('127.0.0.1', 502, False, True)
        with self.assertRaises(TypeError):
            ModbusServer(*args)

    def test_allowed_hosts_set_before_start(self) -> None:
        """A whitelist set as a property before start() is applied (it was ignored before the fix)."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
        server.allowed_hosts = ['10.9.9.9']
        self.port = server.start()[1]
        self.addCleanup(server.stop)
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._request(self._connect()), b'')

    def test_allowed_hosts_set_on_a_running_server(self) -> None:
        """A new whitelist filters the next connections and closes the sessions of the clients it no longer allows."""
        server = self._start_server()
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        with self.assertLogs('pyModbusTCP.server', level='INFO') as logs:
            server.allowed_hosts = ['10.1.2.3']
        self.assertIn('1 session(s) of clients no longer allowed closed', '\n'.join(logs.output))
        self.assertIsNotNone(self._closed_after(sock, 2.0), 'session of a client no longer allowed is not closed')
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._request(self._connect()), b'')
        # allowed again: a new connection is served, a session of an allowed client is kept on a change
        server.allowed_hosts = ['127.0.0.0/8']
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        server.allowed_hosts = None
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)

    def test_allowed_hosts_set_while_a_client_connects(self) -> None:
        """A whitelist set between the check of a new connection and the start of its session: never a session.

        The check of verify_request() saw the old whitelist, and the session was not yet in the snapshot of the
        sessions to close: the client no longer allowed was served.
        """
        from pyModbusTCP.server._transport import CustomThreadingTCPServer
        checked, go_on = threading.Event(), threading.Event()
        verify_request = CustomThreadingTCPServer.verify_request

        def slow_verify_request(tcp_server: CustomThreadingTCPServer, request: Any, client_address: Any) -> bool:
            allowed = verify_request(tcp_server, request, client_address)
            checked.set()
            go_on.wait(5.0)
            return allowed

        with mock.patch.object(CustomThreadingTCPServer, 'verify_request', slow_verify_request):
            server = self._start_server(allowed_hosts=['127.0.0.1'])
            sock = self._connect()
            self.assertTrue(checked.wait(5.0))
            # allowed by the check, then out of the new whitelist before its session starts
            server.allowed_hosts = ['10.1.2.3']
            with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
                go_on.set()
                self.assertEqual(self._request(sock), b'', 'a client no longer allowed is served')
            self.assertIn('host not in allowed hosts', '\n'.join(logs.output))
            self.assertEqual(server.active_sessions, [])

    def test_allowed_hosts_value_and_checks(self) -> None:
        server = ModbusServer(allowed_hosts=['192.168.1.10'])
        # a tuple: a change in place (like append()) fails instead of being silently ignored
        self.assertEqual(server.allowed_hosts, ('192.168.1.10/32',))
        bad_values: tuple[Any, ...] = ('10.0.0.1', ['localhost'], ['10.0.0.5/24'], [42])
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                server.allowed_hosts = value
            # a refused value changes nothing
            self.assertEqual(server.allowed_hosts, ('192.168.1.10/32',))
        server.allowed_hosts = []
        self.assertEqual(server.allowed_hosts, ())
        server.allowed_hosts = None
        self.assertIsNone(server.allowed_hosts)

    def test_read_only_set_on_a_running_server(self) -> None:
        server = self._start_server()
        client = ModbusClient(host='127.0.0.1', port=self.port)
        self.addCleanup(client.close)
        self.assertTrue(client.write_single_register(0, 1))
        server.read_only = True
        self.assertFalse(client.write_single_register(0, 2))
        self.assertEqual(client.last_except, 0x01)
        server.read_only = False
        self.assertTrue(client.write_single_register(0, 3))
        # checked like the constructor argument ('no' would be true)
        bad_values: tuple[Any, ...] = ('no', 1, None)
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(TypeError):
                server.read_only = value
        self.assertFalse(server.read_only)

    def test_limits_set_on_a_running_server(self) -> None:
        server = self._start_server(max_connections=None, idle_timeout=None, evict_idle=False)
        socks = [self._connect() for _ in range(2)]
        for sock in socks:
            self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        # the next connections only: the 2 active sessions are kept
        server.max_connections = 2
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._request(self._connect()), b'')
        for sock in socks:
            self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        server.max_connections = None
        server.max_connections_per_host = 2
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._request(self._connect()), b'')
        server.max_connections_per_host = None
        # idle_timeout applies from the next wait for a request of each session
        server.idle_timeout = 0.5
        sock = self._connect()
        self.assertEqual(len(self._request(sock)), self.RESP_LEN)
        self.assertIsNotNone(self._closed_after(sock, 3.0), 'new idle_timeout not applied')

    def test_limits_checks(self) -> None:
        server = ModbusServer()
        bad_values: tuple[tuple[str, Any, type[Exception]], ...] = (
            ('max_connections', 0, ValueError), ('max_connections', True, TypeError),
            ('max_connections_per_host', -1, ValueError), ('request_timeout', 0, ValueError),
            ('idle_timeout', float('inf'), ValueError), ('idle_timeout', '5', TypeError),
            ('evict_idle', 1, TypeError), ('evict_idle', None, TypeError))
        for name, value, exc in bad_values:
            before = getattr(server, name)
            with self.subTest(name=name, value=value), self.assertRaises(exc):
                setattr(server, name, value)
            self.assertEqual(getattr(server, name), before)
        server.request_timeout = 5
        self.assertEqual(server.request_timeout, 5.0)
        self.assertIsInstance(server.request_timeout, float)


class TestEvictIdle(_LiveServerTestCase):
    """With max_connections reached, the most idle session is closed for a new client (evict_idle, audit S3)."""

    def _served(self, sock: socket.socket) -> bool:
        return len(self._request(sock)) == self.RESP_LEN

    def test_never_active_session_evicted_first(self) -> None:
        """A connection without any request is closed before an older client with requests."""
        server = self._start_server(max_connections=3)
        silent = self._connect()
        # wait for its session: the next connections must not be accepted before it
        deadline = time.monotonic() + 5.0
        while len(server.active_sessions) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        sock_1, sock_2 = self._connect(), self._connect()
        self.assertTrue(self._served(sock_1))
        self.assertTrue(self._served(sock_2))
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            newcomer = self._connect()
            self.assertTrue(self._served(newcomer), 'new client refused while a session is idle')
        self.assertIn('idle session(s) closed', logs.records[0].getMessage())
        self.assertIsNotNone(self._closed_after(silent, 2.0), 'the session without request is not closed')
        for sock in (sock_1, sock_2, newcomer):
            self.assertTrue(self._served(sock))
        self.assertEqual(len(server.active_sessions), 3)

    def test_oldest_request_evicted(self) -> None:
        """Between active sessions, the one with the oldest last request is closed."""
        self._start_server(max_connections=2)
        old, recent = self._connect(), self._connect()
        self.assertTrue(self._served(old))
        time.sleep(0.05)
        self.assertTrue(self._served(recent))
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertTrue(self._served(self._connect()))
        self.assertIsNotNone(self._closed_after(old, 2.0), 'the session with the oldest request is not closed')
        self.assertTrue(self._served(recent))

    def test_held_connections_dont_keep_a_client_out(self) -> None:
        """Connections opened and held without requests (up to max_connections) can't keep a real client out."""
        self._start_server(max_connections=8, max_connections_per_host=None)
        held = [self._connect() for _ in range(8)]
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            for _ in range(3):
                self.assertTrue(self._served(self._connect()), 'a client is refused by held connections')
        closed = [sock for sock in held if self._closed_after(sock, 0.5) is not None]
        self.assertGreaterEqual(len(closed), 3)

    def test_busy_session_never_evicted(self) -> None:
        """A session processing a request is never closed: the new client is refused if every session is busy."""
        release = threading.Event()
        started = threading.Event()

        def fallback(req: Request) -> bytes:
            started.set()
            release.wait(10)
            return req.pdu

        self.addCleanup(release.set)
        self._start_server(max_connections=1, functions={}, fallback=fallback)
        busy = self._connect()
        busy.sendall(b'\x00\x01\x00\x00\x00\x03\x01\x41\x00')
        self.assertTrue(started.wait(5))
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            self.assertIsNotNone(self._closed_after(self._connect(), 2.0), 'connection over the limit not closed')
        self.assertIn('rejected', logs.records[0].getMessage())
        release.set()
        busy.settimeout(5)
        self.assertEqual(busy.recv(9), b'\x00\x01\x00\x00\x00\x03\x01\x41\x00')
        # once answered, the session is idle again: it makes room for the next client
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            newcomer = self._connect()
            newcomer.sendall(b'\x00\x02\x00\x00\x00\x03\x01\x41\x00')
            newcomer.settimeout(5)
            self.assertEqual(newcomer.recv(9), b'\x00\x02\x00\x00\x00\x03\x01\x41\x00')
        self.assertIsNotNone(self._closed_after(busy, 2.0))

    def test_session_evicted_before_its_request_is_processed(self) -> None:
        """A session evicted between the end of the receive of a request and its processing never processes it.

        Its socket is shut down, its response can't be sent: the request (like a write) was executed anyway.
        """
        handler = mock.Mock(side_effect=lambda req: req.pdu)
        server = self._start_server(max_connections=2, functions={0x41: handler})
        tcp_server = server._tcp_server
        assert tcp_server is not None
        original = tcp_server.request_started

        def evicted_first(request: Any) -> bool:
            # a new client takes the slot of this session at this exact moment (the main thread, verify_request())
            self.assertIsNotNone(tcp_server._evict_idle_session())
            return original(request)

        tcp_server.request_started = evicted_first  # type: ignore[method-assign]
        sock = self._connect()
        sock.sendall(b'\x00\x01\x00\x00\x00\x03\x01\x41\x00')
        self.assertIsNotNone(self._closed_after(sock, 2.0), 'the evicted session is not closed')
        handler.assert_not_called()
        # the next requests (of sessions that are not evicted) are processed
        tcp_server.request_started = original  # type: ignore[method-assign]
        sock = self._connect()
        sock.sendall(b'\x00\x02\x00\x00\x00\x03\x01\x41\x00')
        sock.settimeout(5)
        self.assertEqual(sock.recv(9), b'\x00\x02\x00\x00\x00\x03\x01\x41\x00')
        handler.assert_called_once()

    def test_host_limit_checked_before_eviction(self) -> None:
        """A host at its limit is refused: it can't close the sessions of the others."""
        server = self._start_server(max_connections=2, max_connections_per_host=1)
        other = self._connect_from('127.0.0.2')
        mine = self._connect()
        self.assertTrue(self._served(other))
        self.assertTrue(self._served(mine))
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            self.assertIsNotNone(self._closed_after(self._connect(), 2.0), 'a host over its limit is not refused')
        self.assertIn('per host', logs.records[0].getMessage())
        self.assertTrue(self._served(other))
        self.assertTrue(self._served(mine))
        self.assertEqual(len(server.active_sessions), 2)
        # a third host is under its limit: it takes the place of the most idle session
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertTrue(self._served(self._connect_from('127.0.0.3')))
        self.assertIsNotNone(self._closed_after(other, 2.0), 'the most idle session is not closed')

    def _connect_from(self, source_ip: str) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        try:
            sock.bind((source_ip, 0))
        except OSError:
            self.skipTest(f'loopback address {source_ip} not available on this machine')
        sock.settimeout(5)
        sock.connect(('127.0.0.1', self.port))
        return sock

    def test_evict_idle_set_on_a_running_server(self) -> None:
        server = self._start_server(max_connections=1)
        first = self._connect()
        self.assertTrue(self._served(first))
        server.evict_idle = False
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertIsNotNone(self._closed_after(self._connect(), 2.0), 'evict_idle=False not applied')
        self.assertTrue(self._served(first))
        server.evict_idle = True
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertTrue(self._served(self._connect()))
        self.assertIsNotNone(self._closed_after(first, 2.0))
        self.assertIn('evict_idle=True', repr(server))


class TestModbusServerSpecFrames(unittest.TestCase):
    """The examples of the Modbus Application Protocol specification, on raw frames: an independent reference."""

    def setUp(self) -> None:
        self.store = MemoryStore()
        # port=0: a free TCP port chosen by the OS
        self.server = ModbusServer(host='127.0.0.1', port=0, no_block=True, store=self.store)
        _host, port = self.server.start()
        self.addCleanup(self.server.stop)
        self.port = port
        self.sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.addCleanup(self.sock.close)

    def _recv(self, size: int) -> bytes:
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex: str) -> str:
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    @staticmethod
    def bits_of(data: bytes) -> list[bool]:
        return [bool(data[i // 8] >> i % 8 & 1) for i in range(len(data) * 8)]

    def test_read_coils(self) -> None:
        # coils 20 to 38 of the spec (address 19 to 37, 19 coils) are CD 6B 05
        self.store.app.coils.write(19, self.bits_of(bytes.fromhex('cd6b05'))[:19])
        self.assertEqual(self.exchange('01 00 13 00 13'), '0103cd6b05')
        # same bits, but the quantity is not a multiple of 8 and padding bits must be zero
        self.store.app.coils.write(19 + 19, [True] * 5)
        self.assertEqual(self.exchange('01 00 13 00 13'), '0103cd6b05')

    def test_read_discrete_inputs(self) -> None:
        # spec: inputs 197 to 218 (address 196 to 217, 22 inputs) are AC DB 35
        self.store.app.discrete_inputs.write(196, self.bits_of(bytes.fromhex('acdb35'))[:22])
        self.assertEqual(self.exchange('02 00 c4 00 16'), '0203acdb35')

    def test_write_multiple_coils(self) -> None:
        # spec: write 10 coils from address 19: CD 01
        self.assertEqual(self.exchange('0f 00 13 00 0a 02 cd 01'), '0f0013000a')
        self.assertEqual(self.store.app.coils.read(19, 10), self.bits_of(bytes.fromhex('cd01'))[:10])
        self.assertEqual(self.store.app.coils.read(29, 1), [False], 'only 10 coils must be written')

    def test_read_holding_registers(self) -> None:
        # spec: read registers 108 to 110, values 022B 0000 0064
        self.store.app.holding_registers.write(107, [0x022b, 0, 0x64])
        self.assertEqual(self.exchange('03 00 6b 00 03'), '030602 2b0000 0064'.replace(' ', ''))

    def test_write_multiple_registers(self) -> None:
        # spec: write 2 registers from address 1: 000A 0102
        self.assertEqual(self.exchange('10 00 01 00 02 04 00 0a 01 02'), '1000010002')
        self.assertEqual(self.store.app.holding_registers.read(0, 4), [0, 0x000a, 0x0102, 0])

    def test_read_write_multiple_registers(self) -> None:
        # spec: read 6 registers from address 3 and write 3 registers (00FF) from address 14
        self.store.app.holding_registers.write(3, [0x00fe, 0x0acd, 0x0001, 0x0003, 0x000d, 0x00ff])
        resp = self.exchange('17 00 03 00 06 00 0e 00 03 06 00 ff 00 ff 00 ff')
        self.assertEqual(resp, '170c00fe0acd00010003000d00ff')
        self.assertEqual(self.store.app.holding_registers.read(13, 5), [0, 0xff, 0xff, 0xff, 0])

    def test_write_single_coil_values(self) -> None:
        # the only legal values are 0x0000 (off) and 0xFF00 (on), any other one is an ILLEGAL DATA VALUE (03)
        self.store.app.coils.write(10, [True])
        for value in ('0001', '00ff', '8000', 'ff01', 'ffff'):
            self.assertEqual(self.exchange(f'05 000a {value}'), '8503', value)
            self.assertEqual(self.store.app.coils.read(10, 1), [True], 'a refused request must not write')
        self.assertEqual(self.exchange('05 000a 0000'), '05000a0000')
        self.assertEqual(self.store.app.coils.read(10, 1), [False])
        self.assertEqual(self.exchange('05 000a ff00'), '05000aff00')
        self.assertEqual(self.store.app.coils.read(10, 1), [True])

    def test_read_write_multiple_registers_refused_request(self) -> None:
        # a refused request (exception response) must not write anything: here the read area is out of the table
        self.assertEqual(self.exchange('17 ffff 0002 0032 0001 02 0457'), '9702')
        self.assertEqual(self.store.app.holding_registers.read(50, 1), [0])
        # quantities: read from 1 to 125 (0x7D), write from 1 to 121 (0x79)
        self.assertEqual(self.exchange('17 0000 007e 0000 0001 02 0007'), '9703')
        self.assertEqual(self.store.app.holding_registers.read(0, 1), [0])
        resp = self.exchange('17 0000 007d 0000 0001 02 0007')
        self.assertEqual(resp[:4], '17fa')
        self.assertEqual(len(resp), 2 * (2 + 250))
        self.assertEqual(self.exchange('17 0000 0001 0000 0079 f2' + '0000' * 121), '17020000')

    def test_pdu_length(self) -> None:
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
        self.assertEqual(self.store.app.coils.read(0, 16), [False] * 16)
        self.assertEqual(self.store.app.holding_registers.read(0, 2), [0, 0])
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_extra_bytes_are_ignored(self) -> None:
        """Bytes after a complete and consistent request are ignored (the MBAP length keeps the stream in sync)."""
        self.store.app.holding_registers.write(0, [0x1234])
        self.assertEqual(self.exchange('03 0000 0001 ffff'), '03021234')
        self.assertEqual(self.exchange('06 0001 abcd 00'), '060001abcd')
        self.assertEqual(self.exchange('0f 0010 000a 02 ff 03 ee'), '0f0010000a')
        self.assertEqual(self.store.app.coils.read(16, 11), [True] * 10 + [False])
        self.assertEqual(self.exchange('10 0002 0001 02 5678 9abc'), '1000020001')
        self.assertEqual(self.exchange('17 0000 0003 0003 0001 02 0042 ff'), '17061234abcd5678')
        self.assertEqual(self.store.app.holding_registers.read(0, 5), [0x1234, 0xabcd, 0x5678, 0x42, 0])

    def test_mapped_read_without_the_right_count(self) -> None:
        """A mapped range that returns a wrong number of values is a SLAVE DEVICE FAILURE (04), never a bad frame."""
        self.store.map(Space.HOLDING_REGISTERS, 1000, 2, read=lambda address, count, ctx: [1])
        for pdu in ('03 03e8 0002', '17 03e8 0002 0005 0001 02 1234'):
            with self.subTest(pdu), self.assertLogs('pyModbusTCP.server', level='ERROR'):
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}04')
        # the write of function 0x17 is cancelled: the read failed in the same transaction
        self.assertEqual(self.store.app.holding_registers.read(5, 1), [0])

    def test_mapped_values_that_cant_be_encoded(self) -> None:
        """A mapped range that returns a value out of its field (a register over 65535): SLAVE DEVICE FAILURE (04),
        and the session is kept (the request was valid, the TCP stream is still in sync)."""
        self.store.map(Space.HOLDING_REGISTERS, 1000, 1, read=lambda address, count, ctx: [70000])
        self.store.map(Space.INPUT_REGISTERS, 1000, 1, read=lambda address, count, ctx: [-1])
        self.store.map(Space.COILS, 1000, 1, read=lambda address, count, ctx: ['on'])
        for pdu, expected in (('03 03e8 0001', '8304'), ('04 03e8 0001', '8404'), ('01 03e8 0001', '8104')):
            with self.subTest(pdu), self.assertLogs('pyModbusTCP.server', level='ERROR'):
                self.assertEqual(self.exchange(pdu), expected)
        # same connection: still in sync
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_handler_bad_response(self) -> None:
        """A function handler that returns no response, or a response that can't be encoded: SLAVE DEVICE FAILURE."""
        self.server.functions[0x41] = lambda req: b''
        self.server.functions[0x42] = lambda req: req.reply('>B', 300)
        self.server.functions[0x43] = lambda req: cast(bytes, None)
        for pdu in ('41 00', '42 00', '43 00'):
            # the errors of user code are logged once per 10 s at most: a new interval for each one
            self.server._handler_error_log._last_time = None
            with self.subTest(pdu), self.assertLogs('pyModbusTCP.server', level='ERROR'):
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}04')
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_unsupported_function_without_parameter(self) -> None:
        # a request can be a function code only (like 0x07, 0x0B, 0x0C or 0x11): ILLEGAL FUNCTION, not a closed session
        for func in (0x07, 0x0b, 0x0c, 0x11):
            self.assertEqual(self.exchange(f'{func:02x}'), f'{func | 0x80:02x}01')

    def _closed_by_server(self, frame: bytes) -> bool:
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as sock:
            # a connection closed by the server may be reported as a reset (POSIX) or as an abort (WinError 10053
            # on Windows) instead of an end of stream: ConnectionError covers both (and a broken pipe on send)
            try:
                sock.sendall(frame)
                return sock.recv(16) == b''
            except ConnectionError:
                return True

    def test_malformed_requests_close_the_session(self) -> None:
        # no function code at all (MBAP length 1)
        self.assertTrue(self._closed_by_server(b'\x00\x01\x00\x00\x00\x01\x01'))


class TestReadDeviceIdentification(unittest.TestCase):
    """Read Device Identification (function 0x2B, MEI type 0x0E) on raw frames."""

    def setUp(self) -> None:
        # basic objects 0 to 2, regular objects 3 to 6 (empty by default), extended objects 0x80 and 0x82
        self.device_id = DeviceIdentification(vendor_name=b'v', product_code=b'p', major_minor_revision=b'1',
                                              objects_by_id={0x80: b'x', 0x82: b'z'})
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, device_id=self.device_id)
        host, port = server.start()
        self.addCleanup(server.stop)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.addCleanup(self.sock.close)

    def _recv(self, size: int) -> bytes:
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex: str) -> str:
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    def objects(self, pdu_hex: str) -> tuple[list[int], int, int]:
        """Send a request, check the response format and return (object ids, more follows, next object id)."""
        resp = bytes.fromhex(self.exchange(pdu_hex))
        self.assertEqual(resp[:3], bytes.fromhex(pdu_hex)[:3], 'not a regular response')
        ids: list[int] = []
        offset = 7
        for _ in range(resp[6]):
            ids.append(resp[offset])
            offset += 2 + resp[offset + 1]
        self.assertEqual(offset, len(resp))
        return ids, resp[4], resp[5]

    def test_stream_access(self) -> None:
        self.assertEqual(self.objects('2b 0e 01 00'), ([0, 1, 2], 0, 0))
        self.assertEqual(self.objects('2b 0e 01 01'), ([1, 2], 0, 0))
        self.assertEqual(self.objects('2b 0e 02 03'), ([3, 4, 5, 6], 0, 0))
        self.assertEqual(self.objects('2b 0e 02 05'), ([5, 6], 0, 0))
        self.assertEqual(self.objects('2b 0e 03 80'), ([0x80, 0x82], 0, 0))
        self.assertEqual(self.objects('2b 0e 03 82'), ([0x82], 0, 0))

    def test_stream_access_restart(self) -> None:
        """An object id that isn't a known object of the requested category: the stream restarts at its first one."""
        for pdu, ids in (('2b 0e 01 05', [0, 1, 2]), ('2b 0e 01 ff', [0, 1, 2]),
                         ('2b 0e 02 00', [3, 4, 5, 6]), ('2b 0e 02 50', [3, 4, 5, 6]),
                         ('2b 0e 03 00', [0x80, 0x82]), ('2b 0e 03 81', [0x80, 0x82])):
            with self.subTest(pdu):
                self.assertEqual(self.objects(pdu), (ids, 0, 0))

    def test_stream_access_more_follows(self) -> None:
        """A stream that doesn't fit in a response is continued with the next object id given by the server."""
        self.device_id[0x81] = b'a' * 200
        self.device_id[0x82] = b'b' * 200
        self.assertEqual(self.objects('2b 0e 03 00'), ([0x80, 0x81], 0xff, 0x82))
        self.assertEqual(self.objects('2b 0e 03 82'), ([0x82], 0, 0))

    def test_individual_access(self) -> None:
        self.assertEqual(self.exchange('2b 0e 04 82'), '2b0e048300000182017a')
        self.assertEqual(self.objects('2b 0e 04 05'), ([5], 0, 0))
        # an unknown object is an ILLEGAL DATA ADDRESS (02)
        for obj_id in ('07', '7f', '81', 'ff'):
            with self.subTest(obj_id=obj_id):
                self.assertEqual(self.exchange(f'2b 0e 04 {obj_id}'), 'ab02')
        # the session is still in sync
        self.assertEqual(self.objects('2b 0e 04 00'), ([0], 0, 0))

    def test_bad_requests(self) -> None:
        # unknown ReadDevId code: ILLEGAL DATA VALUE (03)
        for code in ('00', '05', 'ff'):
            self.assertEqual(self.exchange(f'2b 0e {code} 00'), 'ab03')
        # another MEI type: ILLEGAL DATA ADDRESS (02), as before
        self.assertEqual(self.exchange('2b 0d 01 00'), 'ab02')


"""Test safe start/stop sequences."""


class TestStopFromSignalHandler(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'needs SIGALRM (not available on Windows)')
    def test_stop_from_signal_handler_in_blocking_mode(self) -> None:
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
            res = subprocess.run([sys.executable, '-c', code], check=False, timeout=15, capture_output=True, text=True)
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


def first_socket_family(**server_kwargs: Any) -> int:
    """Start a server and return the family requested for the first socket it creates.

    start() may fail afterwards (no IPv6 on this machine, invalid address...): only the family matters here.
    """
    real_socket = socket.socket
    families: list[int] = []

    def spy(family: int = -1, *args: Any, **kwargs: Any) -> socket.socket:
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
    def test_ipv4_is_the_default_family(self) -> None:
        self.assertEqual(first_socket_family(host='127.0.0.1', port=0), socket.AF_INET)

    def test_ipv6_option_requests_an_ipv6_socket(self) -> None:
        # fails if address_family is set after TCPServer.__init__() (the socket is already AF_INET)
        self.assertEqual(first_socket_family(host='::1', port=0, ipv6=True), socket.AF_INET6)

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_ipv6_real_connection(self) -> None:
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
    def test_ipv6_server_stop_and_restart(self) -> None:
        server = ModbusServer(host='::1', port=0, ipv6=True, no_block=True)
        for _ in range(3):
            server.start()
            self.assertTrue(server.is_running)
            assert server._tcp_server is not None
            self.assertEqual(server._tcp_server.socket.family, socket.AF_INET6)
            server.stop()
            self.assertFalse(server.is_running)

    def test_ipv4_real_connection(self) -> None:
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
    """The requests that write data (0x05, 0x06, 0x0F, 0x10, 0x15, 0x16, 0x17) get an ILLEGAL FUNCTION exception."""

    # request PDU (as hex string) of every function that writes data
    WRITE_REQUESTS: ClassVar[dict[str, str]] = {
        'write single coil': '05 0001 ff00',
        'write single register': '06 0005 002a',
        'write multiple coils': '0f 0000 0002 01 03',
        'write multiple registers': '10 0000 0001 02 0007',
        'write/read multiple registers': '17 0000 0001 0000 0001 02 0007',
    }

    def _start_server(self, **kwargs: Any) -> ModbusServer:
        """Start a server on a free TCP port (port=0) and open a raw connection to it (self.sock)."""
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, **kwargs)
        host, port = server.start()
        self.addCleanup(server.stop)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.addCleanup(self.sock.close)
        return server

    def _recv(self, size: int) -> bytes:
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            self.assertTrue(chunk, 'connection closed by server')
            data += chunk
        return data

    def exchange(self, pdu_hex: str) -> str:
        """Send a PDU (as hex string) in a frame to the server and return the response PDU as hex string."""
        pdu = bytes.fromhex(pdu_hex)
        self.sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
        header = self._recv(7)
        return self._recv(int.from_bytes(header[4:6], 'big') - 1).hex()

    def test_params_check(self) -> None:
        self.assertIs(ModbusServer().read_only, False)
        self.assertIs(ModbusServer(read_only=True).read_only, True)
        for value in (1, 0, 'yes', None, [True]):
            with self.assertRaises(TypeError, msg=repr(value)):
                ModbusServer(read_only=value)  # type: ignore
        self.assertIn('read_only=True', repr(ModbusServer(read_only=True)))
        self.assertIn('read_only=False', repr(ModbusServer()))

    def test_default_is_read_write(self) -> None:
        store = MemoryStore()
        events: list[ChangeEvent] = []
        store.subscribe(events.append)
        self._start_server(store=store)
        for name, pdu in self.WRITE_REQUESTS.items():
            self.assertEqual(self.exchange(pdu)[:2], pdu[:2], name)
        self.assertEqual(store.app.holding_registers.read(5, 1), [0x2a])
        self.assertTrue(events, 'writes must be notified to the listeners')

    def test_read_functions_are_allowed(self) -> None:
        store = MemoryStore()
        store.app.holding_registers.write(0, [0x1234])
        store.app.input_registers.write(0, [0x5678])
        store.app.coils.write(0, [True] + [False] * 7)
        store.app.discrete_inputs.write(1, [True])
        self._start_server(read_only=True, store=store)
        self.assertEqual(self.exchange('01 0000 0008'), '010101')
        self.assertEqual(self.exchange('02 0000 0008'), '020102')
        self.assertEqual(self.exchange('03 0000 0001'), '03021234')
        self.assertEqual(self.exchange('04 0000 0001'), '04025678')

    def test_write_functions_are_refused(self) -> None:
        store = MemoryStore()
        events: list[ChangeEvent] = []
        store.subscribe(events.append)
        self._start_server(read_only=True, store=store)
        for name, pdu in self.WRITE_REQUESTS.items():
            with self.subTest(name):
                # exception response: function code + 0x80, then the exception code 1 (ILLEGAL FUNCTION)
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}01')
        # nothing is written, nothing is notified
        self.assertEqual(store.app.coils.read(0, 16), [False] * 16)
        self.assertEqual(store.app.holding_registers.read(0, 16), [0] * 16)
        self.assertEqual(events, [])
        # the session is still open and in sync
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')

    def test_server_side_writes_are_still_possible(self) -> None:
        """read_only is for the requests of the clients, not for the application that owns the data store."""
        server = self._start_server(read_only=True)
        server.store.app.holding_registers.write(0, [9])
        self.assertEqual(self.exchange('03 0000 0001'), '03020009')

    def test_other_functions_are_not_concerned(self) -> None:
        self._start_server(read_only=True, device_id=DeviceIdentification(vendor_name=b'me'))
        # read device identification (individual access to VendorName)
        # (conformity level 0x82: regular objects only, no extended object)
        self.assertEqual(self.exchange('2b 0e 04 00'), '2b0e048200000100026d65')
        # an unknown function keeps its own exception
        self.assertEqual(self.exchange('55 00'), 'd501')

    def test_applied_to_every_handler(self) -> None:
        """read_only refuses the write function codes whatever their handler, even a fallback (like a gateway)."""
        received: list[int] = []

        def fallback(req: Request) -> bytes:
            received.append(req.function_code)
            return req.pdu

        self._start_server(read_only=True, functions={}, fallback=fallback)
        self.assertEqual(self.exchange('06 0005 002a'), '8601')
        self.assertEqual(self.exchange('03 0005 0001'), '0300050001')
        # the fallback only gets the read functions: mask write (0x16), write file record (0x15), diagnostics (0x08),
        # a private function or a MEI type other than 0x0E may write
        for pdu in ('16 0000 00ff 0000', '15 07 06 0001 0000 0001 0000', '08 0004 0000', '41 00', '2b 0d 00'):
            with self.subTest(pdu):
                self.assertEqual(self.exchange(pdu), f'{bytes.fromhex(pdu)[0] | 0x80:02x}01')
        self.assertEqual(self.exchange('2b 0e 01 00'), '2b0e0100')
        self.assertEqual(received, [0x03, 0x2B])

    def test_custom_function_not_refused(self) -> None:
        """A custom function of the server is not a fallback: it decides what read_only means for it."""
        server = self._start_server(read_only=True)
        server.function(0x41)(lambda req: req.reply('>B', 7))
        self.assertEqual(self.exchange('41'), '4107')

    def test_with_allowed_hosts(self) -> None:
        self._start_server(read_only=True, allowed_hosts=['127.0.0.1'])
        self.assertEqual(self.exchange('06 0005 002a'), '8601')
        self.assertEqual(self.exchange('03 0000 0001'), '03020000')


class TestRequestByteOrder(unittest.TestCase):
    """ Request.unpack() and Request.reply(): the byte order of the format, big endian without prefix. """

    def setUp(self) -> None:
        self.req = Request(pdu=bytes.fromhex('41 0102 0304'), ctx=RequestContext(function_code=0x41),
                           store=MemoryStore(0))

    def test_big_endian(self) -> None:
        for prefix in ('', '>', '!'):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.req.reply(prefix + 'H', 0x0102), bytes.fromhex('41 0102'))
                self.assertEqual(self.req.unpack(prefix + 'HH', 1, 5), (0x0102, 0x0304))
        # no field: the function code only
        self.assertEqual(self.req.reply(), b'\x41')

    def test_little_endian(self) -> None:
        """'<' is used as is (it was removed in silence by reply(): '<H' gave a big endian value)."""
        self.assertEqual(self.req.reply('<H', 0x0102), bytes.fromhex('41 0201'))
        self.assertEqual(self.req.unpack('<HH', 1, 5), (0x0201, 0x0403))
        # the function code is never part of the format: a single byte, whatever the order of the fields
        self.assertEqual(self.req.reply('<BH', 1, 0x0102), bytes.fromhex('41 01 0201'))

    def test_native_order_on_request(self) -> None:
        """'=' and '@' (the native order of the host) only when asked for: never the default of unpack()."""
        self.assertEqual(self.req.reply('=H', 0x0102), b'\x41' + struct.pack('=H', 0x0102))
        self.assertEqual(self.req.unpack('@H', 1, 3), struct.unpack('@H', bytes.fromhex('0102')))


def test_response_pdu_too_long() -> None:
    """A handler that returns a PDU over 253 bytes: "slave device failure", never an invalid frame."""
    server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
    server.function(0x41)(lambda req: req.reply('>253s', b'x' * 253))
    server.function(0x42)(lambda req: req.reply('>252s', b'x' * 252))
    _, port = server.start()
    try:
        c = ModbusClient(host='127.0.0.1', port=port, timeout=2.0)
        assert c.custom_request(b'\x41') is None and c.last_except == 0x04
        assert c.custom_request(b'\x42') == b'\x42' + b'x' * 252
        c.close()
    finally:
        server.stop()


def test_response_pdu_refused() -> None:
    """A response that the client would refuse is never sent: "slave device failure" instead, the session goes on.

    req.reply() without field (the function code alone) was sent, and the client of pyModbusTCP refused it with a
    "socket recv error"; a PDU of another function too ("function code mismatch").
    """
    server = ModbusServer(host='127.0.0.1', port=0, no_block=True)
    server.function(0x41)(lambda req: req.reply())
    server.function(0x42)(lambda req: b'\x43\x00')
    server.function(0x44)(lambda req: req.reply('>B', 1))
    # an exception response built by hand (instead of raising a ModbusException): the code of the request + 0x80
    server.function(0x45)(lambda req: bytes([0x45 | 0x80, 0x03]))
    _, port = server.start()
    try:
        c = ModbusClient(host='127.0.0.1', port=port, timeout=2.0)
        for pdu in (b'\x41', b'\x42'):
            assert c.custom_request(pdu) is None and c.last_except == 0x04, pdu
        assert c.custom_request(b'\x44') == b'\x44\x01'
        assert c.custom_request(b'\x45') is None and c.last_except == 0x03
        assert c.is_open
        c.close()
    finally:
        server.stop()


def test_invalid_function_code_never_reaches_the_fallback() -> None:
    """A function code 0 (like 0x80 to 0xFF) is not a function: "illegal function", the fallback is never called.

    The handlers are for the codes 1 to 127 only (function() refuses 0), but a request of code 0 was given to the
    fallback (like a gateway that relays it).
    """
    fallback = mock.Mock(side_effect=lambda req: req.pdu)
    server = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=fallback)
    _, port = server.start()
    try:
        for pdu in (b'\x00', b'\x00\x00\x00\x00\x01', b'\x80\x00', b'\xff'):
            with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
                sock.sendall(b'\x00\x01\x00\x00' + (len(pdu) + 1).to_bytes(2, 'big') + b'\x01' + pdu)
                assert sock.recv(9) == b'\x00\x01\x00\x00\x00\x03\x01' + bytes([pdu[0] | 0x80, 0x01]), pdu.hex()
        fallback.assert_not_called()
        # the client sees the exception of its request (function code 0 with the exception flag)
        c = ModbusClient(host='127.0.0.1', port=port, timeout=2.0)
        assert c.custom_request(b'\x00') is None and c.last_except == 0x01
        # the valid codes still reach the fallback
        assert c.custom_request(b'\x01\x00\x00\x00\x01') == b'\x01\x00\x00\x00\x01'
        assert c.custom_request(b'\x7f') is None and c.last_except == 0x04  # a 1 byte response is refused
        assert [call.args[0].function_code for call in fallback.call_args_list] == [0x01, 0x7F]
        c.close()
    finally:
        server.stop()


def test_flag_arguments() -> None:
    """no_block, ipv6 and read_only are bools only: 0/1, a string or None were taken for True or False in silence."""
    for name in ('no_block', 'ipv6', 'read_only'):
        for value in (1, 0, 'false', None):
            try:
                kwargs: dict[str, Any] = {name: value}
                ModbusServer('127.0.0.1', 0, **kwargs)
            except TypeError as e:
                assert name in str(e)
            else:
                raise AssertionError(f'{name}={value!r} accepted')


def test_function_code_tuples() -> None:
    """The tuples of constants describe the functions of the server (docs/package/module_constants.rst)."""
    from pyModbusTCP.constants import (
        MASK_WRITE_REGISTER,
        READ_FUNCTION_CODES,
        SUPPORTED_FUNCTION_CODES,
        WRITE_FILE_RECORD,
        WRITE_FUNCTION_CODES,
    )
    from pyModbusTCP.server import STANDARD_FUNCTIONS
    assert set(SUPPORTED_FUNCTION_CODES) == set(STANDARD_FUNCTIONS)
    # 0x15 and 0x16 are not implemented, but refused by read_only (a handler of the application could answer them)
    assert set(WRITE_FUNCTION_CODES) - set(SUPPORTED_FUNCTION_CODES) == {WRITE_FILE_RECORD, MASK_WRITE_REGISTER}
    assert not set(READ_FUNCTION_CODES) & set(WRITE_FUNCTION_CODES)
    assert set(READ_FUNCTION_CODES) | set(WRITE_FUNCTION_CODES) == set(SUPPORTED_FUNCTION_CODES) | {0x15, 0x16}


"""Tests for the ModbusServer `allowed_hosts` option."""


class TestAllowedHostsOption(unittest.TestCase):
    """Check of the allowed_hosts parameter and match of the client addresses (without any connection)."""

    def _tcp_server(self, hosts: Iterable[str] | None) -> ModbusServer.CustomThreadingTCPServer:
        """Return a (not bound) TCP server of ModbusServer with a whitelist."""
        tcp_server = ModbusServer.CustomThreadingTCPServer(('127.0.0.1', 0), ModbusServer.ModbusRequestHandler,
                                                           bind_and_activate=False)
        self.addCleanup(tcp_server.server_close)
        tcp_server.allowed_networks = ModbusServer._validate_allowed_hosts(hosts)
        return tcp_server

    def test_default(self) -> None:
        server = ModbusServer()
        self.assertIsNone(server.allowed_hosts)
        self.assertIn('allowed_hosts=None', repr(server))

    def test_hosts_are_normalized(self) -> None:
        server = ModbusServer(allowed_hosts=['192.168.1.10', '10.0.0.0/24', '::1', 'fd00::/8', ' 127.0.0.1 '])
        self.assertEqual(server.allowed_hosts,
                         ('192.168.1.10/32', '10.0.0.0/24', '::1/128', 'fd00::/8', '127.0.0.1/32'))
        self.assertIn("allowed_hosts=['192.168.1.10/32'", repr(server))
        # a network with host bits set is refused (often a typo for one address: it would allow the whole network)
        with self.assertRaisesRegex(ValueError, "host bits set: use '192.168.1.0/24' for the network, or "
                                                "'192.168.1.5' for the address only"):
            ModbusServer(allowed_hosts=['192.168.1.5/24'])
        with self.assertRaisesRegex(ValueError, 'write_hosts item .*host bits'):
            AccessControl(MemoryStore(), write_hosts=['10.0.0.1/8'])
        self.assertEqual(ModbusServer(allowed_hosts=['192.168.1.5/32', ' 10.0.0.0/8 ']).allowed_hosts,
                         ('192.168.1.5/32', '10.0.0.0/8'))

    def test_empty_list_allows_no_host(self) -> None:
        """The convention of every allow list: None for every host, an empty list for no host at all."""
        empty_lists: tuple[Iterable[str], ...] = ([], (), set())
        for hosts in empty_lists:
            server = ModbusServer(allowed_hosts=hosts)
            self.assertEqual(server.allowed_hosts, ())
            tcp_server = self._tcp_server(hosts)
            self.assertFalse(tcp_server.is_allowed(('127.0.0.1', 50000)))
            self.assertFalse(tcp_server.is_allowed(('::1', 50000, 0, 0)))
        self.assertTrue(self._tcp_server(None).is_allowed(('127.0.0.1', 50000)))
        # a warning at startup: an empty list from a configuration is often a mistake
        server = ModbusServer(host='127.0.0.1', port=0, no_block=True, allowed_hosts=[])
        with self.assertLogs('pyModbusTCP.server', level='WARNING') as logs:
            server.start()
        server.stop()
        self.assertIn('every connection will be refused', '\n'.join(logs.output))

    def test_any_iterable_is_accepted(self) -> None:
        for hosts in (('127.0.0.1',), iter(['127.0.0.1']), {'127.0.0.1'}, (host for host in ['127.0.0.1'])):
            self.assertEqual(ModbusServer(allowed_hosts=hosts).allowed_hosts, ('127.0.0.1/32',))

    def test_params_check(self) -> None:
        # hostnames are not supported
        bad_values: tuple[Iterable[str], ...] = (
            ['localhost'], ['example.com'], [''], ['999.1.1.1'], ['1.2.3'], ['10.0.0.0/33'],
            ['::1/129'], ['10.0.0.0/a'], ['127.0.0.1', 'localhost'])
        for hosts in bad_values:
            with self.assertRaises(ValueError, msg=repr(hosts)):
                ModbusServer(allowed_hosts=hosts)
        # a lone string must not be read char by char, an int is a valid address for ipaddress but not here
        # bad values are typed Any: they are deliberately not of the annotated type
        bad_types: tuple[Any, ...] = (
            '127.0.0.1', b'127.0.0.1', '', 42, 1.5, [3232235777], [None], [1.5], [b'127.0.0.1'], [('127.0.0.1',)])
        for bad_type in bad_types:
            with self.assertRaises(TypeError, msg=repr(bad_type)):
                ModbusServer(allowed_hosts=bad_type)
        # the error message names the faulty item
        with self.assertRaises(ValueError) as ctx:
            ModbusServer(allowed_hosts=['127.0.0.1', 'localhost'])
        self.assertIn("'localhost'", str(ctx.exception))

    def test_no_whitelist_allows_everything(self) -> None:
        tcp_server = self._tcp_server(None)
        for address in ('127.0.0.1', '8.8.8.8', '::1', 'not an ip'):
            self.assertTrue(tcp_server.is_allowed((address, 1234)), address)

    def test_ipv4_match(self) -> None:
        tcp_server = self._tcp_server(['10.1.2.3', '192.168.0.0/16', '172.16.0.0/24'])
        for address, expected in (('10.1.2.3', True), ('10.1.2.4', False), ('192.168.0.1', True),
                                  ('192.168.255.254', True), ('192.169.0.1', False), ('127.0.0.1', False),
                                  ('172.15.255.255', False), ('172.16.0.0', True), ('172.16.0.255', True),
                                  ('172.16.1.0', False)):
            self.assertEqual(tcp_server.is_allowed((address, 1234)), expected, address)

    def test_ipv6_match(self) -> None:
        tcp_server = self._tcp_server(['::1', 'fd00::/8'])
        for address, expected in (('::1', True), ('::2', False), ('fd12:3456::1', True), ('fe80::1', False)):
            self.assertEqual(tcp_server.is_allowed((address, 1234, 0, 0)), expected, address)
        # the IP versions are never mixed
        self.assertFalse(tcp_server.is_allowed(('127.0.0.1', 1234)))
        self.assertFalse(self._tcp_server(['127.0.0.1']).is_allowed(('::1', 1234, 0, 0)))

    def test_ipv4_mapped_address(self) -> None:
        """An IPv4 client of a dual-stack IPv6 socket is seen as ::ffff:a.b.c.d."""
        tcp_server = self._tcp_server(['192.168.1.0/24'])
        self.assertTrue(tcp_server.is_allowed(('::ffff:192.168.1.7', 1234, 0, 0)))
        self.assertFalse(tcp_server.is_allowed(('::ffff:10.0.0.1', 1234, 0, 0)))

    def test_ipv4_mapped_entry(self) -> None:
        """An IPv4-mapped entry is converted to IPv4: the server sees such a client as IPv4, the IPv6 form never
        matched it (S-09)."""
        server = ModbusServer(allowed_hosts=['::ffff:192.168.1.10', '::ffff:10.0.0.0/104'])
        self.assertEqual(server.allowed_hosts, ('192.168.1.10/32', '10.0.0.0/8'))
        tcp_server = self._tcp_server(['::ffff:192.168.1.10', '::ffff:10.0.0.0/104'])
        for address, expected in (('192.168.1.10', True), ('::ffff:192.168.1.10', True), ('10.9.8.7', True),
                                  ('::ffff:10.9.8.7', True), ('192.168.1.11', False), ('::ffff:192.168.1.11', False)):
            client = (address, 1234, 0, 0) if ':' in address else (address, 1234)
            self.assertEqual(tcp_server.is_allowed(client), expected, address)
        self.assertEqual(ModbusServer(allowed_hosts=['::ffff:0:0/96']).allowed_hosts, ('0.0.0.0/0',))

    def test_link_local_entry_is_refused(self) -> None:
        """An allow list can't tell the interface of a link-local client: a link-local entry (or any entry with an
        interface) is refused, it would allow every interface (S-09)."""
        for entry in ('fe80::/10', 'fe80::1', 'fe80::/64', 'febf::1'):
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'link-local address'):
                ModbusServer(allowed_hosts=[entry])
        for entry in ('fe80::1%eth0', '2001:db8::1%eth0', '::1%lo'):
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'interface'):
                ModbusServer(allowed_hosts=[entry])
        # the same rule for every allow list
        with self.assertRaisesRegex(ValueError, 'write_hosts item .*link-local'):
            AccessControl(MemoryStore(), write_hosts=['fe80::/10'])
        # a wider network that contains fe80::/10 is a choice of the user (every IPv6 client): kept
        self.assertEqual(ModbusServer(allowed_hosts=['::/0']).allowed_hosts, ('::/0',))
        # the zone id of a client is dropped for the match: a link-local client only matches a wider network
        self.assertTrue(self._tcp_server(['::/0']).is_allowed(('fe80::1%eth0', 1234, 0, 0)))
        self.assertFalse(self._tcp_server(['::1']).is_allowed(('fe80::1%eth0', 1234, 0, 0)))

    def test_invalid_client_address_is_refused(self) -> None:
        tcp_server = self._tcp_server(['127.0.0.1'])
        for address in ('', 'not an ip', '1.2.3', '127.0.0.1\n'):
            self.assertFalse(tcp_server.is_allowed((address, 1234)), repr(address))


class TestAllowedHostsIPv6(unittest.TestCase):
    """Tests of allowed_hosts on a real IPv6 connection."""

    def _connect(self, **server_kwargs: Any) -> bytes:
        server = ModbusServer(host='::1', port=0, ipv6=True, no_block=True, **server_kwargs)
        _host, port = server.start()
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
    def test_listed_client_is_served(self) -> None:
        self.assertEqual(self._connect(allowed_hosts=['::1']), struct.pack('>HHHBBBH', 1, 0, 5, 1, 3, 2, 0))

    @unittest.skipUnless(ipv6_available(), 'IPv6 loopback not available on this machine')
    def test_unlisted_client_is_closed(self) -> None:
        with self.assertLogs('pyModbusTCP.server', level='WARNING'):
            self.assertEqual(self._connect(allowed_hosts=['fd00::/8', '127.0.0.1']), b'')


class TestLogThrottle(unittest.TestCase):
    """ LogThrottle: one message per interval, with the number of events. """

    def setUp(self) -> None:
        self.log = logging.getLogger('pyModbusTCP.test.throttle')

    def test_named_fields(self) -> None:
        """%(count)d at any position of the message, the other named fields from the keyword arguments."""
        throttle = LogThrottle(self.log, interval=10.0)
        with self.assertLogs(self.log, level='WARNING') as logs:
            # the first event at once, with the count at the end; a % in a value is never a field
            throttle.event('refused from %(host)s: %(count)d', host='100%')
            for _ in range(4):
                throttle.event('refused from %(host)s: %(count)d', host='x')
            self.assertEqual([r.getMessage() for r in logs.records], ['refused from 100%: 1'])
            # after the interval: the count of the events since the last message, here at the start
            assert throttle._last_time is not None
            throttle._last_time -= throttle.interval
            throttle.event('%(count)d event(s) (limit %(limit)d, last from %(host)s)', limit=16, host='y')
            # no field but the count
            throttle._last_time -= throttle.interval
            throttle.event('%(count)d event(s)')
        self.assertEqual([r.getMessage() for r in logs.records[1:]],
                         ['5 event(s) (limit 16, last from y)', '1 event(s)'])
        # count is the number of events, never a field: refused at once, even when the message would not be logged
        with self.assertRaisesRegex(TypeError, 'count'):
            throttle.event('%(count)d event(s)', count=3)

    def test_exc_info(self) -> None:
        throttle = LogThrottle(self.log, level=logging.ERROR)
        with self.assertLogs(self.log, level='ERROR') as logs:
            try:
                raise RuntimeError('boom')
            except RuntimeError:
                throttle.event('error in handler (%(count)d in total)', exc_info=True)
        self.assertEqual(logs.records[0].getMessage(), 'error in handler (1 in total)')
        self.assertIn('RuntimeError: boom', logs.output[0])


if __name__ == '__main__':
    unittest.main()
