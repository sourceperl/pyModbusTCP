"""Tests of the command line tools (pyModbusTCP.tools)."""

import logging
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import pytest

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.constants import VERSION
from pyModbusTCP.server import DeviceIdentification, MemoryStore, ModbusServer, Space
from pyModbusTCP.tools import _entry
from pyModbusTCP.tools import client as cli_tool
from pyModbusTCP.tools._common import is_permission_error
from pyModbusTCP.utils import crc16


def free_port() -> int:
    """Return a TCP port that is free right now on localhost."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def server() -> Iterator[tuple[ModbusServer, int]]:
    """A Modbus/TCP server on localhost (port chosen by the OS), with a device identification."""
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True,
                       device_id=DeviceIdentification(vendor_name=b'ACME', product_code=b'X\x1b[31m'))
    _, port = srv.start()
    yield srv, port
    srv.stop()


def run_client(capsys: pytest.CaptureFixture[str], args: list[str]) -> tuple[int, str, str]:
    """Run mbtcp-client, return (exit status, stdout, stderr)."""
    try:
        status = cli_tool.main(args)
    except SystemExit as e:  # argparse errors
        status = int(e.code or 0)
    out, err = capsys.readouterr()
    return status, out, err


# --- mbtcp-client ---

def test_client_read_write_registers(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    _, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    assert run_client(capsys, base + ['write-registers', '10', '1', '0x1234', '-1'])[0] == 0
    status, out, _ = run_client(capsys, base + ['read-holding', '10', '-n', '3'])
    assert status == 0
    assert out.splitlines() == ['10 1', '11 4660', '12 65535']
    assert run_client(capsys, base + ['read-holding', '10', '-n', '3', '--hex'])[1].split() == \
        ['10', '0x0001', '11', '0x1234', '12', '0xFFFF']
    assert run_client(capsys, base + ['read-holding', '12', '--signed'])[1] == '12 -1\n'


def test_client_write_read(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    """write-read (function 0x17): write, then read the registers in one request."""
    import json
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    # default: read back the registers written
    status, out, _ = run_client(capsys, base + ['write-read', '10', '1', '0x1234', '-1'])
    assert status == 0
    assert out.splitlines() == ['10 1', '11 4660', '12 65535']
    assert srv.store.app.holding_registers.read(10, 3) == [1, 0x1234, 0xFFFF]
    # another read range, signed values
    assert run_client(capsys, base + ['write-read', '10', '5', '-r', '11', '-n', '3', '--signed'])[1].splitlines() == \
        ['11 4660', '12 -1', '13 0']
    # json: the values read, and the values written
    obj = json.loads(run_client(capsys, base + ['write-read', '20', '0x41', '-f', 'json', '--hex'])[1])
    assert obj['command'] == 'write-read' and obj['address'] == 20 and obj['values'] == ['0x0041']
    assert obj['written'] == {'address': 20, 'values': ['0x0041']}
    # table: the header gives both ranges, the "Written" column the values written
    out = run_client(capsys, base + ['write-read', '20', '1', '2', '-n', '3', '-f', 'table'])[1]
    assert '2 written @ 20..21' in out and '3 holding registers @ 20..22 read' in out
    rows = {line.split()[1]: line.split() for line in out.splitlines() if line[2:].strip()[:1].isdigit()}
    assert rows['20'][:5] == ['0', '20', '0x0014', '0x0001', '0x0001']
    assert rows['22'][:4] == ['2', '22', '0x0016', '0x0000'], 'no written value at 22'
    assert '!' not in out
    # a value read that differs from the value written (changed by the server in the request): marked "!"
    store = srv.store
    assert isinstance(store, MemoryStore)
    store.map(Space.HOLDING_REGISTERS, 30, 1, read=lambda address, count, ctx: [5],
              write=lambda address, values, ctx: None)
    out = run_client(capsys, base + ['write-read', '30', '1', '-f', 'table'])[1]
    assert [line[0] for line in out.splitlines() if '0x001E' in line] == ['!']
    assert '1 register read with another value than the one written' in out
    # errors: modbus exception (status 1), invalid parameters (status 2, with the usage of write-read)
    srv.read_only = True
    status, _, err = run_client(capsys, base + ['write-read', '0', '1'])
    assert status == 1 and 'exception 0x01' in err
    for args, error in ((['0'] + ['1'] * 122, '1 to 121 values'), (['0', '1', '-n', '126'], 'between 1 and 125'),
                        (['0', '70000'], 'value 70000 out of'), (['65535', '1', '2'], 'the write of 2 registers'),
                        (['0', '1', '-r', '65535', '-n', '2'], 'the read of 2 registers')):
        status, _, err = run_client(capsys, base + ['write-read'] + args)
        assert status == 2 and error in err and 'usage: mbtcp-client write-read' in err, args


def test_client_coils(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    assert run_client(capsys, base + ['write-coil', '5', 'on'])[0] == 0
    assert run_client(capsys, base + ['write-coils', '6', '1', 'off', 'true'])[0] == 0
    assert srv.store.app.coils.read(5, 4) == [True, True, False, True]
    assert run_client(capsys, base + ['read-coils', '5', '-n', '4'])[1].split() == \
        ['5', '1', '6', '1', '7', '0', '8', '1']


def test_client_device_id_is_sanitized(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    _, port = server
    status, out, _ = run_client(capsys, ['-H', '127.0.0.1', '-p', str(port), 'device-id'])
    assert status == 0
    # a control char sent by the server (here an ANSI escape) is never printed as is on the terminal
    assert out.splitlines() == ['0x00 ACME', '0x01 X\\x1b[31m', '0x02 ']


def test_client_errors(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    # modbus exception: exit status 1
    srv.read_only = True
    status, _, err = run_client(capsys, base + ['write-register', '0', '1'])
    assert status == 1 and 'exception 0x01' in err
    # invalid parameters: exit status 2
    assert run_client(capsys, base + ['read-holding', '0', '-n', '200'])[0] == 2
    assert run_client(capsys, base + ['write-register', '0', '70000'])[0] == 2
    assert run_client(capsys, base + ['write-coil', '0', 'maybe'])[0] == 2
    # nobody listening: exit status 1
    status, _, err = run_client(capsys, ['-H', '127.0.0.1', '-p', str(free_port()), '-t', '1', 'read-coils', '0'])
    assert status == 1 and 'connect error' in err


def test_client_output_formats(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    import json
    srv, port = server
    srv.store.app.holding_registers.write(10, [1, 0xFFFE, 0x4142])
    base = ['-H', '127.0.0.1', '-p', str(port)]
    # auto: plain when the output is not a terminal (scripts get a stable output)
    assert run_client(capsys, base + ['read-holding', '10', '-n', '3'])[1] == '10 1\n11 65534\n12 16706\n'
    assert run_client(capsys, base + ['read-holding', '10', '-n', '3', '-f', 'plain'])[1] == \
        '10 1\n11 65534\n12 16706\n'
    # csv: a header, then "timestamp,address,value"
    lines = run_client(capsys, base + ['read-holding', '10', '-n', '3', '-f', 'csv', '--signed'])[1].splitlines()
    assert lines[0] == 'timestamp,address,value'
    assert [line.split(',', 1)[1] for line in lines[1:]] == ['10,1', '11,-2', '12,16706']
    # json: one object per read
    obj = json.loads(run_client(capsys, base + ['read-holding', '10', '-n', '3', '-f', 'json', '--hex'])[1])
    assert obj['command'] == 'read-holding' and obj['address'] == 10
    assert obj['values'] == ['0x0001', '0xFFFE', '0x4142']
    srv.store.app.coils.write(0, [True, False])
    obj = json.loads(run_client(capsys, base + ['read-coils', '0', '-n', '2', '-f', 'json'])[1])
    assert obj['values'] == [True, False]
    obj = json.loads(run_client(capsys, base + ['device-id', '-f', 'json'])[1])
    assert obj['objects'][0] == {'id': 0, 'name': 'VendorName', 'value': 'ACME'}
    # the escape char sent by the server is escaped by json
    assert '\x1b' not in run_client(capsys, base + ['device-id', '-f', 'json'])[1]
    # writes: no output out of a terminal (like before)
    assert run_client(capsys, base + ['write-register', '0', '1']) == (0, '', '')


def test_client_table(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    srv, port = server
    srv.store.app.holding_registers.write(10, [0x4142, 0xFFFF])
    base = ['-H', '127.0.0.1', '-p', str(port)]
    status, out, _ = run_client(capsys, base + ['read-holding', '10', '-n', '2', '-f', 'table'])
    assert status == 0
    # no colors out of a terminal
    assert '\x1b' not in out
    assert 'read-holding' in out and '2 holding registers @ 10..11' in out
    rows = {line.split()[1]: line.split() for line in out.splitlines()[4:]}
    assert rows['10'] == ['0', '10', '0x000A', '0x4142', '16706', '16706', '0100', '0001', '0100', '0010', 'AB']
    assert rows['11'][:6] == ['1', '11', '0x000B', '0xFFFF', '65535', '-1']
    # bits: a grid of 10 per row
    srv.store.app.coils.write(100, [True] * 12)
    out = run_client(capsys, base + ['read-coils', '100', '-n', '12', '-f', 'table'])[1]
    assert '12 on, 0 off' in out
    assert [line.split()[:3] for line in out.splitlines() if ' 0x00' in line] == \
        [['0', '100', '0x0064'], ['10', '110', '0x006E']]
    # device identification: names of the standard objects, control chars escaped
    out = run_client(capsys, base + ['device-id', '-f', 'table'])[1]
    assert 'VendorName' in out and 'ProductCode' in out and 'X\\x1b[31m' in out


def test_client_table_highlights_changes() -> None:
    """With -i, the values changed since the previous read are marked (a "*" without colors)."""
    import argparse
    import io

    args = argparse.Namespace(host='localhost', port=502, unit_id=1, command='read-holding', address=0, number=2,
                              interval=1.0, debug=False, format='table', no_color=True, hex=False, signed=False)
    stream = io.StringIO()
    out = cli_tool.Output(args, stream)
    assert not out.live  # not a terminal: the tables are printed one after the other
    out.read(cli_tool.ReadResult(0, [1, 2], time.time(), 0.001))
    out.read(cli_tool.ReadResult(0, [1, 3], time.time(), 0.001))
    second = stream.getvalue().split('read-holding')[2]
    marks = [line[0] for line in second.splitlines() if line[2:].strip()[:1].isdigit() and '0x' in line]
    assert marks == [' ', '*']
    out.read_error('connect error')
    assert out.errors == 1


def test_client_values() -> None:
    assert cli_tool.register_ascii(0x4142) == 'AB'
    assert cli_tool.register_ascii(0x0041) == '.A'
    assert cli_tool.register_binary(0x1234) == '0001 0010 0011 0100'
    assert cli_tool.device_object_name(0x03) == 'VendorUrl'
    assert cli_tool.device_object_name(0x10) == 'reserved'
    assert cli_tool.device_object_name(0x80) == 'private'

# --- mbtcp-server ---


def test_server_command() -> None:
    """mbtcp-server serves requests, refuses writes in read-only mode and stops cleanly on SIGTERM."""
    port = free_port()
    proc = subprocess.Popen([sys.executable, '-m', 'pyModbusTCP.tools.server', '-H', '127.0.0.1', '-p', str(port),
                             '--read-only'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        c = ModbusClient(host='127.0.0.1', port=port, timeout=2.0)
        deadline = time.monotonic() + 10.0
        regs = None
        while regs is None and time.monotonic() < deadline:
            regs = c.read_holding_registers(0, 2)
            time.sleep(0.1)
        assert regs == [0, 0]
        assert c.write_single_register(0, 1) is False
        c.close()
    finally:
        if os.name == 'nt':
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=10)
    if os.name != 'nt':
        assert proc.returncode == 0, out
        assert 'Read-only mode' in out


def test_server_invalid_allow() -> None:
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-A', 'not-an-ip'], check=False,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert 'not an IP address' in proc.stderr


def test_permission_error_detection() -> None:
    try:
        raise ModbusServer.NetworkError('bind') from PermissionError(13, 'Permission denied')
    except ModbusServer.NetworkError as e:
        assert is_permission_error(e)
    assert not is_permission_error(ModbusServer.NetworkError('address already in use'))


# --- mbtcp-tcp2rtu ---

@pytest.mark.parametrize('kwargs, expected', [
    ({}, {'0300000001': None, '0600000001': None, '08000000': None, 'u0': 0x0A, 'u248': 0x0A}),
    ({'read_only': True}, {'0300000001': None, '2b0e0100': None, '0600000001': 0x01, '0f000000010101': 0x01,
                           '2b0d00': 0x01, '08000000': 0x01, 'u0': 0x0A}),
    ({'allow_broadcast': True}, {'u0': None, 'u248': 0x0A}),
])
def test_tcp2rtu_filters(kwargs: dict, expected: dict) -> None:  # type: ignore[type-arg]
    """Requests are relayed (None) or refused with a modbus exception before reaching the serial line.

    The serial line is a loopback (pyserial "loop://"): a relayed request comes back as its own answer.
    """
    serial = pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    port = serial.serial_for_url('loop://', baudrate=19200)
    worker = tcp_to_rtu_gw.ModbusSerialWorker(port, timeout=0.3, **kwargs)
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        for key, exp_code in expected.items():
            unit, pdu = (int(key[1:]), '0600000001') if key.startswith('u') else (1, key)
            c = ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=unit, timeout=2.0)
            resp = c.custom_request(bytes.fromhex(pdu))
            if exp_code is None:
                assert resp == bytes.fromhex(pdu), key
            else:
                assert resp is None and c.last_except == exp_code, key
            c.close()
    finally:
        worker.stop()
        srv.stop()
        thread.join(5.0)


class SilentSerial:
    """A serial port where no slave ever answers (each request holds the line for the whole timeout)."""
    baudrate, bytesize, parity, stopbits, port, is_open, in_waiting = 19200, 8, 'N', 1, 'silent', True, 0

    def __init__(self) -> None:
        self.timeout = 0.1

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int = 1) -> bytes:
        time.sleep(self.timeout)
        return b''


def connect_from(source_ip: str, port: int) -> socket.socket:
    """Open a TCP connection to localhost from another loopback address (skip the test if it's not available)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((source_ip, 0))
    except OSError:
        sock.close()
        pytest.skip(f'loopback address {source_ip} not available on this machine')
    sock.connect(('127.0.0.1', port))
    return sock


def test_tcp2rtu_fair_share(caplog: pytest.LogCaptureFixture) -> None:
    """A host that floods the gateway can't take the serial line: the requests of another host still reach it.

    The flood is refused with exception 0x0A (at most 2 pending requests per host) and logged once (log throttle).
    """
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    worker = tcp_to_rtu_gw.ModbusSerialWorker(SilentSerial(), timeout=0.3)  # type: ignore[arg-type]
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    stop = threading.Event()
    flood_excepts: list[int] = []
    request = struct.pack('>HHHBBHH', 1, 0, 6, 1, 0x03, 0, 1)

    def flood(sock: socket.socket) -> None:
        sock.settimeout(10)
        while not stop.is_set():
            sock.sendall(request)
            resp = sock.recv(64)
            if len(resp) < 9:
                return
            flood_excepts.append(resp[8])

    flood_socks: list[socket.socket] = []
    try:
        flood_socks = [connect_from('127.0.0.2', tcp_port) for _ in range(8)]
        with caplog.at_level('WARNING', logger='mbtcp-tcp2rtu'):
            threads = [threading.Thread(target=flood, args=(sock,), daemon=True) for sock in flood_socks]
            for t in threads:
                t.start()
            time.sleep(0.3)
            # another host (127.0.0.1): its requests reach the serial line (no answer: exception 0x0B, not 0x0A)
            c = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=5.0)
            for _ in range(3):
                assert c.read_holding_registers(0, 1) is None
                assert c.last_except == 0x0B
            c.close()
            stop.set()
            for t in threads:
                t.join(5.0)
        # the flood host got "gateway path unavailable" for its extra requests, logged only once
        assert 0x0A in flood_excepts
        busy_logs = [r for r in caplog.records if 'Too many pending requests' in r.getMessage()]
        assert len(busy_logs) == 1
    finally:
        stop.set()
        for sock in flood_socks:
            sock.close()
        worker.stop()
        srv.stop()
        thread.join(5.0)


