""" pyModbusTCP server: TCP layer (threaded TCP server and per connection request handler). """

from __future__ import annotations

import socket
import sys
import time
from collections.abc import Callable
from socketserver import BaseRequestHandler, ThreadingTCPServer
from threading import Lock
from typing import Any, cast

from ._common import LogThrottle, _IPNetwork, client_ip, host_key, logger
from .errors import Error, NetworkError
from .session import SessionData


class CustomThreadingTCPServer(ThreadingTCPServer):
    """Threaded TCP server."""
    address_family = socket.AF_INET
    # SO_REUSEADDR: on POSIX systems, a restarted server can listen at once, even with connections of the previous
    # one still in TIME_WAIT (another socket can't bind a port in the LISTEN state). On Windows, it means something
    # else: it allows another socket to bind the port in use and take its connections (port hijacking), and opens the
    # port to the sockets of the other user accounts. So, on Windows, the port is reserved with SO_EXCLUSIVEADDRUSE
    # instead (see server_bind()).
    allow_reuse_address = sys.platform != 'win32'
    daemon_threads: bool = True
    # listen backlog: the socketserver default (5) makes a burst of simultaneous connections wait for a TCP SYN
    # retransmission (1 s or more) as soon as the accept loop is a bit late
    request_queue_size: int = 128
    # these 4 settings are set by ModbusServer.start() (see ModbusServer.__init__() for a description)
    max_connections: int | None = None
    max_connections_per_host: int | None = None
    idle_timeout: float | None = None
    request_timeout: float | None = None
    # whitelist of allowed client networks (None: every client is allowed), set by ModbusServer.start()
    allowed_networks: tuple[_IPNetwork, ...] | None = None
    engine: Callable[[SessionData], None]

    def __init__(self, *args: Any, ipv6: bool = False, **kwargs: Any) -> None:
        # must be set before ThreadingTCPServer.__init__(), which creates the socket from it
        self.address_family = socket.AF_INET6 if ipv6 else socket.AF_INET
        # active sessions (a session = a client TCP connection with its thread): socket -> client address
        self._active_sessions: dict[socket.socket, Any] = {}
        # number of active sessions of each client host (see host_key()), protected by the sessions lock
        self._host_sessions: dict[str, int] = {}
        self._sessions_lock = Lock()
        # refused connections: the first one is logged at once, then one message per 10 s at most (a connection
        # flood must not flood the log)
        self._denied_log = LogThrottle(logger)
        self._rejected_log = LogThrottle(logger)
        self._host_rejected_log = LogThrottle(logger)
        super().__init__(*args, **kwargs)

    def server_bind(self) -> None:
        # Windows: no other socket (of this process, of another one, of another user) can bind this port
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    @property
    def _sessions(self) -> int:
        """Number of active sessions."""
        return len(self._active_sessions)

    def active_sessions(self) -> list[tuple[socket.socket, Any]]:
        """Return a snapshot of the active sessions as a list of (socket, client address)."""
        with self._sessions_lock:
            return list(self._active_sessions.items())

    def shutdown_sessions(self) -> int:
        """Call shutdown() on the socket of every active session.

        Each session thread sees its connection closed on the next recv() and ends cleanly (see
        ModbusRequestHandler.handle()). Sessions are not removed here: their own thread does it when it ends.

        :return: number of sessions the shutdown was requested for
        """
        # snapshot: don't hold the lock while calling the sockets, session threads need it to end
        sessions = self.active_sessions()
        for sock, client_address in sessions:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                # already closed or disconnected by the peer: nothing more to do
                logger.debug('Session shutdown for %r: %r', client_address, e)
        return len(sessions)

    def is_allowed(self, client_address: Any) -> bool:
        """Return True if the client address is accepted by the allowed hosts whitelist (if any)."""
        if self.allowed_networks is None:
            return True
        # zone id dropped, an IPv4-mapped address (IPv4 client of a dual-stack socket) seen as IPv4
        ip = client_ip(client_address)
        if ip is None:
            return False
        return any(ip in network for network in self.allowed_networks)

    def _add_session(self, request: Any, client_address: Any) -> None:
        with self._sessions_lock:
            self._active_sessions[request] = client_address
            key = host_key(client_address)
            self._host_sessions[key] = self._host_sessions.get(key, 0) + 1

    def _remove_session(self, request: Any) -> None:
        with self._sessions_lock:
            client_address = self._active_sessions.pop(request, None)
            if client_address is None:
                return
            key = host_key(client_address)
            remaining = self._host_sessions.get(key, 0) - 1
            if remaining > 0:
                self._host_sessions[key] = remaining
            else:
                self._host_sessions.pop(key, None)

    def verify_request(self, request: Any, client_address: Any) -> bool:
        # called by the main server thread, the only one that add sessions (so these checks are safe: the
        # counters can only decrease meanwhile, when a session ends)
        if not self.is_allowed(client_address):
            self._denied_log.event('Connection refused, host not in allowed hosts: %d connection(s) refused '
                                   '(last from %r)', client_address)
            return False
        if self.max_connections is not None and self._sessions >= self.max_connections:
            self._rejected_log.event('Maximum number of connections reached: %d connection(s) rejected '
                                     '(limit %d, last from %r)', self.max_connections, client_address)
            return False
        if self.max_connections_per_host is not None:
            key = host_key(client_address)
            with self._sessions_lock:
                host_sessions = self._host_sessions.get(key, 0)
            if host_sessions >= self.max_connections_per_host:
                self._host_rejected_log.event('Maximum number of connections per host reached: %d connection(s) '
                                              'rejected (limit %d, last from %s)',
                                              self.max_connections_per_host, key)
                return False
        return True

    def process_request(self, request: Any, client_address: Any) -> None:
        self._add_session(request, client_address)
        try:
            super().process_request(request, client_address)
        except BaseException:
            # the session thread can't start: no session
            self._remove_session(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._remove_session(request)


class ModbusRequestHandler(BaseRequestHandler):
    # default socket timeout (in s) on blocking operations
    _SOCKET_TIMEOUT: float = 1.0
    # deadline in effect at the end of the last _recv_all() call (see handle())
    _last_deadline: float | None = None

    def _send_all(self, data: bytes) -> None:
        try:
            self.request.sendall(data)
        except TimeoutError:
            # sendall() may have sent only a part of the frame, the TCP stream is now out of sync:
            # raise an error to close this session (see handle())
            raise NetworkError('timeout on send, close session')

    def _check_and_update_timeout(self, deadline: float | None, timeout_msg: str) -> bool:
        """Check the deadline (on a system call is only needed when it is near)"""
        sock_timeout_changed = False
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkError(timeout_msg)
            if remaining < self._SOCKET_TIMEOUT:
                self.request.settimeout(remaining)
                sock_timeout_changed = True
        return sock_timeout_changed

    def _recv_chunk(self, size: int, data_len: int, deadline: float | None, timeout_msg: str,
                    request_timeout: float | None) -> tuple[bytes, float | None, str, bool]:
        sock_timeout_changed = self._check_and_update_timeout(deadline, timeout_msg)

        # recv all data or a chunk of it
        data_chunk = self.request.recv(size - data_len)

        # check data chunk
        if data_chunk:
            if data_len == 0 and request_timeout is not None:
                # the request is started, the idle wait is over
                deadline = time.monotonic() + request_timeout
                timeout_msg = 'request timeout'
            return data_chunk, deadline, timeout_msg, sock_timeout_changed
        else:
            raise NetworkError('recv return null')

    def _recv_all(self, size: int, deadline: float | None = None, timeout_msg: str = 'recv timeout',
                  request_timeout: float | None = None) -> bytes:
        """Receive size bytes (loop until all bytes are received).

        :param deadline: limit for the end of reception (a time.monotonic() value), None for no limit
        :param timeout_msg: message of the error raised if the deadline is reached
        :param request_timeout: if set, the deadline is replaced by "now + request_timeout" as soon as the first
            data is received (used to wait for a new request, without limit or with an idle deadline, then limit
            the time taken to receive it)
        :raises NetworkError: on deadline, on connection closed or if the main server is stopped
        """
        data = b''
        sock_timeout_changed = False
        try:
            while len(data) < size:
                try:
                    data_chunk, deadline, timeout_msg, timeout_changed = self._recv_chunk(
                        size, len(data), deadline, timeout_msg, request_timeout
                    )
                    if timeout_changed:
                        sock_timeout_changed = True
                    data += data_chunk
                except TimeoutError:
                    # just redo main server run test, deadline test and recv operations on timeout
                    pass
        finally:
            # restore the default timeout used by the other socket operations
            if sock_timeout_changed:
                self.request.settimeout(self._SOCKET_TIMEOUT)
        # the deadline in effect at the end (with request_timeout: the end of the request that has started)
        self._last_deadline = deadline
        return data

    def setup(self) -> None:
        # set a socket timeout of 1s on blocking operations (like send/recv)
        # this avoids hang thread deletion when main server exit (see _recv_all method)
        self.request.settimeout(self._SOCKET_TIMEOUT)

    def handle(self) -> None:
        # try/except: end current thread on ModbusServer._InternalError, OSError or socket.error
        # this also close the current TCP session associated with it
        session_data = SessionData()
        # the server that created this handler (BaseRequestHandler.server is typed BaseServer)
        server = cast(CustomThreadingTCPServer, self.server)
        try:
            # update server info structure
            (session_data.client.address, session_data.client.port) = self.request.getpeername()[:2]
            # debug message
            logger.debug('Accept new connection from %r', session_data.client)
            # main processing loop
            while True:
                # init session data for new request
                session_data.new_request()
                # receive mbap from client:
                # - wait for a new request, the session is closed after idle_timeout without data (if set)
                # - then the rest of the header must come in request_timeout (avoid a stalled request)
                idle_timeout = server.idle_timeout
                request_timeout = server.request_timeout
                idle_deadline = None if idle_timeout is None else time.monotonic() + idle_timeout
                session_data.request.mbap.raw = self._recv_all(7, idle_deadline, 'idle timeout', request_timeout)
                # receive pdu from client: the whole request (MBAP header and PDU) must come in request_timeout
                # from its first byte (a new delay for the PDU would let a client hold a session twice as long)
                req_deadline = None if request_timeout is None else self._last_deadline
                session_data.request.pdu.raw = self._recv_all(session_data.request.mbap.length - 1, req_deadline,
                                                              'request timeout')
                # update response MBAP fields with request data
                session_data.set_response_mbap()
                # pass the current session data to request engine
                server.engine(session_data)
                # send the tx pdu with the last rx mbap (only length field change)
                self._send_all(session_data.response.raw)
        except (Error, OSError) as e:
            # debug message
            logger.debug('Exception %r for %r', e, session_data.client)
            # on main loop except: exit from it and cleanly close the current socket
            self.request.close()
