"""Tests of the command line tools (pyModbusTCP.tools)."""

import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Iterator, List, Tuple

import pytest

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import DeviceIdentification, ModbusServer
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
def server() -> Iterator[Tuple[ModbusServer, int]]:
    """A Modbus/TCP server on localhost (port chosen by the OS), with a device identification."""
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True,
                       device_id=DeviceIdentification(vendor_name=b'ACME', product_code=b'X\x1b[31m'))
    _, port = srv.start()
    yield srv, port
    srv.stop()


def run_client(capsys: pytest.CaptureFixture[str], args: List[str]) -> Tuple[int, str, str]:
    """Run mbtcp-client, return (exit status, stdout, stderr)."""
    try:
        status = cli_tool.main(args)
    except SystemExit as e:  # argparse errors
        status = int(e.code or 0)
    out, err = capsys.readouterr()
    return status, out, err


# --- mbtcp-client ---

def test_client_read_write_registers(server: Tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    _, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    assert run_client(capsys, base + ['write-registers', '10', '1', '0x1234', '-1'])[0] == 0
    status, out, _ = run_client(capsys, base + ['read-holding', '10', '-n', '3'])
    assert status == 0
    assert out.splitlines() == ['10 1', '11 4660', '12 65535']
    assert run_client(capsys, base + ['read-holding', '10', '-n', '3', '--hex'])[1].split() == \
        ['10', '0x0001', '11', '0x1234', '12', '0xFFFF']
    assert run_client(capsys, base + ['read-holding', '12', '--signed'])[1] == '12 -1\n'


def test_client_coils(server: Tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    assert run_client(capsys, base + ['write-coil', '5', 'on'])[0] == 0
    assert run_client(capsys, base + ['write-coils', '6', '1', 'off', 'true'])[0] == 0
    assert srv.data_bank.get_coils(5, 4) == [True, True, False, True]
    assert run_client(capsys, base + ['read-coils', '5', '-n', '4'])[1].split() == \
        ['5', '1', '6', '1', '7', '0', '8', '1']


def test_client_device_id_is_sanitized(server: Tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    _, port = server
    status, out, _ = run_client(capsys, ['-H', '127.0.0.1', '-p', str(port), 'device-id'])
    assert status == 0
    # a control char sent by the server (here an ANSI escape) is never printed as is on the terminal
    assert out.splitlines() == ['0x00 ACME', '0x01 X\\x1b[31m', '0x02 ']


def test_client_errors(server: Tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
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
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-A', 'not-an-ip'],
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
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, ext_engine=worker.srv_engine_entry)
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


def test_tcp2rtu_invalid_allow(capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    with pytest.raises(SystemExit) as exc:
        tcp_to_rtu_gw.main(['-A', 'not-an-ip', '/dev/null'])
    assert exc.value.code == 2
    assert 'not an IP address' in capsys.readouterr().err


# --- mbtcp-rtu2tcp ---

def rtu_frame(slave: int, pdu: bytes) -> bytes:
    frame = bytes([slave]) + pdu
    return frame + struct.pack('<H', crc16(frame))


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
def test_rtu2tcp_relay(server: Tuple[ModbusServer, int]) -> None:
    """A RTU master on a pseudo-terminal reads and writes the TCP server through the gateway (slave 30)."""
    serial = pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_to_tcp_gw
    srv, tcp_port = server
    srv.data_bank.set_holding_registers(0, [0x1234])
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
        assert srv.data_bank.get_holding_registers(1) == [5]
        # frame for another slave: ignored
        assert exchange(rtu_frame(31, bytes.fromhex('0300000001')), expect_answer=False) == b''
        # broadcast: ignored without --allow-broadcast
        assert exchange(rtu_frame(0, bytes.fromhex('0600020007')), expect_answer=False) == b''
        assert srv.data_bank.get_holding_registers(2) == [0]
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
    for option in ('--help', '--version'):
        # through the dispatcher (python -m pyModbusTCP.tools) and the module itself
        for cmd in (['-m', 'pyModbusTCP.tools', command], ['-m', 'pyModbusTCP.tools.' + _entry.COMMANDS[command]]):
            proc = subprocess.run([sys.executable] + cmd + [option], capture_output=True, text=True, timeout=30)
            assert proc.returncode == 0, proc.stderr
            assert proc.stdout


def test_dispatcher_usage() -> None:
    for args, status in (([], 2), (['--help'], 0), (['unknown'], 2)):
        proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools'] + args,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == status, args
        assert 'usage: python -m pyModbusTCP.tools' in proc.stdout + proc.stderr


@pytest.mark.parametrize('command', ['tcp2rtu', 'rtu2tcp'])
def test_gateway_without_pyserial(command: str) -> None:
    """Without pyserial, a gateway exits with status 2 and the way to install it (the other tools still work)."""
    code = ('import sys; sys.modules["serial"] = None; '  # "import serial" now fails like a missing pyserial
            'from pyModbusTCP.tools import _entry; '
            'import pyModbusTCP.tools.client, pyModbusTCP.tools.server; '  # the other tools still work
            'sys.exit(_entry.main([%r, "--help"]))' % command)
    proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, proc.stderr
    assert 'pip install "pyModbusTCP[serial]"' in proc.stderr