def test_tcp2rtu_refused_requests_log_throttle(caplog: pytest.LogCaptureFixture) -> None:
    """Requests refused by the filters are logged once, then once per 10 s at most."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    worker = tcp_to_rtu_gw.ModbusSerialWorker(SilentSerial(), timeout=0.3, read_only=True)  # type: ignore[arg-type]
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        with caplog.at_level('INFO', logger='mbtcp-tcp2rtu'):
            c = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=2.0)
            for _ in range(50):
                assert not c.write_single_register(0, 1)
                assert c.last_except == 0x01
            c.close()
        refused = [r for r in caplog.records if 'Request refused' in r.getMessage()]
        assert len(refused) == 1
    finally:
        srv.stop()


def test_tcp2rtu_invalid_allow(capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    with pytest.raises(SystemExit) as exc:
        tcp_to_rtu_gw.main(['-A', 'not-an-ip', '/dev/null'])
    assert exc.value.code == 2
    assert 'not an IP address' in capsys.readouterr().err


class RecordingSerial(SilentSerial):
    """A silent serial port that records the frames written on the line."""

    def __init__(self) -> None:
        super().__init__()
        self.written: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)


def raw_request(port: int, pdu: bytes, unit_id: int = 1) -> bytes:
    """Send a raw request PDU to a Modbus/TCP server, return the response PDU."""
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(struct.pack('>HHHB', 1, 0, len(pdu) + 1, unit_id) + pdu)
        resp = b''
        while len(resp) < 7 or len(resp) < 6 + struct.unpack('>H', resp[4:6])[0]:
            chunk = sock.recv(260)
            if not chunk:
                break
            resp += chunk
        return resp[7:]


@pytest.mark.parametrize('read_only', [False, True])
def test_tcp2rtu_invalid_function_codes(read_only: bool) -> None:
    """A function code 0 or with the exception flag (0x80 to 0xFF) is never written on the bus: exception 0x01."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    serial_port = RecordingSerial()
    worker = tcp_to_rtu_gw.ModbusSerialWorker(serial_port, timeout=0.1, read_only=read_only)  # type: ignore[arg-type]
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        for pdu in (b'\x00', b'\x81\x00\x00\x00\x01', b'\x83\x00\x00\x00\x01', b'\xff'):
            assert raw_request(tcp_port, pdu) == bytes([pdu[0] | 0x80, 0x01]), pdu.hex()
        assert serial_port.written == []
        # a valid read request still reaches the line (no answer from the slave: exception 0x0B)
        assert raw_request(tcp_port, b'\x03\x00\x00\x00\x01') == b'\x83\x0b'
        assert len(serial_port.written) == 1
    finally:
        worker.stop()
        srv.stop()
        thread.join(5.0)


