""" pyModbusTCP server: the ModbusServer class (configuration, start/stop and request engine). """

from __future__ import annotations

import socket
import warnings
from threading import RLock, Thread, current_thread
from typing import Callable, Iterable, List, Optional, Tuple, TypeAlias

from ..constants import (
    EXP_DATA_VALUE,
    EXP_ILLEGAL_FUNCTION,
    EXP_SLAVE_DEVICE_FAILURE,
    WRITE_FUNCTION_CODES,
)
from . import _transport, _validators, errors, frames, session
from ._common import logger
from ._functions import ServerFunctionsMixin
from ._transport import CustomThreadingTCPServer
from .data_bank import DataBank
from .data_handler import DataHandler
from .device_id import DeviceIdentification
from .errors import Error, NetworkError, PDUDecodeError


class ModbusServer(ServerFunctionsMixin):
    """ Modbus TCP server """

    # interval (in s) at which the main server loop checks for a stop request: this is the max latency of stop()
    _SERVE_POLL_INTERVAL: float = 0.05

    # historical nested names (ModbusServer.Error, ModbusServer.SessionData...): the classes live in submodules
    Error: TypeAlias = errors.Error
    NetworkError: TypeAlias = errors.NetworkError
    DataFormatError: TypeAlias = errors.DataFormatError
    PDUDecodeError: TypeAlias = errors.PDUDecodeError
    ClientInfo: TypeAlias = session.ClientInfo
    ServerInfo: TypeAlias = session.ServerInfo
    SessionData: TypeAlias = session.SessionData
    Frame: TypeAlias = frames.Frame
    MBAP: TypeAlias = frames.MBAP
    PDU: TypeAlias = frames.PDU
    CustomThreadingTCPServer: TypeAlias = _transport.CustomThreadingTCPServer
    ModbusRequestHandler: TypeAlias = _transport.ModbusRequestHandler

    # kept for compatibility (allowed hosts check used outside of this class)
    _validate_allowed_hosts = staticmethod(_validators.validate_allowed_hosts)

    def __init__(self, host: str = 'localhost', port: int = 502, no_block: bool = False, ipv6: bool = False,
                 data_bank: Optional[DataBank] = None, data_hdl: Optional[DataHandler] = None,
                 ext_engine: Optional[Callable[[session.SessionData], None]] = None,
                 device_id: Optional[DeviceIdentification] = None,
                 request_timeout: Optional[float] = 30.0, idle_timeout: Optional[float] = 300,
                 max_connections: Optional[int] = 64, max_connections_per_host: Optional[int] = 16,
                 read_only: bool = False, allowed_hosts: Optional[Iterable[str]] = None) -> None:
        """Initialize a Modbus TCP server.

        Sets up a Modbus TCP server with configurable network parameters, data handling,
        and optional device identification. Type validation is performed on critical
        parameters to prevent silent runtime failures.


        :param host: hostname or IPv4/IPv6 address to listen on (default is 'localhost')
        :type host: str
        :param port: TCP port number (default is 502), use 0 to let the OS choose a free port
        :type port: int
        :param no_block: no block mode, i.e. start() will return (default is False)
        :type no_block: bool
        :param ipv6: use ipv6 stack (default is False)
        :type ipv6: bool
        :param data_bank: instance of custom data bank, if you don't want the default one (optional)
        :type data_bank: DataBank
        :param data_hdl: instance of custom data handler, if you don't want the default one (optional)
        :type data_hdl: DataHandler
        :param ext_engine: an external engine reference (ref to ext_engine(session_data)) (optional)
        :type ext_engine: callable
        :param device_id: instance of DeviceIdentification class for read device identification request (optional)
        :type device_id: DeviceIdentification
        :param request_timeout: max time in seconds to receive a request once it has started (the MBAP header, then\
            the PDU: a stalled request), on expiry the client session is closed, None to disable (default is 30.0)
        :type request_timeout: float or None
        :param idle_timeout: max time in seconds without any request from a client before closing its session,\
            None to disable (default is 300.0)
        :type idle_timeout: float or None
        :param max_connections: max number of simultaneous client connections, the next ones are closed immediately,\
            None for no limit (default is 64)
        :type max_connections: int or None
        :param read_only: refuse every request that writes data (functions 0x05, 0x06, 0x0F, 0x10 and 0x17) with an\
            "illegal function" exception, only the internal engine is concerned (default is False)
        :type read_only: bool
        :param allowed_hosts: whitelist of the clients allowed to connect: IP addresses or networks in CIDR notation\
            (like ['192.168.1.10', '10.0.0.0/24', '::1']), other connections are closed at once, None to accept\
            every client (default), hostnames are not supported (an empty whitelist is an error)
        :type allowed_hosts: iterable of str or None
        :param max_connections_per_host: max number of simultaneous connections from the same client host (an IPv4\
            address, or a /64 network for IPv6), the next ones are closed immediately: a single host can't take all\
            the connections of max_connections. Clients behind a NAT share the same address. None for no limit\
            (default is 16)
        :type max_connections_per_host: int or None
        """
        # validate complex objects (data storage and callbacks)
        _validators.validate_data_bank_and_handler(data_bank, data_hdl)
        _validators.validate_ext_engine(ext_engine)
        device_id = _validators.validate_device_id(device_id)

        # validate numeric parameters with constraints
        request_timeout = _validators.validate_timeout(request_timeout, 'request_timeout')
        idle_timeout = _validators.validate_timeout(idle_timeout, 'idle_timeout')
        max_connections = _validators.validate_max_connections(max_connections)
        max_connections_per_host = _validators.validate_max_connections(max_connections_per_host,
                                                                        'max_connections_per_host')
        read_only = _validators.validate_read_only(read_only)
        allowed_networks = _validators.validate_allowed_hosts(allowed_hosts)

        # store network configuration
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6

        # store timeout settings
        self.request_timeout = request_timeout
        self.idle_timeout = idle_timeout
        self.max_connections = max_connections
        self.max_connections_per_host = max_connections_per_host

        # store access control settings (the whitelist is kept as normalized networks, in CIDR notation)
        self.read_only = read_only
        self._allowed_networks = allowed_networks
        self.allowed_hosts: Optional[List[str]] = None
        if allowed_networks is not None:
            self.allowed_hosts = [str(network) for network in allowed_networks]

        # store callbacks and device info
        self.ext_engine = ext_engine
        self.device_id = device_id

        # initialize data storage (prefer data_hdl's internal bank, then explicit data_bank, then create new)
        if data_hdl:
            self.data_bank = data_hdl.data_bank
            self.data_hdl = data_hdl
        else:
            self.data_bank = data_bank or DataBank(virtual_mode=bool(ext_engine))
            self.data_hdl = DataHandler(data_bank=self.data_bank)

        # initialize internal state for server operation
        # active server instance
        self._tcp_server: Optional[CustomThreadingTCPServer] = None
        # server thread handle
        self._srv_thread: Optional[Thread] = None
        # ensure atomic start and stop operations
        # (reentrant: stop() may be called from a signal handler running in the thread that is inside start())
        self._start_stop_lock = RLock()

        # map Modbus function codes to handler methods (fixed routing table, see ServerFunctionsMixin)
        self._func_map = self._build_func_map()

    @property
    def is_run(self) -> bool:
        """Deprecated: use is_running."""
        warnings.warn('ModbusServer.is_run is deprecated, use is_running instead',
                      DeprecationWarning, stacklevel=2)
        return self.is_running

    @property
    def is_running(self) -> bool:
        """Return True if server running."""
        return self._srv_thread is not None and self._srv_thread.is_alive()

    @property
    def active_sessions(self) -> List[Tuple[str, int]]:
        """Return the (address, port) of the clients of all active sessions (empty if server is stopped)."""
        tcp_server = self._tcp_server
        if tcp_server is None:
            return []
        return [tuple(client_address[:2]) for _, client_address in tcp_server.active_sessions()]

    def shutdown_sessions(self) -> int:
        """Call shutdown() on every active session (client connection).

        The server keeps listening: new clients can still connect afterwards.
        It is automatically called when the server stops.

        :return: number of sessions the shutdown was requested for
        :rtype: int
        """
        tcp_server = self._tcp_server
        if tcp_server is None:
            return 0
        return tcp_server.shutdown_sessions()

    def __repr__(self) -> str:
        r_str = ('ModbusServer(host=\'%s\', port=%d, no_block=%s, ipv6=%s, data_bank=%s, data_hdl=%s, ext_engine=%s, '
                 'device_id=%s, request_timeout=%s, idle_timeout=%s, max_connections=%s, max_connections_per_host=%s, '
                 'read_only=%s, allowed_hosts=%s, )')
        r_str %= (self.host, self.port, self.no_block, self.ipv6, self.data_bank, self.data_hdl, self.ext_engine,
                  self.device_id, self.request_timeout, self.idle_timeout, self.max_connections,
                  self.max_connections_per_host, self.read_only, self.allowed_hosts)
        return r_str

    def _engine(self, session_data: session.SessionData) -> None:
        """Main request processing engine.

        :type session_data: SessionData
        """
        # call external engine or internal one (if ext_engine undefined)
        if callable(self.ext_engine):
            try:
                self.ext_engine(session_data)
            except Exception as e:
                logger.exception('External engine raise an exception, close session')
                raise Error('external engine raise an exception: %r' % e) from e
        else:
            self._internal_engine(session_data)

    def _internal_engine(self, session_data: session.SessionData) -> None:
        """Default internal processing engine: call default modbus func.

        :type session_data: SessionData
        """
        # session alias
        client = session_data.client
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        func_code = recv_pdu.func_code
        # a request can't have the exception flag (bit 7) set, and an exception response must fit in a byte:
        # answer "illegal function" with the same code (code | 0x80), the TCP session stays synchronized
        if func_code > 0x7F:
            logger.debug('Request with an invalid function code 0x%02X from %s', func_code, client)
            send_pdu.build_except(func_code & 0x7F, EXP_ILLEGAL_FUNCTION)
            return
        # read-only server: a function that writes data is not available
        if self.read_only and func_code in WRITE_FUNCTION_CODES:
            logger.debug('Write function 0x%02X refused (read-only mode) from %s', func_code, client)
            send_pdu.build_except(func_code, EXP_ILLEGAL_FUNCTION)
            return
        # get the ad-hoc function, if none exists (or is disabled with None), send an "illegal function" exception
        func = self._func_map.get(func_code)
        if not callable(func):
            send_pdu.build_except(func_code, EXP_ILLEGAL_FUNCTION)
            return
        # call ad-hoc func
        try:
            func(session_data)
        except PDUDecodeError as e:
            # request PDU too short or malformed: the TCP stream is still in sync, answer with "illegal data value"
            logger.debug('Malformed request for function 0x%02X: %s from %s', func_code, e, client)
            send_pdu.build_except(func_code, EXP_DATA_VALUE)
        except Error:
            # network error or other frame error: let the session handler close the connection
            raise
        except Exception:
            # unexpected error (like an exception raised by a user callback): log it and keep the session alive
            logger.exception('Unexpected error during processing of function 0x%02X from %s', func_code, client)
            send_pdu.build_except(func_code, EXP_SLAVE_DEVICE_FAILURE)

    def start(self) -> Tuple[str, int]:
        """Start the server.

        This function will block (or not if no_block flag is set).
        With port=0, the port chosen by the OS is available through the returned tuple.

        :return: A tuple containing the bound host string and port integer.
        :rtype: Tuple[str, int]
        :raises NetworkError: if the server can't listen (port already in use, bad host...)
        """
        with self._start_stop_lock:
            # do nothing if server is already running
            if self.is_running:
                raise NetworkError('server is already running')
            # init TCP server
            # always need a fresh instance since server_close() destroys the underlying socket file descriptor
            tcp_server: Optional[CustomThreadingTCPServer] = None
            try:
                # IPv4 or IPv6: handled by the server class, the socket is created in its constructor
                tcp_server = self.CustomThreadingTCPServer((self.host, self.port), self.ModbusRequestHandler,
                                                           bind_and_activate=False, ipv6=self.ipv6)
                # pass some things shared with server threads (access via self.server in ModbusRequestHandler.handle())
                tcp_server.engine = self._engine
                tcp_server.request_timeout = self.request_timeout
                tcp_server.idle_timeout = self.idle_timeout
                tcp_server.max_connections = self.max_connections
                tcp_server.max_connections_per_host = self.max_connections_per_host
                tcp_server.allowed_networks = self._allowed_networks
                # set socket options
                tcp_server.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # bind and activate
                tcp_server.server_bind()
                tcp_server.server_activate()
            except OSError as e:
                # don't keep the listening socket open after a failed start
                if tcp_server is not None:
                    tcp_server.server_close()
                raise NetworkError(e) from e
            logger.info('Listening on %s:%d', *tcp_server.server_address[:2])
            self._tcp_server = tcp_server

            # start the request processing service
            srv_thread = Thread(target=self._serve, args=(tcp_server,), daemon=True)
            self._srv_thread = srv_thread
            try:
                self._srv_thread.start()
            except BaseException:
                # unable to start thread
                tcp_server.server_close()
                self._tcp_server = None
                self._srv_thread = None
                raise
        # block here if we are not in no_block mode
        if not self.no_block:
            srv_thread.join()
        # return current bindings to client
        host, port = tcp_server.server_address[:2]
        return str(host), int(port)

    def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for the server thread to finish (useful in non-blocking mode)."""
        srv_thread = self._srv_thread
        if srv_thread is not None:
            srv_thread.join(timeout=timeout)

    def stop(self) -> None:
        """Stop the server."""
        with self._start_stop_lock:
            # send a shutdown request to TCP server (tell the serve_forever() loop to stop and wait until it does)
            tcp_server = self._tcp_server
            if tcp_server is not None:
                tcp_server.shutdown()
            # wait for the server thread: a start() right after stop() must find a clean state
            srv_thread = self._srv_thread
            if srv_thread is not None and srv_thread is not current_thread():
                srv_thread.join()
            self._srv_thread = None
            self._tcp_server = None

    def _serve(self, tcp_server: CustomThreadingTCPServer) -> None:
        # the server is given as argument: self._tcp_server may be reset by a concurrent stop() at any time
        try:
            tcp_server.serve_forever(poll_interval=self._SERVE_POLL_INTERVAL)
        finally:
            # actively shutdown every session instead of waiting for them to notice the flag
            tcp_server.shutdown_sessions()
            tcp_server.server_close()
