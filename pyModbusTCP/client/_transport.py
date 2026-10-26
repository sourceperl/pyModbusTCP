""" pyModbusTCP client: TCP layer (connection, send/receive of the Modbus/TCP frames, frames trace). """

import logging
import random
import select
import socket
import struct
import sys
import time
from binascii import hexlify
from socket import AF_UNSPEC, IPPROTO_TCP, SOCK_STREAM, TCP_NODELAY
from typing import TYPE_CHECKING

from ..constants import (
    MB_CONNECT_ERR,
    MB_FRAME_ERR,
    MB_RECV_ERR,
    MB_SEND_ERR,
    MB_SOCK_CLOSE_ERR,
    MB_TIMEOUT_ERR,
    MBAP_MAX_LENGTH,
    MBAP_MIN_LENGTH,
    MBAP_SIZE,
)
from ._common import logger
from ._errors import ModbusExcept, NetworkError
from ._validators import MAX_CONNECT_RETRY_DELAY

# broadcast requests whose late reply is ignored (see _recv_pdu()): the last ones sent, for a limited time
BROADCAST_IDS_MAX = 128
BROADCAST_REPLY_DELAY = 10.0
# errors of a send/recv on a connection that the server has reset or no longer knows (it didn't read the request)
# (exception classes, not errno values: they also match the WSAExxx codes of Windows)
CONN_LOST_ERRORS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