def test_tcp2rtu_fair_queue_round_robin() -> None:
    """The requests are served in turn by client host, each host in the order of its requests."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    q = tcp_to_rtu_gw.FairQueue(maxsize=8)
    queries = {}
    for name in ('a1', 'a2', 'a3', 'b1', 'c1', 'b2'):
        queries[name] = tcp_to_rtu_gw.RtuQuery(host=name[0])
        q.put(queries[name])
    assert q.qsize() == 6
    order = [q.get(timeout=0) for _ in range(6)]
    assert order == [queries[n] for n in ('a1', 'b1', 'c1', 'a2', 'b2', 'a3')]
    with pytest.raises(queue.Empty):
        q.get_nowait()
    for _ in range(8):
        q.put(tcp_to_rtu_gw.RtuQuery(host='x'))
    with pytest.raises(queue.Full):
        q.put(tcp_to_rtu_gw.RtuQuery(host='y'))


def test_tcp2rtu_fair_share_between_several_flooding_hosts() -> None:
    """Several hosts that flood the gateway can't take every turn of the serial line: the requests of another host
    still reach it (the slave doesn't answer: exception 0x0B, never 0x0A "gateway path unavailable")."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    worker = tcp_to_rtu_gw.ModbusSerialWorker(SilentSerial(), timeout=0.2)  # type: ignore[arg-type]
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    stop = threading.Event()
    request = struct.pack('>HHHBBHH', 1, 0, 6, 1, 0x03, 0, 1)

    def flood(sock: socket.socket) -> None:
        sock.settimeout(10)
        try:
            while not stop.is_set():
                sock.sendall(request)
                if len(sock.recv(64)) < 9:
                    return
        except OSError:
            return

    flood_socks: list[socket.socket] = []
    threads: list[threading.Thread] = []
    try:
        # 4 flooding hosts with 4 connections each: they could fill a plain FIFO of the size of the queue
        flood_socks = [connect_from(f'127.0.0.{i}', tcp_port) for i in (2, 3, 4, 5) for _ in range(4)]
        threads = [threading.Thread(target=flood, args=(sock,), daemon=True) for sock in flood_socks]
        for t in threads:
            t.start()
        time.sleep(0.5)
        # 1 turn of each flooding host (0.2 s each) before the request of 127.0.0.1, at most
        c = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=5.0)
        for _ in range(3):
            assert c.read_holding_registers(0, 1) is None
            assert c.last_except == 0x0B
        c.close()
    finally:
        stop.set()
        for t in threads:
            t.join(10.0)
        for sock in flood_socks:
            sock.close()
        worker.stop()
        srv.stop()
        thread.join(5.0)


def test_tcp2rtu_cancelled_request_is_not_sent() -> None:
    """A request that nobody waits for anymore (session timeout) never holds the serial line."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    serial_port = RecordingSerial()
    worker = tcp_to_rtu_gw.ModbusSerialWorker(serial_port, timeout=0.1)  # type: ignore[arg-type]
    query = tcp_to_rtu_gw.RtuQuery(host='h')
    query.request.build(raw_pdu=b'\x03\x00\x00\x00\x01', slave_ad=1)
    query.cancelled = True
    worker.rtu_queries_q.put(query)
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    try:
        assert query.completed.wait(5.0)
        assert serial_port.written == []
    finally:
        worker.stop()
        thread.join(5.0)


@pytest.mark.parametrize('tool, args, error', [
    ('tcp_to_rtu_gw', ['-b', '0'], '-b/--baudrate'),
    ('tcp_to_rtu_gw', ['-t', '-1'], '-t/--timeout'),
    ('tcp_to_rtu_gw', ['-t', 'nan'], '-t/--timeout'),
    ('tcp_to_rtu_gw', ['-e', '0'], '-e/--eof'),
    ('rtu_to_tcp_gw', ['-b', '-9600'], '-b/--baudrate'),
    ('rtu_to_tcp_gw', ['-a', '0'], '-a/--address'),
    ('rtu_to_tcp_gw', ['-a', '248'], '-a/--address'),
    ('rtu_to_tcp_gw', ['-e', 'inf'], '-e/--eof'),
    ('rtu_to_tcp_gw', ['-t', '0'], '-t/--timeout'),
    ('tcp_to_rtu_gw', ['--rs485-rts-tx', 'low'], '--rs485-rts-tx needs --rs485'),
    ('tcp_to_rtu_gw', ['--rs485', '--rs485-rts-rx', 'medium'], '--rs485-rts-rx'),
    ('tcp_to_rtu_gw', ['--rs485', '--rs485-delay-before-tx', '0.5'], '--rs485-delay-before-tx'),
    ('rtu_to_tcp_gw', ['--rs485-delay-before-rx', '0.001'], '--rs485-delay-before-rx needs --rs485'),
    ('rtu_to_tcp_gw', ['--rs485', '--rs485-delay-before-rx', '-1'], '--rs485-delay-before-rx'),
])
def test_gateway_invalid_args(tool: str, args: list[str], error: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Invalid serial settings are refused at startup (they made the gateway crash, or fail at each request)."""
    pytest.importorskip('serial')
    import importlib
    module = importlib.import_module('pyModbusTCP.tools.' + tool)
    with pytest.raises(SystemExit) as exc:
        module.main(args + ['/dev/null'])
    assert exc.value.code == 2
    assert error in capsys.readouterr().err