class ClientTransportMixin:
    """TCP layer of ModbusClient: open/close the connection, send a request PDU and receive the reply PDU.

    The frames are checked here (MBAP header, function code, exception), not their content.
    """

    if TYPE_CHECKING:
        # settings: properties of ModbusClient (read-only here, a variable would be an incompatible override of them)
        @property
        def host(self) -> str: ...
        @property
        def port(self) -> int: ...
        @property
        def unit_id(self) -> int: ...
        @property
        def timeout(self) -> float: ...
        @property
        def auto_open(self) -> bool: ...
        @property
        def auto_close(self) -> bool: ...
        @property
        def connect_retries(self) -> int: ...
        @property
        def connect_retry_delay(self) -> float: ...

    # set by ModbusClient (settings and connection state)
    _tcp_no_delay: bool
    _sock: socket.socket | None
    _deadline: float
    _rx_count: int
    _conn_reused: bool
    _transaction_id: int
    # transaction id -> time of the send (time.monotonic()) of the last broadcast requests, the oldest first: a late
    # reply to one of them is ignored (see _recv_pdu())
    _broadcast_ids: dict[int, float]
    _broadcast_interval: float
    _last_broadcast: float

    @property
    def is_open(self) -> bool:
        """Get current status of the TCP connection (True = open).

        A connection closed or reset by the server (server stopped, idle timeout...) is seen as closed: it is
        closed on the client side too. A server gone without closing it (power cut, cable pulled) can't be seen:
        the connection stays open until a request fails on it.
        """
        if not self._sock_open:
            return False
        if self._closed_by_peer():
            self._debug_msg('connection closed by the server')
            self.close()
            return False
        return True

    @property
    def _sock_open(self) -> bool:
        """Return True if the client socket is open (local state only, the server may have closed the connection)."""
        if self._sock is None:
            return False
        try:
            return self._sock.fileno() >= 0
        except (AttributeError, OSError):
            return False

    def _open(self) -> None:
        """Connect to modbus server (open TCP connection), with up to connect_retries new attempts on failure.

        Only the connection is retried, never a request: a write request sent twice could be executed twice.
        """
        attempt = 0
        while True:
            try:
                self._open_once()
                if attempt:
                    self._debug_msg(f'connected after {attempt} retr{"y" if attempt == 1 else "ies"}')
                return
            except NetworkError as e:
                if not e.retryable or attempt >= self.connect_retries:
                    raise
                delay = self._connect_retry_wait(attempt)
                attempt += 1
                self._debug_msg(f'{e.message}: retry {attempt}/{self.connect_retries} in {delay:.3f} s')
                time.sleep(delay)

    def _connect_retry_wait(self, attempt: int) -> float:
        """Delay in seconds before the connect retry number attempt + 1 (exponential backoff with jitter).

        The base delay doubles at each retry (capped at MAX_CONNECT_RETRY_DELAY, the max of connect_retry_delay), the
        wait is a random value between half and all of it: many clients disconnected at once by a server restart don't
        retry in sync.
        """
        # (exponent capped: 2.0 ** 32 already exceeds any cap, a large one would be a float overflow)
        base = min(self.connect_retry_delay * 2.0 ** min(attempt, 32), MAX_CONNECT_RETRY_DELAY)
        return base / 2 + random.uniform(0.0, base / 2)

    def _open_once(self) -> None:
        """Connect to modbus server (open TCP connection): one attempt."""
        # open an already open socket -> reset it
        if self._sock_open:
            self.close()
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        try:
            addr_infos = socket.getaddrinfo(self.host, self.port, AF_UNSPEC, SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as e:
            # name resolution failure: report it like any other connect error (no exception for the caller)
            # (UnicodeError: a name that the IDNA codec can't encode, like a label too long once encoded)
            # only a temporary DNS failure (EAI_AGAIN) is worth a retry, an unknown name stays unknown
            retryable = isinstance(e, socket.gaierror) and e.errno == socket.EAI_AGAIN
            raise NetworkError(MB_CONNECT_ERR, f'host name resolution error: {e}', retryable=retryable) from None
        # try each address in turn, keep the last failure reason for the error message
        last_err: OSError | None = None
        for af, sock_type, proto, _canon_name, sa in addr_infos:
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except OSError as e:
                last_err = e
                continue
            try:
                self._sock.settimeout(self.timeout)
                # disable Nagle's algorithm: modbus requests are small frames
                # that expect an immediate reply (request/response pattern)
                self._set_sock_tcp_no_delay(self._tcp_no_delay)
                self._sock.connect(sa)
            except OSError as e:
                last_err = e
                self.close()
                continue
            break
        # check connect status
        if not self._sock_open:
            reason = 'connection refused' if last_err is None else f'connect error: {last_err}'
            raise NetworkError(MB_CONNECT_ERR, reason, retryable=True)

    def _closed_by_peer(self) -> bool:
        """Return True if the server has closed (FIN) or reset (RST) the open connection, without blocking.

        A connection closed by the server stays open on the client side until a send/recv fails: this check finds it
        (for is_open and before a request is sent on it). The bytes waiting on the socket (if any) are not read.
        """
        if self._sock is None:
            return True
        try:
            # poll(): no limit on the fd value, select() only on Windows (no poll(), but no such limit either)
            # (a sys.platform test, not hasattr(): the type checkers know that poll() is missing on Windows)
            if sys.platform == 'win32':
                readable = bool(select.select([self._sock], [], [], 0)[0])
            else:
                poller = select.poll()
                poller.register(self._sock, select.POLLIN)
                readable = bool(poller.poll(0))
            # nothing to read: the connection is alive (or the server is gone without a FIN, nothing can tell)
            if not readable:
                return False
            # readable: some data (a late reply, kept for _recv_pdu()), an end of stream (b'') or an error
            return self._sock.recv(1, socket.MSG_PEEK) == b''
        except (OSError, ValueError):
            return True

    def _open_for_request(self) -> bool:
        """For auto_open mode: open the connection if it's closed, or if the server has closed it.

        :returns: True if the connection was already open and is reused, False if it's a new one
        """
        if self._sock_open:
            if not self._closed_by_peer():
                return True
            self._debug_msg('connection closed by the server: reconnect')
            self.close()
        self._open()
        return False

    def close(self) -> None:
        """Close current TCP connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
        # no late reply to a broadcast can come on a new connection
        self._broadcast_ids.clear()

    def _set_sock_timeout(self, value: float) -> None:
        """Set the current socket timeout, but only if it differs by more than 10 ms from the current one.

        Each settimeout() is a system call: in the usual case (response received at once) the timeout in place
        is already (almost) the right one, so there is nothing to do.

        :param value: socket timeout in seconds
        :type value: float
        """
        # skip for unset socket
        if self._sock is None:
            return
        # apply timeout if diff is great than 10ms
        current = self._sock.gettimeout()
        if current is None or abs(current - value) > 0.01:
            self._sock.settimeout(value)

    def _set_sock_tcp_no_delay(self, value: bool) -> None:
        # skip for unset socket
        if self._sock is None:
            return
        # apply tcp_nodelay value
        try:
            self._sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, int(value))
        except OSError as e:
            logger.debug('Unable to change TCP_NODELAY on the open connection: %r', e)

    def _send(self, frame: bytes) -> None:
        """Send frame over current socket.

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: bytes
        """
        # check socket status
        if self._sock is None or not self._sock_open:
            raise NetworkError(MB_SOCK_CLOSE_ERR, 'try to send on a close socket')
        # the timeout is a limit for the whole request (send + receive), not for each recv() call:
        # a slow server sending its response byte by byte can't hold the client more than timeout seconds
        self._deadline = time.monotonic() + self.timeout
        # send
        try:
            # restore the full timeout (it's reduced by _recv() to the time remaining in the previous request)
            self._set_sock_timeout(self.timeout)
            self._sock.sendall(frame)
        except TimeoutError:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError as e:
            self.close()
            # a modbus frame is sent in one send() call: if it fails, nothing of it was sent
            raise NetworkError(MB_SEND_ERR, f'send error: {e}', retryable=isinstance(e, CONN_LOST_ERRORS)) from None
        self._rx_count = 0

    def _send_pdu(self, pdu: bytes) -> None:
        """Convert modbus PDU to frame and send it.

        _conn_reused is set to True if the request is sent on a connection already open before it.

        :param pdu: modbus frame PDU
        :type pdu: bytes
        """
        # for auto_open mode, check TCP and open on need (also if the server has closed it)
        self._conn_reused = self._open_for_request() if self.auto_open else self._sock_open
        # add MBAP header to PDU
        tx_frame = self._add_mbap(pdu)
        # send frame with error check
        self._send(tx_frame)
        # debug
        self._on_tx_rx(frame=tx_frame, is_tx=True)

    def _send_broadcast(self, pdu: bytes) -> None:
        """Send a request PDU to the unit id 0 (broadcast), without waiting for a reply.

        :param pdu: modbus frame PDU (a write function)
        :type pdu: bytes
        """
        # for auto_open mode, check TCP and open on need
        # never faster than broadcast_interval: a serial line can't take broadcasts faster than its slaves execute them
        # (perf_counter(): monotonic too, but precise on every OS, monotonic() has a 15.6 ms step on Windows before
        # Python 3.13, a gap could be that much shorter)
        wait = self._last_broadcast + self._broadcast_interval - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        if self.auto_open:
            self._open_for_request()
        tx_frame = self._add_mbap(pdu, unit_id=0)
        self._send(tx_frame)
        # a gateway may reply anyway (an echo, an exception after its timeout): see _recv_pdu()
        self._broadcast_ids.pop(self._transaction_id, None)
        self._last_broadcast = time.perf_counter()
        self._broadcast_ids[self._transaction_id] = time.monotonic()
        while len(self._broadcast_ids) > BROADCAST_IDS_MAX:
            del self._broadcast_ids[next(iter(self._broadcast_ids))]
        self._on_tx_rx(frame=tx_frame, is_tx=True)
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()

    def _recv(self, size: int) -> bytes:
        """Receive data over current socket (at most size bytes).

        :param size: max number of bytes to receive
        :type size: int
        :returns: receive data (never empty)
        :rtype: bytes
        :raises NetworkError: on timeout, closed or broken connection
        """
        # check socket status
        if self._sock is None or not self._sock_open:
            raise NetworkError(MB_SOCK_CLOSE_ERR, 'try to recv on a close socket')
        # time remaining before the end of the request
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        try:
            self._set_sock_timeout(remaining)
            r_buffer = self._sock.recv(size)
        except TimeoutError:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError as e:
            self.close()
            # reset before any byte of the reply: the server (or its TCP stack after a reboot) didn't read the request
            # (an end of stream b'' is not enough: the server may have executed the request, then closed)
            retryable = self._rx_count == 0 and isinstance(e, CONN_LOST_ERRORS)
            raise NetworkError(MB_RECV_ERR, f'recv error: {e}', retryable=retryable) from None
        # handle recv error
        if not r_buffer:
            self.close()
            raise NetworkError(MB_RECV_ERR, 'recv error: connection closed by the server')
        self._rx_count += len(r_buffer)
        return r_buffer

    def _recv_all(self, size: int) -> bytes:
        """Receive data over current socket, loop until all bytes is received (avoid TCP frag).

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data
        :rtype: bytes
        """
        r_buffer = b''
        while len(r_buffer) < size:
            r_buffer += self._recv(size - len(r_buffer))
        return r_buffer

    def _recv_pdu(self, func_code: int, min_len: int = 2) -> bytes:
        """Receive the modbus PDU (Protocol Data Unit).

        :param func_code: function code of the request (the reply must have the same)
        :type func_code: int
        :param min_len: minimal length of the PDU
        :type min_len: int
        :returns: modbus frame PDU
        :rtype: bytes
        """
        # receive 7 bytes header (MBAP), skip the late replies to a broadcast
        while True:
            rx_mbap = self._recv_all(MBAP_SIZE)
            (f_transaction_id, f_protocol_id, f_length, f_unit_id) = struct.unpack('>HHHB', rx_mbap)
            if not self._is_broadcast_reply(f_transaction_id, f_protocol_id, f_length, f_unit_id):
                break
            # a reply to a broadcast request (unit id 0) sent before this one: read and ignored
            rx_pdu = self._recv_all(f_length - 1)
            del self._broadcast_ids[f_transaction_id]
            self._on_tx_rx(frame=rx_mbap + rx_pdu, is_tx=False)
            self._debug_msg(f'late reply to the broadcast request {f_transaction_id} ignored')
        # check MBAP fields
        f_transaction_err = f_transaction_id != self._transaction_id
        f_protocol_err = f_protocol_id != 0
        f_length_err = not MBAP_MIN_LENGTH <= f_length <= MBAP_MAX_LENGTH
        f_unit_id_err = f_unit_id != self.unit_id
        # checking error status of fields: the stream can't be trusted anymore, close it
        if f_transaction_err or f_protocol_err or f_length_err or f_unit_id_err:
            self.close()
            self._on_tx_rx(frame=rx_mbap, is_tx=False)
            raise NetworkError(MB_FRAME_ERR, 'MBAP checking error')
        # recv PDU
        rx_pdu = self._recv_all(f_length - 1)
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()
        # dump frame
        self._on_tx_rx(frame=rx_mbap + rx_pdu, is_tx=False)
        # body decode
        # check PDU length for global minimal frame (an except frame: func code + exp code)
        if len(rx_pdu) < 2:
            raise NetworkError(MB_FRAME_ERR, 'PDU length is too short')
        # extract function code: it must be the one of the request (with or without the exception flag)
        rx_fc = rx_pdu[0]
        if rx_fc & 0x7f != func_code:
            raise NetworkError(MB_FRAME_ERR,
                               f'function code mismatch (0x{rx_fc:02X} for a 0x{func_code:02X} request)')
        # check except status
        if rx_fc >= 0x80:
            exp_code = rx_pdu[1]
            raise ModbusExcept(exp_code)
        # check PDU length for specific request set in min_len (keep this after except checking)
        if len(rx_pdu) < min_len:
            raise NetworkError(MB_FRAME_ERR, 'PDU length is too short for current request')
        # if no error, return PDU
        return rx_pdu

    def _is_broadcast_reply(self, transaction_id: int, protocol_id: int, length: int, unit_id: int) -> bool:
        """Return True for the MBAP header of a late reply to a recent broadcast request (every field must match).

        A broadcast request is recent for max(timeout, BROADCAST_REPLY_DELAY) seconds after its send.
        """
        sent = self._broadcast_ids.get(transaction_id)
        if sent is None or transaction_id == self._transaction_id:
            return False
        recent = time.monotonic() - sent <= max(self.timeout, BROADCAST_REPLY_DELAY)
        return recent and unit_id == 0 and protocol_id == 0 and MBAP_MIN_LENGTH <= length <= MBAP_MAX_LENGTH

    def _add_mbap(self, pdu: bytes, unit_id: int | None = None) -> bytes:
        """Return full modbus frame with MBAP (modbus application protocol header) append to PDU.

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: bytes
        :param unit_id: unit id of the frame (default: the one of the client)
        :type unit_id: int or None
        :returns: full modbus frame
        :rtype: bytes
        """
        # build MBAP
        # next transaction ID (16 bits counter): it always differs from the previous request one, so a late reply
        # of the previous request (left on the socket) is always detected as such and never taken for the current one
        self._transaction_id = (self._transaction_id + 1) & 0xffff
        protocol_id = 0
        length = len(pdu) + 1
        unit_id = self.unit_id if unit_id is None else unit_id
        mbap = struct.pack('>HHHB', self._transaction_id, protocol_id, length, unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _debug_msg(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f'({self.host}:{self.port}:{self.unit_id}) {msg}')

    def _on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        # format a log message
        if logger.isEnabledFor(logging.DEBUG):
            type_s = 'Tx' if is_tx else 'Rx'
            mbap_s = hexlify(frame[0:7], sep=' ').upper().decode()
            pdu_s = hexlify(frame[7:], sep=' ').upper().decode()
            self._debug_msg(f'{type_s} [{mbap_s}] {pdu_s}')
        # notify user
        self.on_tx_rx(frame=frame, is_tx=is_tx)

    def on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        """Call for each Tx/Rx (for user purposes)."""