def test_gateway_api_checks() -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_to_tcp_gw, tcp_to_rtu_gw
    for t35 in (rtu_to_tcp_gw.t35, tcp_to_rtu_gw.t35):
        with pytest.raises(ValueError):
            t35(0, 8, 'N', 1)
    for address in (0, 248, 255):
        with pytest.raises(ValueError):
            rtu_to_tcp_gw.SerialModbusWorker(SilentSerial(), ModbusClient(), slave_addr=address)  # type: ignore


def test_gateway_rs485_settings() -> None:
    """The --rs485-* options give the RS485Settings of pyserial; a level not given is the one of the driver, if any."""
    pytest.importorskip('serial')
    import argparse

    from pyModbusTCP.tools import _common

    parser = argparse.ArgumentParser()
    _common.add_rs485_args(parser)
    assert _common.rs485_settings(parser.parse_args([])) is None
    # no RS-485 mode in the driver: RTS high to send, low to receive (pyserial and Windows)
    setup = _common.rs485_settings(parser.parse_args(['--rs485']))
    assert setup is not None
    st = setup.settings
    assert (st.rts_level_for_tx, st.rts_level_for_rx) == (True, False)
    assert (st.delay_before_tx, st.delay_before_rx) == (None, None)
    assert not st.loopback  # no echo of the frames sent
    assert setup.text() == 'RTS high on tx, low on rx, delay before tx driver, before rx driver'
    # RS-485 mode already set in the driver (device tree): its levels are kept
    setup = _common.rs485_settings(parser.parse_args(['--rs485']), driver_levels=(False, True))
    assert setup is not None
    assert (setup.settings.rts_level_for_tx, setup.settings.rts_level_for_rx) == (False, True)
    assert setup.text().startswith('RTS low (driver) on tx, high (driver) on rx')
    # the command line wins over the driver, level by level
    setup = _common.rs485_settings(parser.parse_args(['--rs485', '--rs485-rts-tx', 'high']),
                                   driver_levels=(False, True))
    assert setup is not None
    assert (setup.settings.rts_level_for_tx, setup.settings.rts_level_for_rx) == (True, True)
    assert setup.text().startswith('RTS high on tx, high (driver) on rx')
    setup = _common.rs485_settings(parser.parse_args(
        ['--rs485', '--rs485-rts-tx', 'low', '--rs485-rts-rx', 'high',
         '--rs485-delay-before-tx', '0.002', '--rs485-delay-before-rx', '0']))
    assert setup is not None
    st = setup.settings
    assert (st.rts_level_for_tx, st.rts_level_for_rx) == (False, True)
    assert (st.delay_before_tx, st.delay_before_rx) == (0.002, 0.0)
    assert setup.text() == 'RTS low on tx, high on rx, delay before tx 0.002 s, before rx 0.000 s'


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='RS-485 ioctl of Linux')
def test_gateway_driver_rts_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RTS levels of a RS-485 mode already set in the driver are read with the TIOCGRS485 ioctl."""
    pytest.importorskip('serial')
    import fcntl

    from serial import serialposix

    from pyModbusTCP.tools import _common

    class Port:
        def fileno(self) -> int:
            return 42

    def fake_ioctl(flags: int) -> object:
        def ioctl(fd: int, request: int, buf: list[int]) -> int:
            assert (fd, request) == (42, serialposix.TIOCGRS485)
            buf[0] = flags
            return 0
        return ioctl

    on, tx, rx = (serialposix.SER_RS485_ENABLED, serialposix.SER_RS485_RTS_ON_SEND,
                  serialposix.SER_RS485_RTS_AFTER_SEND)
    for flags, levels in ((on | tx, (True, False)), (on | rx, (False, True)), (on, (False, False)),
                          (tx | rx, None)):  # mode disabled: its flags are meaningless
        monkeypatch.setattr(fcntl, 'ioctl', fake_ioctl(flags))
        assert _common.driver_rts_levels(Port()) == levels  # type: ignore[arg-type]

    def no_rs485(fd: int, request: int, buf: list[int]) -> int:
        raise OSError(25, 'Inappropriate ioctl for device')

    monkeypatch.setattr(fcntl, 'ioctl', no_rs485)
    assert _common.driver_rts_levels(Port()) is None  # type: ignore[arg-type]


class RS485Port:
    """A serial port that accepts its RS-485 mode, or refuses it like pyserial (ValueError or NotImplementedError)."""

    port = '/dev/ttyFAKE'

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.mode: object = None

    @property
    def rs485_mode(self) -> object:
        return self.mode

    @rs485_mode.setter
    def rs485_mode(self, settings: object) -> None:
        if self.error is not None:
            raise self.error
        self.mode = settings


def test_gateway_apply_rs485() -> None:
    pytest.importorskip('serial')
    from serial.rs485 import RS485Settings

    from pyModbusTCP.tools import _common

    settings = RS485Settings(rts_level_for_tx=False, rts_level_for_rx=True)
    port = RS485Port()
    _common.apply_rs485(port, settings)  # type: ignore[arg-type]
    assert port.rs485_mode is settings
    # refused by the driver (Linux, Windows) or not supported (macOS): a ValueError, the gateways exit with code 2
    with pytest.raises(ValueError, match='refused by the serial driver of /dev/ttyFAKE'):
        _common.apply_rs485(RS485Port(ValueError('Failed to set RS485 mode')), settings)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='not supported on this platform'):
        _common.apply_rs485(RS485Port(NotImplementedError('RS485')), settings)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
@pytest.mark.parametrize('tool', ['tcp_to_rtu_gw', 'rtu_to_tcp_gw'])
def test_gateway_rs485_refused(tool: str, caplog: pytest.LogCaptureFixture) -> None:
    """A pseudo-terminal has no RS-485 mode: the gateway stops at startup with the exit code 2."""
    pytest.importorskip('serial')
    import importlib
    module = importlib.import_module('pyModbusTCP.tools.' + tool)
    master_fd, slave_fd = os.openpty()
    try:
        with caplog.at_level(logging.CRITICAL):
            assert module.main(['--rs485', '-p', str(free_port()), os.ttyname(slave_fd)]) == 2
        assert 'RS-485 mode' in caplog.text
    finally:
        os.close(master_fd)
        os.close(slave_fd)


# --- mbtcp-rtu2tcp ---

def rtu_frame(slave: int, pdu: bytes) -> bytes:
    frame = bytes([slave]) + pdu
    return frame + struct.pack('<H', crc16(frame))


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
def test_rtu2tcp_relay(server: tuple[ModbusServer, int]) -> None:
    """A RTU master on a pseudo-terminal reads and writes the TCP server through the gateway (slave 30)."""
    serial = pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_to_tcp_gw
    srv, tcp_port = server
    srv.store.app.holding_registers.write(0, [0x1234])
    master_fd, slave_fd = os.openpty()
    port = serial.Serial(os.ttyname(slave_fd), baudrate=19200)
    tcp_cli = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=2.0)
    worker = rtu_to_tcp_gw.SerialModbusWorker(port=port, mbus_cli=tcp_cli, slave_addr=30)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()

    def exchange(frame: bytes, expect_answer: bool = True) -> bytes:
        os.write(master_fd, frame)
        answer = b''
        deadline = time.monotonic() + (3.0 if expect_answer else 0.5)
        while time.monotonic() < deadline:
            os.set_blocking(master_fd, False)
            try:
                answer += os.read(master_fd, 256)
            except BlockingIOError:
                pass
            if answer and len(answer) >= 5 and crc16(answer) == 0:
                break
            time.sleep(0.02)
        return answer

    try:
        time.sleep(0.3)  # let the worker flush its input buffer
        # read 1 holding register
        assert exchange(rtu_frame(30, bytes.fromhex('0300000001'))) == rtu_frame(30, bytes.fromhex('03021234'))
        # write 1 holding register
        assert exchange(rtu_frame(30, bytes.fromhex('0600010005'))) == rtu_frame(30, bytes.fromhex('0600010005'))
        assert srv.store.app.holding_registers.read(1) == [5]
        # frame for another slave: ignored
        assert exchange(rtu_frame(31, bytes.fromhex('0300000001')), expect_answer=False) == b''
        # broadcast: ignored without --allow-broadcast
        assert exchange(rtu_frame(0, bytes.fromhex('0600020007')), expect_answer=False) == b''
        assert srv.store.app.holding_registers.read(2) == [0]
    finally:
        worker.stop()
        thread.join(5.0)
        port.close()
        tcp_cli.close()
        os.close(master_fd)
        os.close(slave_fd)


# --- common ---

@pytest.mark.parametrize('command', ['server', 'client', 'tcp2rtu', 'rtu2tcp'])
def test_help_and_version(command: str) -> None:
    if command in ('tcp2rtu', 'rtu2tcp'):
        pytest.importorskip('serial')
    for option in ('--help', '--version', '-v'):
        # through the dispatcher (python -m pyModbusTCP.tools) and the module itself
        for cmd in (['-m', 'pyModbusTCP.tools', command], ['-m', 'pyModbusTCP.tools.' + _entry.COMMANDS[command]]):
            proc = subprocess.run([sys.executable] + cmd + [option], check=False,
                                  capture_output=True, text=True, timeout=30)
            assert proc.returncode == 0, proc.stderr
            assert proc.stdout
            if option != '--help':
                assert proc.stdout.strip().endswith('(pyModbusTCP) ' + VERSION)


def test_dispatcher_usage() -> None:
    for args, status in (([], 2), (['--help'], 0), (['unknown'], 2)):
        proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools'] + args, check=False,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == status, args
        assert 'usage: python -m pyModbusTCP.tools' in proc.stdout + proc.stderr
    for option in ('-v', '--version'):
        proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools', option], check=False,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        assert proc.stdout == f'python -m pyModbusTCP.tools (pyModbusTCP) {VERSION}\n'


def test_argparse_types() -> None:
    import argparse

    from pyModbusTCP.tools import _common
    assert _common.positive_float('0.5') == 0.5
    assert _common.positive_float('86400') == 86400.0
    for bad in ('0', '-1', 'nan', 'inf', '-inf', '1e300', '86401', 'abc'):
        with pytest.raises(argparse.ArgumentTypeError):
            _common.positive_float(bad)
    assert _common.positive_int('9600') == 9600
    for bad in ('0', '-1', '1.5', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            _common.positive_int(bad)
    assert [_common.slave_address(v) for v in ('1', '247', '0x1e')] == [1, 247, 30]
    for bad in ('0', '248', '-1', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            _common.slave_address(bad)


@pytest.mark.skipif(os.name == 'nt', reason='SIGTERM is not a catchable signal on Windows')
def test_stop_on_sigterm() -> None:
    """SIGTERM calls the stop function in the block, the previous handler is restored at the end."""
    import logging

    from pyModbusTCP.tools._common import stop_on_sigterm
    previous = signal.getsignal(signal.SIGTERM)
    stopped = threading.Event()
    with stop_on_sigterm(stopped.set, logging.getLogger('test')):
        os.kill(os.getpid(), signal.SIGTERM)
        assert stopped.wait(5.0)
    assert signal.getsignal(signal.SIGTERM) == previous
    # from a thread other than the main one: nothing is changed (no ValueError)
    result: list[object] = []

    def in_thread() -> None:
        with stop_on_sigterm(stopped.set, logging.getLogger('test')):
            result.append(signal.getsignal(signal.SIGTERM))

    t = threading.Thread(target=in_thread)
    t.start()
    t.join(5.0)
    assert result == [previous]


@pytest.mark.parametrize('extra_args, status', [([], 130), (['-i', '1'], 0)])
def test_client_ctrl_c_status(extra_args: list[str], status: int, capsys: pytest.CaptureFixture[str],
                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C ends a repeated read normally (0), but a single interrupted request is a failure (130)."""
    def interrupted(*_args: object) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_tool, 'run_command', interrupted)
    assert run_client(capsys, ['read-holding', '0'] + extra_args)[0] == status


@pytest.mark.parametrize('command', ['tcp2rtu', 'rtu2tcp'])
def test_gateway_without_pyserial(command: str) -> None:
    """Without pyserial, a gateway exits with status 2 and the way to install it (the other tools still work)."""
    code = ('import sys; sys.modules["serial"] = None; '  # "import serial" now fails like a missing pyserial
            'from pyModbusTCP.tools import _entry; '
            'import pyModbusTCP.tools.client, pyModbusTCP.tools.server; '  # the other tools still work
            f'sys.exit(_entry.main([{command!r}, "--help"]))')
    proc = subprocess.run([sys.executable, '-c', code], check=False, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, proc.stderr
    assert 'pip install "pyModbusTCP[serial]"' in proc.stderr
