"""Tests of the command line tools (pyModbusTCP.tools)."""

import argparse
import contextlib
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
from collections.abc import Generator, Iterator
from typing import Any

import pytest

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.constants import VERSION
from pyModbusTCP.server import DeviceIdentification, MemoryStore, ModbusServer, Space
from pyModbusTCP.tools import _entry
from pyModbusTCP.tools import client as cli_tool
from pyModbusTCP.tools._common import FunctionFilter, is_permission_error
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
    for args, error in ((['0'] + ['1'] * 122, '1 to 121 registers'), (['0', '1', '-n', '126'], 'between 1 and 125'),
                        (['0', '70000'], 'value 70000 out of'), (['65535', '1', '2'], 'the write of 2 registers'),
                        (['0', '1', '-r', '65535', '-n', '2'], 'the read of 2 registers')):
        status, _, err = run_client(capsys, base + ['write-read'] + args)
        assert status == 2 and error in err and 'usage: mbtcp-client write-read' in err, args


def test_client_typed_values(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    """-T/--type and -B/--byte-order: values on several registers."""
    import json
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    regs = srv.store.app.holding_registers
    assert run_client(capsys, base + ['write-registers', '10', '3.14', '-2.5', '-T', 'float32', '-B', 'CDAB'])[0] == 0
    assert regs.read(10, 4) == [0xF5C3, 0x4048, 0x0000, 0xC020]
    # -n counts the values; plain: the address of the first register of each value
    cmd = ['read-holding', '10', '-n', '2', '-T', 'float32', '-B', 'CDAB']
    assert run_client(capsys, base + cmd)[1] == '10 3.14\n12 -2.5\n'
    assert run_client(capsys, base + cmd + ['--hex'])[1] == '10 0x4048F5C3\n12 0xC0200000\n'
    lines = run_client(capsys, base + cmd + ['-f', 'csv'])[1].splitlines()
    assert [line.split(',')[1:] for line in lines[1:]] == [['10', '3.14'], ['12', '-2.5']]
    obj = json.loads(run_client(capsys, base + cmd + ['-f', 'json'])[1])
    assert (obj['type'], obj['byte_order'], obj['values']) == ('float32', 'CDAB', [3.14, -2.5])
    # table: the value, its bits (ABCD) and the registers as read
    out = run_client(capsys, base + cmd + ['-f', 'table'])[1]
    assert '2 float32 CDAB @ 10..13' in out
    assert ['0', '10', '0x000A', '3.14', '0x4048F5C3', 'F5C3', '4048'] in [line.split() for line in out.splitlines()]
    # -B alone: uint16, byte swapped
    assert run_client(capsys, base + ['read-holding', '11', '-B', 'BADC', '--hex'])[1] == '11 0x4840\n'
    # 64 bits, a negative value after "--" (it looks like an option)
    assert run_client(capsys, base + ['write-registers', '20', '-T', 'float64', '--', '-1e-3'])[0] == 0
    assert run_client(capsys, base + ['read-input-regs', '20', '-T', 'float64'])[0] == 0  # input registers: empty
    assert run_client(capsys, base + ['read-holding', '20', '-T', 'float64'])[1] == '20 -0.001\n'
    # write-read: the values written and read back, NaN in json
    obj = json.loads(run_client(capsys, base + ['write-read', '30', 'nan', '1', '-T', 'float32', '-n', '3',
                                                '-f', 'json'])[1])
    assert obj['address'] == 30 and obj['values'] == ['nan', 1.0, 0.0]
    assert obj['written'] == {'address': 30, 'values': ['nan', 1.0]}
    out = run_client(capsys, base + ['write-read', '30', '7', '-T', 'int32', '-B', 'DCBA', '-f', 'table'])[1]
    assert '1 written @ 30..31' in out and '1 int32 DCBA @ 30..31 read' in out
    assert regs.read(30, 2) == [0x0700, 0x0000]  # 00 00 00 07 in the DCBA order
    # invalid parameters: exit status 2, with the usage of the command
    for args, error in ((['read-holding', '0', '-n', '63', '-T', 'float32'], 'between 1 and 62'),
                        (['read-holding', '0', '-T', 'int32', '--signed'], '--signed cannot be used'),
                        (['write-registers', '0', '1.5', '-T', 'int32'], "'1.5' is not an integer"),
                        (['write-registers', '0', '1e39', '-T', 'float32'], 'out of the float32 range'),
                        (['write-read', '0'] + ['1'] * 31 + ['-T', 'float64'], '124 registers (31 values)'),
                        (['write-read', '0', '1', '-T', 'float64', '-n', '32'], 'between 1 and 31')):
        status, _, err = run_client(capsys, base + args)
        assert status == 2 and error in err and f'usage: mbtcp-client {args[0]}' in err, args


def test_client_former_byte_order_option(capsys: pytest.CaptureFixture[str]) -> None:
    """-b is renamed -B/--byte-order (-b is the baud rate of the serial tools): a clear error, not a silent default."""
    for args in (['read-holding', '0', '-b', 'CDAB'], ['write-registers', '0', '1', '-b']):
        with pytest.raises(SystemExit) as exc:
            cli_tool.main(['-p', '1', *args])
        assert exc.value.code == 2
        assert '-b is renamed -B/--byte-order' in capsys.readouterr().err


def test_client_coils(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    srv, port = server
    base = ['-H', '127.0.0.1', '-p', str(port)]
    assert run_client(capsys, base + ['write-coil', '5', 'on'])[0] == 0
    assert run_client(capsys, base + ['write-coils', '6', '1', 'off', 'true'])[0] == 0
    assert srv.store.app.coils.read(5, 4) == [True, True, False, True]
    assert run_client(capsys, base + ['read-coils', '5', '-n', '4'])[1].split() == \
        ['5', '1', '6', '1', '7', '0', '8', '1']
    # yes/no too, any case; the help and the error list every accepted word
    assert run_client(capsys, base + ['write-coils', '5', 'NO', 'Yes'])[0] == 0
    assert srv.store.app.coils.read(5, 2) == [False, True]
    status, _, err = run_client(capsys, base + ['write-coil', '0', 'maybe'])
    assert status == 2 and '1/0, on/off, true/false or yes/no' in err


def test_client_device_id_is_sanitized(server: tuple[ModbusServer, int], capsys: pytest.CaptureFixture[str]) -> None:
    _, port = server
    status, out, _ = run_client(capsys, ['-H', '127.0.0.1', '-p', str(port), 'device-id'])
    assert status == 0
    # a control char sent by the server (here an ANSI escape) is never printed as is on the terminal
    assert out.splitlines() == ['0x00 ACME', '0x01 X\\x1b[31m', '0x02 ']


@pytest.mark.parametrize('text, cell', [
    ('=HYPERLINK("http://x","y")', '\'=HYPERLINK("http://x","y")'), ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
    ('-2+3', "'-2+3"), ('@SUM(A1)', "'@SUM(A1)"), ('\tx', "'\tx"), ('ACME', 'ACME'), ('A=1', 'A=1'), ('', ''),
])
def test_csv_text(text: str, cell: str) -> None:
    assert cli_tool.csv_text(text) == cell


def test_client_device_id_csv_no_formula(capsys: pytest.CaptureFixture[str]) -> None:
    """A device-id text is never a formula in the csv output (CWE-1236)."""
    import csv
    import io
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True,
                       device_id=DeviceIdentification(vendor_name=b'=HYPERLINK("http://evil","x")',
                                                      product_code=b'@SUM(1)', major_minor_revision=b'1.0'))
    _, port = srv.start()
    try:
        status, out, _ = run_client(capsys, ['-H', '127.0.0.1', '-p', str(port), 'device-id', '-f', 'csv'])
    finally:
        srv.stop()
    assert status == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ['id', 'name', 'value']
    assert [row[2] for row in rows[1:]] == ['\'=HYPERLINK("http://evil","x")', "'@SUM(1)", '1.0']


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


def test_server_log_changes(caplog: pytest.LogCaptureFixture) -> None:
    """--log-changes: one line per changed value of a small write, one line for a write of many values (1968 coils
    is one line, never 1968: the log never grows faster than the requests)."""
    from pyModbusTCP.server import ChangeEvent, RequestContext, WrittenRange
    from pyModbusTCP.server.session import ClientInfo
    from pyModbusTCP.tools import server as server_tool
    ctx = RequestContext(client=ClientInfo('10.0.0.5', 50000), unit_id=1, function_code=0x0F)
    many = WrittenRange(Space.COILS, 100, (False,) * 1968, (True,) * 1000 + (False,) * 968)
    few = WrittenRange(Space.HOLDING_REGISTERS, 3, (0, 0, 7), (1, 1, 7))
    inputs = WrittenRange(Space.INPUT_REGISTERS, 0, (0,), (1,))
    with caplog.at_level(logging.INFO, logger='mbtcp-server'):
        server_tool.log_changes(ChangeEvent(ctx, writes=(many, few, inputs)))
    lines = [r.getMessage() for r in caplog.records if r.name == 'mbtcp-server']
    assert lines == ['Coils @100..2067: 1968 written, 1000 changed (from 10.0.0.5)',
                     'Holding register @3: 0 -> 1 (from 10.0.0.5)', 'Holding register @4: 0 -> 1 (from 10.0.0.5)']


def test_server_serve_sigterm_handler() -> None:
    """serve() sets its SIGTERM handler like the gateways (stop_on_sigterm): restored on return, and none outside
    the main thread (signal.signal() raised ValueError there, the server never started)."""
    from pyModbusTCP.tools import server as server_tool
    args = server_tool.build_parser().parse_args([])
    previous = signal.getsignal(signal.SIGTERM)

    def stop_when_running(srv: ModbusServer) -> None:
        deadline = time.monotonic() + 5.0
        while not srv.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
        srv.stop()

    def run(srv: ModbusServer) -> int:
        threading.Thread(target=stop_when_running, args=(srv,), daemon=True).start()
        return server_tool.serve(srv, args)

    # in the main thread: the handler of serve() while it runs, the previous one after
    assert run(ModbusServer('127.0.0.1', 0)) == 0
    assert signal.getsignal(signal.SIGTERM) == previous
    # in another thread: no handler set, no error
    status: list[int] = []
    thread = threading.Thread(target=lambda: status.append(run(ModbusServer('127.0.0.1', 0))))
    thread.start()
    thread.join(10.0)
    assert status == [0]
    assert signal.getsignal(signal.SIGTERM) == previous


@pytest.mark.parametrize('args, error', [
    (['--max-connections', '0'], '--max-connections'),
    (['--max-connections', '-1'], '--max-connections'),
    (['--max-connections-per-host', '1.5'], '--max-connections-per-host'),
])
def test_server_invalid_args(args: list[str], error: str, capsys: pytest.CaptureFixture[str]) -> None:
    """mbtcp-server refuses an invalid limit at the parse of its arguments, like the other tools."""
    from pyModbusTCP.tools import server as server_tool
    with pytest.raises(SystemExit) as exc:
        server_tool.main(args)
    assert exc.value.code == 2
    assert error in capsys.readouterr().err


def test_client_unit_id_and_code(capsys: pytest.CaptureFixture[str]) -> None:
    """mbtcp-client: -u is a unit id (0 to 255), -c a read code in any notation, like the other numbers."""
    for args in (['-u', '256', 'read-coils', '0'], ['-u', '-1', 'read-coils', '0'], ['device-id', '-c', '5']):
        assert run_client(capsys, args)[0] == 2, args
    parsed = cli_tool.build_parser().parse_args(['-u', '0x10', 'device-id', '-c', '0x2'])
    assert (parsed.unit_id, parsed.code) == (16, 2)


def test_server_link_local_allow() -> None:
    """-A fe80::/10 is refused (an allow list can't tell the interface of a client), with the way to do it."""
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-A', 'fe80::/10'], check=False,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert 'link-local address' in proc.stderr


def test_server_invalid_allow() -> None:
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-A', 'not-an-ip'], check=False,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert 'not an IP address' in proc.stderr


def test_server_empty_host() -> None:
    """-H '' would listen on every interface: refused (S-07)."""
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-H', ''], check=False,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert 'host must not be empty' in proc.stderr


def test_server_legacy_ipv4_host() -> None:
    """-H 0 would listen on every interface (0 is 0.0.0.0 for bind()): refused (S-08)."""
    proc = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools.server', '-H', '0'], check=False,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert 'standard form' in proc.stderr


def test_permission_error_detection() -> None:
    try:
        raise ModbusServer.NetworkError('bind') from PermissionError(13, 'Permission denied')
    except ModbusServer.NetworkError as e:
        assert is_permission_error(e)
    assert not is_permission_error(ModbusServer.NetworkError('address already in use'))


# --- mbtcp-tcp2rtu ---

@pytest.mark.parametrize('kwargs, expected', [
    # default: the standard data functions only (S-03), not the diagnostics, the file records, other MEI types...
    ({}, {'0300000001': 0x0B, '0600000001': None, '16000a00ff0000': None, '2b0e0100': 0x0B, '08000000': 0x01,
          '15': 0x01, '2b0d00': 0x01, '41': 0x01, 'u0': 0x0A, 'u248': 0x0A}),
    ({'read_only': True}, {'0300000001': 0x0B, '2b0e0100': 0x0B, '0600000001': 0x01, '0f000000010101': 0x01,
                           '2b0d00': 0x01, '08000000': 0x01, 'u0': 0x0A}),
    ({'allow_broadcast': True}, {'u0': None, 'u248': 0x0A}),
    ('all', {'08000000': None, '4100': None, '2b0d00': None, '0600000001': None}),
    ('3,0x08,0x2B/0x0D', {'0300000001': 0x0B, '08000000': None, '2b0d00': None, '0600000001': 0x01,
                          '2b0e0100': 0x01}),
])
def test_tcp2rtu_filters(kwargs: dict | str, expected: dict) -> None:  # type: ignore[type-arg]
    """Requests are relayed or refused with a modbus exception (0x01, 0x0A) before reaching the serial line.

    kwargs: the arguments of TcpToRtuWorker, or the text of a --functions filter.

    The serial line is a loopback (pyserial "loop://"): a relayed request comes back as its own answer. This echo is
    the valid response of 0x06 and of an unknown function like 0x08 (None: the echo is returned), not of a read
    (0x03, 0x2B/0x0E): the gateway waits for a valid one until the timeout, exception 0x0B (relayed, then refused).
    """
    serial = pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    port = serial.serial_for_url('loop://', baudrate=19200)
    if isinstance(kwargs, str):
        kwargs = {'functions': FunctionFilter.parse(kwargs)}
    worker = tcp_to_rtu_gw.TcpToRtuWorker(port, timeout=0.3, **kwargs)
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


class TimedSerial(SilentSerial):
    """A serial port where no slave answers, that records the frames written and their time.

    The time is time.perf_counter() (precise on every OS: time.monotonic() has a 15.6 ms step on Windows before
    Python 3.13).
    """

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[float, bytes]] = []

    def write(self, data: bytes) -> int:
        self.writes.append((time.perf_counter(), bytes(data)))
        return len(data)


def wait_writes(port: TimedSerial, count: int, timeout: float = 10.0) -> None:
    """Wait until count frames are written on port (no fixed delay: a loaded CI oversleeps), then a bit more for
    any frame in excess."""
    deadline = time.monotonic() + timeout
    while len(port.writes) < count and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)


@contextlib.contextmanager
def broadcast_gateway(port: Any, **kwargs: Any) -> Generator[int, None, None]:
    """A gateway (worker thread and TCP server) on port that relays broadcasts, yields its TCP port."""
    from pyModbusTCP.tools import tcp_to_rtu_gw
    worker = tcp_to_rtu_gw.TcpToRtuWorker(port, timeout=1.0, allow_broadcast=True, **kwargs)
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        yield tcp_port
    finally:
        worker.stop()
        srv.stop()
        thread.join(5.0)


def test_tcp2rtu_broadcast() -> None:
    """A broadcast write gets its normal response at once, the bus stays silent for broadcast_delay after it."""
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.3) as tcp_port:
        c = ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0)
        # the normal response of each function, at once (no slave answers a broadcast)
        for pdu, response in (('06000a002a', '06000a002a'), ('0f000000030105', '0f00000003'),
                              ('10000000020400010002', '1000000002'), ('16000a00ff0000', '16000a00ff0000')):
            start = time.monotonic()
            assert c.custom_request(bytes.fromhex(pdu)) == bytes.fromhex(response), pdu
            assert time.monotonic() - start < 0.2, 'answered at once, before the write on the bus'
        wait_writes(port, 4)
        # the frames are on the bus, with the broadcast address, the next one after broadcast_delay
        frames = [frame for _, frame in port.writes]
        assert [frame[0] for frame in frames] == [0] * 4
        assert frames[0] == rtu_frame(0, bytes.fromhex('06000a002a'))
        gaps = [b[0] - a[0] for a, b in zip(port.writes, port.writes[1:])]
        assert min(gaps) >= 0.3
        # a broadcast of a read (no answer: no meaning), or a malformed write: refused, never on the bus
        assert c.read_holding_registers(0, 1) is None and c.last_except == 0x01
        assert c.custom_request(bytes.fromhex('06000a')) is None and c.last_except == 0x03
        assert len(port.writes) == 4
        # a burst on an idle bus: the buffer takes a few broadcasts (4, the one the worker holds during the silence of
        # the bus, and the one it may have written already), then the next ones are refused at once (busy)
        results = []
        refused_excepts = []
        burst_start = time.monotonic()
        for i in range(8):
            start = time.monotonic()
            results.append(c.write_single_register(i, i))
            assert time.monotonic() - start < 0.2
            if not results[-1]:
                refused_excepts.append(c.last_except)
        burst_time = time.monotonic() - burst_start
        assert False in results and set(refused_excepts) == {0x06}
        first_refused = results.index(False)
        assert results[:4] == [True] * 4 and first_refused <= 6
        # once the buffer is full, a broadcast is accepted again only when the bus has taken one (one per
        # broadcast_delay): on a slow host, the burst may last long enough for that (but never more often)
        accepted = [i for i, ok in enumerate(results) if ok]
        assert len(accepted) - first_refused <= burst_time / 0.3
        # the accepted ones are all written, in order
        wait_writes(port, 4 + len(accepted))
        assert [frame[3] for _, frame in port.writes[4:]] == accepted
        c.close()


def test_tcp2rtu_broadcast_write_file_record() -> None:
    """A broadcast of 0x15 is answered with its echo: a malformed one gets exception 0x03 and is never on the bus.

    The function code alone was queued for the bus while the client got a "slave device failure" (the echo of one
    byte is refused by the server).
    """
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.0, functions=FunctionFilter.parse('6,0x15')) as tcp_port:
        with ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0) as c:
            malformed = ('15',                                  # the function code alone
                         '1500',                                # a byte count of 0
                         '150906000100000001',                  # byte count 9, 7 bytes of data
                         '15090600010000000112',                # the record data truncated
                         '150905000100000001abcd',              # reference type 5
                         '150906000100000000abcd',              # a record length of 0
                         '150906000100000001abcdef')            # a byte after the data
            for pdu in malformed:
                assert c.custom_request(bytes.fromhex(pdu)) is None and c.last_except == 0x03, pdu
            # a valid request: its echo at once, and its frame on the bus
            valid = bytes.fromhex('15 09 06 0001 0000 0001 abcd')
            assert c.custom_request(valid) == valid
            # two sub-requests
            valid2 = bytes.fromhex('15 14 06 0001 0000 0001 abcd 06 0002 0003 0002 0102 0304')
            assert c.custom_request(valid2) == valid2
        wait_writes(port, 2)
        assert [frame for _, frame in port.writes] == [rtu_frame(0, valid), rtu_frame(0, valid2)]


def test_tcp2rtu_broadcast_max_age() -> None:
    """A broadcast that waited more than broadcast_max_age for the bus is dropped, never written."""
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.4, broadcast_max_age=0.6) as tcp_port:
        with ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0) as c:
            # all accepted, the bus takes one per 0.4 s: the third one would wait 0.8 s
            assert all(c.write_single_register(i, i) for i in range(4))
            time.sleep(1.5)
        assert [frame[3] for _, frame in port.writes] == [0, 1]


def test_tcp2rtu_broadcast_flood() -> None:
    """A flood of broadcasts: the bus gets at most one per broadcast_delay, only the buffer waits after it."""
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.3) as tcp_port:
        flooder = ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0)
        end = time.monotonic() + 1.0
        accepted = refused = 0
        while time.monotonic() < end:
            if flooder.write_multiple_registers(20480, [55]):
                accepted += 1
            else:
                assert flooder.last_except == 0x06
                refused += 1
            time.sleep(0.01)
        flooder.close()
        stop = time.perf_counter()
        assert refused > 0 and accepted <= 1 + 1.0 / 0.3 + 4 + 1
        # every accepted broadcast is written, the refused ones never are
        wait_writes(port, accepted)
        assert len(port.writes) == accepted
        # the backlog after the flood is the buffer at most: 4, and the one the worker holds
        assert sum(t > stop for t, _ in port.writes) <= 4 + 1


def test_tcp2rtu_broadcast_delay_checks() -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    for delay in (-0.1, 11, float('nan'), True):
        with pytest.raises(ValueError):
            tcp_to_rtu_gw.TcpToRtuWorker(SilentSerial(), broadcast_delay=delay)  # type: ignore[arg-type]
    assert tcp_to_rtu_gw.broadcast_delay('0') == 0.0
    for text in ('-1', '11', 'nan', 'x'):
        with pytest.raises(argparse.ArgumentTypeError):
            tcp_to_rtu_gw.broadcast_delay(text)
    for kwargs in ({'broadcast_buffer': 0}, {'broadcast_buffer': 9}, {'broadcast_buffer': 2.0},
                   {'broadcast_max_age': 0}, {'broadcast_max_age': 61}, {'broadcast_max_age': float('nan')}):
        with pytest.raises(ValueError):
            tcp_to_rtu_gw.TcpToRtuWorker(SilentSerial(), **kwargs)  # type: ignore[arg-type]
    assert tcp_to_rtu_gw.broadcast_buffer('8') == 8 and tcp_to_rtu_gw.broadcast_max_age('0.5') == 0.5
    for func, text in ((tcp_to_rtu_gw.broadcast_buffer, '0'), (tcp_to_rtu_gw.broadcast_buffer, '1.5'),
                       (tcp_to_rtu_gw.broadcast_max_age, '0'), (tcp_to_rtu_gw.broadcast_max_age, 'nan')):
        with pytest.raises(argparse.ArgumentTypeError):
            func(text)


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
    worker = tcp_to_rtu_gw.TcpToRtuWorker(SilentSerial(), timeout=0.3)  # type: ignore[arg-type]
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
    worker = tcp_to_rtu_gw.TcpToRtuWorker(SilentSerial(), timeout=0.3, read_only=True)  # type: ignore[arg-type]
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


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
@pytest.mark.parametrize('args, expected', [
    ([], {'host': 'localhost', 'ipv6': False, 'idle_timeout': 300.0}),
    (['-6', '-H', '::1', '--idle-timeout', '12.5'], {'host': '::1', 'ipv6': True, 'idle_timeout': 12.5}),
    (['--ipv6', '-H', '::'], {'host': '::', 'ipv6': True, 'idle_timeout': 300.0}),
])
def test_tcp2rtu_server_settings(args: list[str], expected: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                 caplog: pytest.LogCaptureFixture) -> None:
    """-6/--ipv6 and --idle-timeout reach the server of the gateway (audit C4: it could only listen on IPv4)."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    servers: list[ModbusServer] = []

    class RecordedServer(ModbusServer):
        def start(self) -> Any:
            servers.append(self)
            raise ModbusServer.NetworkError('not started (test)')

    monkeypatch.setattr(tcp_to_rtu_gw, 'ModbusServer', RecordedServer)
    master_fd, slave_fd = os.openpty()
    try:
        with caplog.at_level(logging.CRITICAL):
            assert tcp_to_rtu_gw.main([*args, '-p', str(free_port()), os.ttyname(slave_fd)]) == 2
    finally:
        os.close(master_fd)
        os.close(slave_fd)
    assert len(servers) == 1
    server = servers[0]
    assert {name: getattr(server, name) for name in expected} == expected


@pytest.mark.parametrize('tool', ['server', 'tcp_to_rtu_gw'])
@pytest.mark.parametrize('host', ['::1', '::', 'fe80::1%eth0'])
def test_ipv6_host_needs_ipv6_flag(tool: str, host: str, capsys: pytest.CaptureFixture[str]) -> None:
    """An IPv6 -H address without -6: a clear message, not the "address family" error of the IPv4 socket."""
    if tool != 'server':
        pytest.importorskip('serial')
    import importlib
    module = importlib.import_module('pyModbusTCP.tools.' + tool)
    args = ['-H', host] + ([] if tool == 'server' else ['/dev/null'])
    with pytest.raises(SystemExit) as exc:
        module.main(args)
    assert exc.value.code == 2
    assert f'-H {host} is an IPv6 address: add -6' in capsys.readouterr().err


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
    worker = tcp_to_rtu_gw.TcpToRtuWorker(serial_port, timeout=0.1, read_only=read_only)  # type: ignore[arg-type]
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


def test_tcp2rtu_request_size_ok() -> None:
    """A request of a standard function has exactly the size of its fields: no byte missing, no byte after them."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools.tcp_to_rtu_gw import request_size_ok
    cases = [
        # fixed size: the reads, the single writes, mask write register
        ('0300000001', True), ('030000000100', False), ('03000000', False), ('0100000010', True),
        ('0600010005', True), ('0600010005deadbeef', False), ('05000100', False), ('16000a00ff0000', True),
        ('16000a00ff000000', False),
        # the functions of the serial line without data, read FIFO queue
        ('07', True), ('0700', False), ('0b', True), ('0c', True), ('11', True), ('1100', False),
        ('180001', True), ('18000100', False),
        # byte count and its data
        ('0f000000030105', True), ('0f00000003010500', False), ('0f000000030205', False), ('0f0000000301', False),
        ('0f00000003', False), ('10000000020400010002', True), ('1000000002040001000200', False),
        ('1000000005', False), ('170000000300100001020001', True), ('17000000030010000102000100', False),
        ('1700000003001000', False),
        # file records: their own format
        ('14070600010000000a', True), ('14070600010000000a00', False), ('14070500010000000a', False),
        ('150906000100000001abcd', True), ('150906000100000001abcdef', False),
        # read device identification: MEI type, read code, object id
        ('2b0e0100', True), ('2b0e010000', False), ('2b0e01', False),
        # a size unknown to the gateway: as is (diagnostics, another MEI type, a vendor function)
        ('0800000000', True), ('08000012345678', True), ('2b0d0102030405', True), ('41', True), ('41010203', True),
    ]
    for pdu, expected in cases:
        assert request_size_ok(bytes.fromhex(pdu)) is expected, pdu


@pytest.mark.parametrize('kwargs', [{'read_only': True}, {}, {'functions': 'all'}])
def test_tcp2rtu_no_hidden_frame(kwargs: dict[str, Any]) -> None:
    """The bytes after a request never reach the serial line: exception 0x03 (illegal data value).

    The PDU of a request is written in one RTU frame, with a CRC at its end. A slave that ends a frame by its size
    (and not by the silence of 3.5 chars) would take bytes after a request for the next frame: here a write to the
    slave 5 behind a read of the slave 1, through a read-only gateway.
    """
    pytest.importorskip('serial')
    from pyModbusTCP.tools import tcp_to_rtu_gw
    if kwargs.get('functions') == 'all':
        kwargs = {'functions': FunctionFilter.parse('all')}
    serial_port = RecordingSerial()
    worker = tcp_to_rtu_gw.TcpToRtuWorker(serial_port, timeout=0.1, **kwargs)  # type: ignore[arg-type]
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, functions={}, fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        read = bytes.fromhex('0300000001')
        # the CRC that ends the frame of the read for the slave 1, then the frame of a write for the slave 5
        hidden = rtu_frame(1, read)[-2:] + rtu_frame(5, bytes.fromhex('060000beef'))
        for pdu in (read + hidden, read + b'\x00', bytes.fromhex('2b0e0100') + hidden, read[:4]):
            assert raw_request(tcp_port, pdu) == bytes([pdu[0] | 0x80, 0x03]), pdu.hex()
        assert serial_port.written == []
        # the same request without the extra bytes reaches the line (no answer from the slave: exception 0x0B)
        assert raw_request(tcp_port, read) == b'\x83\x0b'
        assert serial_port.written == [rtu_frame(1, read)]
    finally:
        worker.stop()
        srv.stop()
        thread.join(5.0)


def test_tcp2rtu_broadcast_no_hidden_frame() -> None:
    """A broadcast with bytes after its fields (or missing ones) is refused (0x03), never written on the bus."""
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.0) as tcp_port:
        with ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0) as c:
            for pdu in ('060001000adeadbeef', '1000000005', '0f00007fff', '10000000020400010002ff', '16000a00ff000000'):
                assert c.custom_request(bytes.fromhex(pdu)) is None and c.last_except == 0x03, pdu
            assert c.custom_request(bytes.fromhex('060001000a')) == bytes.fromhex('060001000a')
        wait_writes(port, 1)
        assert [frame for _, frame in port.writes] == [rtu_frame(0, bytes.fromhex('060001000a'))]


def test_tcp2rtu_broadcast_error() -> None:
    """The content of a broadcast is checked like a slave would check it: 0, or the exception code of a slave."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools.tcp_to_rtu_gw import broadcast_error
    cases = [
        # write single coil: 0x0000 or 0xFF00 only
        ('050001ff00', 0), ('0500010000', 0), ('0500001234', 0x03), ('05000100ff', 0x03), ('050001ff', 0x03),
        # write single register, mask write register: any value
        ('060001ffff', 0), ('16000a00ff0000', 0),
        # write multiple coils: 1 to 1968 coils, (quantity + 7) // 8 bytes, the range in 0 to 65535
        ('0f000000030105', 0), ('0f00007fff0105', 0x03), ('0f0000000001ff', 0x03), ('0f000000090105', 0x03),
        ('0f000007b0f6' + 'ff' * 246, 0), ('0f000007b1f6' + 'ff' * 246, 0x03), ('0ffff8000902ffff', 0x02),
        ('0fffff00010101', 0),
        # write multiple registers: 1 to 123 registers, 2 bytes each, the range in 0 to 65535
        ('10000000020400010002', 0), ('1000000003020001', 0x03), ('100000000000', 0x03),
        ('100000007bf6' + '00' * 246, 0), ('100000007cf8' + '00' * 248, 0x03), ('10ffff00020400010002', 0x02),
        # write file record: its format; the size of each function
        ('150906000100000001abcd', 0), ('15', 0x03), ('1000000005', 0x03), ('060001000adeadbeef', 0x03),
    ]
    for pdu, expected in cases:
        assert broadcast_error(bytes.fromhex(pdu)) == expected, pdu


def test_tcp2rtu_broadcast_invalid_content() -> None:
    """A broadcast that every slave would refuse gets the exception of a slave, never a success, never the bus."""
    pytest.importorskip('serial')
    port = TimedSerial()
    with broadcast_gateway(port, broadcast_delay=0.0) as tcp_port:
        with ModbusClient(host='127.0.0.1', port=tcp_port, unit_id=0, timeout=3.0) as c:
            for pdu, exp_code in (('0500001234', 0x03), ('0f00007fff0105', 0x03), ('0f0000000001ff', 0x03),
                                  ('1000000003020001', 0x03), ('100000000000', 0x03), ('10ffff00020400010002', 0x02)):
                assert c.custom_request(bytes.fromhex(pdu)) is None and c.last_except == exp_code, pdu
            # the library client never sends them: its broadcasts are valid ones
            assert c.write_single_coil(1, True, broadcast=True)
            assert c.write_multiple_registers(10, [1, 2], broadcast=True)
        wait_writes(port, 2)
        expected = [rtu_frame(0, bytes.fromhex('050001ff00')), rtu_frame(0, bytes.fromhex('10000a00020400010002'))]
        assert [frame for _, frame in port.writes] == expected


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
    worker = tcp_to_rtu_gw.TcpToRtuWorker(SilentSerial(), timeout=0.2)  # type: ignore[arg-type]
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
    worker = tcp_to_rtu_gw.TcpToRtuWorker(serial_port, timeout=0.1)  # type: ignore[arg-type]
    query = tcp_to_rtu_gw.RtuQuery(host='h')
    query.request.build(raw_pdu=b'\x03\x00\x00\x00\x01', slave_address=1)
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
    ('rtu_scan', ['-b', '9600,0'], '-b/--baudrate'),
    ('rtu_scan', ['-a', '0'], '-a/--address'),
    ('rtu_scan', ['-a', '1-248'], '-a/--address'),
    ('rtu_scan', ['-a', '10-2'], 'empty range'),
    ('rtu_scan', ['--parity', 'N,X'], 'invalid parity'),
    ('rtu_scan', ['--stopbits', '1.5'], 'invalid stop bits'),
    ('rtu_scan', ['-F', '5'], '-F/--function'),
    ('rtu_scan', ['-t', '0'], '-t/--timeout'),
    ('rtu_scan', ['--rs485-rts-tx', 'low'], '--rs485-rts-tx needs --rs485'),
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
    ('tcp_to_rtu_gw', ['--functions', '3,x'], '--functions'),
    ('tcp_to_rtu_gw', ['--functions', '0x80'], 'out of 1 to 127'),
    ('tcp_to_rtu_gw', ['--functions', '3/1'], 'only the function 0x2B has a MEI type'),
    ('tcp_to_rtu_gw', ['--functions', ''], 'empty function code'),
    ('tcp_to_rtu_gw', ['--functions', '3', '--read-only'], 'not allowed with argument'),
    ('tcp_to_rtu_gw', ['--ipv6-host-prefix', '0'], '--ipv6-host-prefix'),
    ('tcp_to_rtu_gw', ['--ipv6-host-prefix', '129'], '--ipv6-host-prefix'),
    ('tcp_to_rtu_gw', ['--ipv6-host-prefix', '/64'], '--ipv6-host-prefix'),
    ('tcp_to_rtu_gw', ['--max-connections', '0'], '--max-connections'),
    ('tcp_to_rtu_gw', ['--max-connections-per-host', 'x'], '--max-connections-per-host'),
    ('rtu_to_tcp_gw', ['-u', '256'], '-u/--unit-id'),
    ('rtu_to_tcp_gw', ['-u', 'one'], '-u/--unit-id'),
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


def test_tcp2rtu_function_filter() -> None:
    """--functions: parse, match (0x2B by MEI type), log text; --read-only is a shortcut, exclusive with it."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools._common import DEFAULT_FUNCTIONS, READ_ONLY_FUNCTIONS
    from pyModbusTCP.tools.tcp_to_rtu_gw import TcpToRtuWorker
    flt = FunctionFilter.parse(' 1, 0x03 ,16,0x2B, 43/13 ')
    assert flt == FunctionFilter(frozenset({1, 3, 16}), frozenset({0x0E, 0x0D}))
    assert flt.text() == '0x01, 0x03, 0x10, 0x2B/0x0D, 0x2B/0x0E'
    assert flt.allows(b'\x03\x00\x00\x00\x01') and not flt.allows(b'\x06\x00\x00\x00\x01')
    assert flt.allows(b'\x2b\x0d') and not flt.allows(b'\x2b\x0c') and not flt.allows(b'\x2b')
    assert FunctionFilter.parse('ALL').codes is None and FunctionFilter.parse('all').allows(b'\x5a')
    assert FunctionFilter.parse('all').text() == 'every function'
    assert DEFAULT_FUNCTIONS.text() == '0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0F, 0x10, 0x16, 0x17, 0x2B/0x0E'
    assert READ_ONLY_FUNCTIONS.text() == '0x01, 0x02, 0x03, 0x04, 0x2B/0x0E'
    for bad in ('0', '128', '3,,4', 'x', '0x2B/256', '6/1'):
        with pytest.raises(ValueError):
            FunctionFilter.parse(bad)
    with pytest.raises(ValueError):
        FunctionFilter(frozenset({0x2B}))
    worker = TcpToRtuWorker(SilentSerial())  # type: ignore[arg-type]
    assert worker.functions == DEFAULT_FUNCTIONS
    assert TcpToRtuWorker(SilentSerial(), read_only=True).functions == READ_ONLY_FUNCTIONS  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TcpToRtuWorker(SilentSerial(), read_only=True, functions=DEFAULT_FUNCTIONS)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        TcpToRtuWorker(SilentSerial(), functions='1,2')  # type: ignore[arg-type]
    # the client hosts that share the serial line: IPv6 clients by network of ipv6_host_prefix bits
    assert TcpToRtuWorker(SilentSerial()).ipv6_host_prefix == 64  # type: ignore[arg-type]
    assert TcpToRtuWorker(SilentSerial(), ipv6_host_prefix=128).ipv6_host_prefix == 128  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TcpToRtuWorker(SilentSerial(), ipv6_host_prefix=0)  # type: ignore[arg-type]
    # the host key of a request follows the prefix
    from pyModbusTCP.server import Request, RequestContext
    from pyModbusTCP.server.session import ClientInfo
    for prefix, expected in ((64, '2001:db8:1:2::/64'), (128, '2001:db8:1:2::7/128')):
        worker = TcpToRtuWorker(SilentSerial(), ipv6_host_prefix=prefix)  # type: ignore[arg-type]
        hosts: list[str] = []

        def relay(req: Request, host: str = '', hosts: list[str] = hosts) -> bytes:
            hosts.append(host)
            return b'\x03\x00'

        worker._relay = relay  # type: ignore[method-assign]
        ctx = RequestContext(client=ClientInfo('2001:db8:1:2::7', 50000), unit_id=1, function_code=3)
        worker.relay_request(Request(pdu=b'\x03\x00\x00\x00\x01', ctx=ctx, store=MemoryStore(0)))
        assert hosts == [expected]


def test_gateway_api_checks() -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import _rtu, rtu_to_tcp_gw
    with pytest.raises(ValueError):
        _rtu.t35(0, 8, 'N', 1)
    for address in (0, 248, 255):
        with pytest.raises(ValueError):
            rtu_to_tcp_gw.RtuToTcpWorker(SilentSerial(), ModbusClient(), slave_address=address)  # type: ignore


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
    worker = rtu_to_tcp_gw.RtuToTcpWorker(port=port, mbus_cli=tcp_cli, slave_address=30)
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
        wait_input_flushed(worker)
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


def wait_input_flushed(worker: Any, timeout: float = 10.0) -> None:
    """Wait until a rtu2tcp worker has flushed the input buffer of its line, at its start: a frame written before is
    lost. Not a fixed delay: a busy host (like a CI runner) may start the worker thread late, the first frame of a test
    was then discarded (a broadcast lost: the read after it got the old value)."""
    deadline = time.monotonic() + timeout
    while worker._flush_pending:
        assert time.monotonic() < deadline, 'the worker never flushed its input buffer'
        time.sleep(0.01)


@contextlib.contextmanager
def rtu2tcp_gateway(tcp_port: int, **kwargs: Any) -> Generator[tuple[int, Any], None, None]:
    """A rtu2tcp gateway (slave 30) on a pseudo-terminal, relaying to tcp_port: yields (master fd, worker)."""
    serial = pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_to_tcp_gw
    master_fd, slave_fd = os.openpty()
    os.set_blocking(master_fd, False)
    port = serial.Serial(os.ttyname(slave_fd), baudrate=19200)
    tcp_cli = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=2.0)
    worker = rtu_to_tcp_gw.RtuToTcpWorker(port=port, mbus_cli=tcp_cli, slave_address=30, **kwargs)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        wait_input_flushed(worker)
        yield master_fd, worker
    finally:
        worker.stop()
        thread.join(5.0)
        port.close()
        tcp_cli.close()
        os.close(master_fd)
        os.close(slave_fd)


def read_answer(master_fd: int, timeout: float) -> bytes:
    """The bytes written by the gateway on the line within timeout (stops at the first complete frame)."""
    answer = b''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            answer += os.read(master_fd, 256)
        except BlockingIOError:
            pass
        if len(answer) >= 5 and crc16(answer) == 0:
            break
        time.sleep(0.02)
    return answer


def slow_function(srv: ModbusServer, func_code: int, delay: float) -> list[float]:
    """Make a function of srv answer after delay: returns the list of the times of its calls."""
    handler = srv.functions[func_code]
    calls: list[float] = []

    def slow(req: Any) -> bytes:
        calls.append(time.monotonic())
        time.sleep(delay)
        return handler(req)

    srv.functions[func_code] = slow
    return calls


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
def test_rtu2tcp_broadcast_does_not_block(server: tuple[ModbusServer, int]) -> None:
    """A broadcast to a slow server doesn't block the serial line: the next frames are received on time, one by one."""
    srv, tcp_port = server
    srv.store.app.holding_registers.write(0, [0x1234])
    slow_function(srv, 0x06, 0.5)
    with rtu2tcp_gateway(tcp_port, allow_broadcast=True) as (master_fd, _worker):
        # a broadcast write, then (while the server writes it) a frame for another slave, then a read for the
        # gateway: before, the 2 frames waited in the buffer of the line during the broadcast and were read as one
        # frame, dropped (CRC error), the master got no answer
        # (0.15 s between the frames, all within the 0.5 s of the server: a busy host, like a CI runner, may wake the
        # worker late, the frames must not come so close that the worker reads 2 of them as one)
        os.write(master_fd, rtu_frame(0, bytes.fromhex('0600020007')))
        time.sleep(0.15)
        os.write(master_fd, rtu_frame(31, bytes.fromhex('0300000001')))
        time.sleep(0.15)
        os.write(master_fd, rtu_frame(30, bytes.fromhex('0300020001')))
        # the read is sent after the broadcast (the order of the bus): it sees its value
        assert read_answer(master_fd, 3.0) == rtu_frame(30, bytes.fromhex('03020007'))


def test_rtu2tcp_broadcast_backlog(caplog: pytest.LogCaptureFixture) -> None:
    """Broadcasts faster than the server: BROADCAST_BACKLOG wait for it (plus the one in progress), the next ones are
    dropped (logged), never queued without limit; the waiting ones are sent in the order of the bus.

    No serial line here: the frames are given to the worker one by one (on a pseudo-terminal, the timers of a busy
    host can merge 2 frames sent close together into one, dropped for its CRC), and the server answers only when the
    test releases it.
    """
    pytest.importorskip('serial')
    from pyModbusTCP.tools import _rtu, rtu_to_tcp_gw
    backlog = rtu_to_tcp_gw.BROADCAST_BACKLOG
    started, release = threading.Event(), threading.Event()
    sent: list[bytes] = []

    class BlockedClient:
        """A Modbus/TCP client whose server answers only once released."""
        timeout = 2.0

        def custom_request(self, pdu: bytes) -> bytes:
            sent.append(pdu)
            started.set()
            release.wait(5.0)
            return pdu

    worker = rtu_to_tcp_gw.RtuToTcpWorker(
        SilentSerial(), BlockedClient(), slave_address=30, allow_broadcast=True)  # type: ignore[arg-type]
    pdus = [bytes.fromhex('06%04x0001' % (10 + n)) for n in range(backlog + 4)]

    def receive(pdu: bytes) -> None:
        worker.request = _rtu.ModbusRTUFrame(rtu_frame(0, pdu))
        worker.handle_request()

    try:
        with caplog.at_level(logging.WARNING, logger='mbtcp-rtu2tcp'):
            # the first one is taken by the relay thread at once (in progress, the server doesn't answer yet)
            receive(pdus[0])
            assert started.wait(5.0)
            # the next ones wait for the server: backlog of them, the others are dropped
            for pdu in pdus[1:]:
                receive(pdu)
        assert worker._jobs.qsize() == backlog
        assert any('Broadcast dropped' in r.getMessage() for r in caplog.records)
    finally:
        release.set()
        worker.stop()
        assert worker._relay_thread is not None
        worker._relay_thread.join(5.0)
    # the one in progress, then the waiting ones, in the order of the bus; never the dropped ones
    assert sent == pdus[:backlog + 1]


def test_rtu2tcp_client_never_closed_while_the_relay_uses_it() -> None:
    """At the end, the Modbus/TCP client is closed only once the relay thread is done (ModbusClient is not
    thread-safe): a request held longer than the bound of the wait of run() is never broken by a close()."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import _rtu, rtu_to_tcp_gw
    started, release = threading.Event(), threading.Event()

    class BlockedClient:
        """A Modbus/TCP client whose request lasts until released, its close() calls are counted."""
        timeout = 0.05
        closed = 0

        def custom_request(self, pdu: bytes) -> bytes:
            started.set()
            release.wait(10.0)
            return pdu

        def close(self) -> None:
            self.closed += 1

    client = BlockedClient()
    worker = rtu_to_tcp_gw.RtuToTcpWorker(
        SilentSerial(), client, slave_address=30, allow_broadcast=True)  # type: ignore[arg-type]
    try:
        assert not worker.relay_running
        # a broadcast: the relay thread takes it, its request is held
        worker.request = _rtu.ModbusRTUFrame(rtu_frame(0, bytes.fromhex('0600010001')))
        worker.handle_request()
        assert started.wait(5.0)
        # the end of run(): its wait for the relay thread ((4 + 1) * 0.05 + 1 s) ends before the request
        worker.stop()
        worker.run()
        assert worker.relay_running
        assert worker.close_client() is False and client.closed == 0
    finally:
        release.set()
    assert worker._relay_thread is not None
    worker._relay_thread.join(5.0)
    assert not worker.relay_running
    assert worker.close_client() is True and client.closed == 1


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
def test_rtu2tcp_late_response_dropped(server: tuple[ModbusServer, int], caplog: pytest.LogCaptureFixture) -> None:
    """A response ready after the master has sent another frame is not written (it would collide on the bus)."""
    srv, tcp_port = server
    srv.store.app.holding_registers.write(0, [0x1234])
    slow_function(srv, 0x03, 0.5)
    with caplog.at_level(logging.WARNING, logger='mbtcp-rtu2tcp'), rtu2tcp_gateway(tcp_port) as (master_fd, _worker):
        os.write(master_fd, rtu_frame(30, bytes.fromhex('0300000001')))
        # the master gives up before the server answers, it talks to another slave
        time.sleep(0.2)
        os.write(master_fd, rtu_frame(31, bytes.fromhex('0300000001')))
        assert read_answer(master_fd, 1.0) == b''
        # the next request, the master waits for it: answered
        os.write(master_fd, rtu_frame(30, bytes.fromhex('0300000001')))
        assert read_answer(master_fd, 3.0) == rtu_frame(30, bytes.fromhex('03021234'))
    assert any('Response dropped' in r.getMessage() for r in caplog.records)


def test_rtu2tcp_filters() -> None:
    """S10 of the audit: the gateway relays the functions of its filter only (the standard data functions by default,
    like mbtcp-tcp2rtu); an invalid function code is never relayed nor answered, a broadcast only for a write."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import _rtu, rtu_to_tcp_gw
    from pyModbusTCP.tools._common import DEFAULT_FUNCTIONS, READ_ONLY_FUNCTIONS

    class EchoClient:
        """A Modbus/TCP client whose server answers every request with its PDU."""
        timeout = 2.0

        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def custom_request(self, pdu: bytes) -> bytes:
            self.sent.append(pdu)
            return pdu

    def run(frames: list[tuple[int, str]], **kwargs: Any) -> tuple[list[str], list[str]]:
        """Give the frames to a gateway (slave 30): returns the PDUs sent to the server and the answers (hex)."""
        cli = EchoClient()
        worker = rtu_to_tcp_gw.RtuToTcpWorker(SilentSerial(), cli, slave_address=30,  # type: ignore[arg-type]
                                              **kwargs)
        answers = []
        try:
            for slave, pdu_hex in frames:
                worker.request = _rtu.ModbusRTUFrame(rtu_frame(slave, bytes.fromhex(pdu_hex)))
                worker.response = _rtu.ModbusRTUFrame()
                worker.handle_request()
                answers.append(worker.response.pdu.hex() if worker.response.is_set else '')
        finally:
            worker.stop()
            if worker._relay_thread is not None:
                worker._relay_thread.join(5.0)
        return [pdu.hex() for pdu in cli.sent], answers

    assert rtu_to_tcp_gw.RtuToTcpWorker(SilentSerial(), EchoClient()).functions == DEFAULT_FUNCTIONS  # type: ignore
    # default filter: a read is relayed, a diagnostic gets "illegal function" from the gateway, invalid codes are
    # ignored, never sent to the server
    sent, answers = run([(30, '0300000001'), (30, '0800000000'), (30, '8302'), (30, '00'), (30, '2b0d00')])
    assert sent == ['0300000001']
    assert answers == ['0300000001', '8801', '', '', 'ab01']
    # --functions all: the diagnostic is relayed, never an invalid code
    sent, _ = run([(30, '0800000000'), (30, '8302')], functions=FunctionFilter(None))
    assert sent == ['0800000000']
    # --read-only: the reads only
    sent, answers = run([(30, '0300000001'), (30, '0600000001')], read_only=True)
    assert (sent, answers) == (['0300000001'], ['0300000001', '8601'])
    assert rtu_to_tcp_gw.RtuToTcpWorker(SilentSerial(), EchoClient(),  # type: ignore[arg-type]
                                        read_only=True).functions == READ_ONLY_FUNCTIONS
    # broadcasts (never answered): a write of the filter only, never in read-only mode
    sent, answers = run([(0, '0600000001'), (0, '0300000001'), (0, '0800000000')], allow_broadcast=True)
    assert (sent, answers) == (['0600000001'], ['', '', ''])
    sent, _ = run([(0, '0600000001')], allow_broadcast=True, read_only=True)
    assert sent == []
    with pytest.raises(ValueError):
        rtu_to_tcp_gw.RtuToTcpWorker(SilentSerial(), EchoClient(), read_only=True,  # type: ignore[arg-type]
                                     functions=DEFAULT_FUNCTIONS)


# --- mbtcp-rtu-scan ---

class SlavesSerial:
    """A serial port with Modbus/RTU slaves that only answer at 19200 bauds, 8E1: slave 17 has holding register 0 =
    1234 and coil 0 on, slave 5 answers every read with exception 0x02. Options: a local echo, a noise answered at
    19200 8N1, and baud rates refused by the port.
    """
    port, bytesize, is_open, timeout = 'slaves', 8, True, 0.0

    def __init__(self, echo: bool = False, noise: bool = False, refused: tuple[int, ...] = ()) -> None:
        self._baudrate, self.parity, self.stopbits = 9600, 'N', 1
        self.echo, self.noise, self.refused = echo, noise, refused
        self.buffer = bytearray()
        self.requests: list[tuple[int, str, float, bytes]] = []

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        if value in self.refused:
            raise OSError(22, 'Invalid argument')
        self._baudrate = value

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def reset_input_buffer(self) -> None:
        self.buffer.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    def read(self, size: int = 1) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def write(self, data: bytes) -> int:
        self.requests.append((self.baudrate, self.parity, self.stopbits, bytes(data)))
        if self.echo:
            self.buffer += data
        settings = (self.baudrate, self.parity, self.stopbits)
        if self.noise and settings == (19200, 'N', 1):
            self.buffer += b'\xff\x13\x00'
        if settings != (19200, 'E', 1) or crc16(data) != 0:
            return len(data)
        address, function = data[0], data[1]
        if address == 17 and function in (1, 2):
            self.buffer += rtu_frame(17, bytes([function, 1, 0x01]))
        elif address == 17 and function == 3:
            self.buffer += rtu_frame(17, bytes([3, 2, 0x04, 0xD2]))
        elif address in (5, 17):
            self.buffer += rtu_frame(address, bytes([function | 0x80, 0x02 if address == 5 else 0x01]))
        return len(data)


def run_rtu_scan(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, args: list[str],
                 port: SlavesSerial | None = None) -> tuple[int, str, SlavesSerial]:
    """Run mbtcp-rtu-scan on a SlavesSerial port: (exit status, stdout, the port)."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_scan
    port = port or SlavesSerial()
    monkeypatch.setattr(rtu_scan, 'Serial', lambda **_kwargs: port)
    status = rtu_scan.main(args + ['-t', '0.001', 'COM9'])
    return status, capsys.readouterr().out, port


def test_rtu_scan_frame_bounded() -> None:
    """S10 of the audit: a line that never goes silent can't hold the scan, a frame is MAX_RTU_FRAME bytes at most."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_scan
    from pyModbusTCP.tools._rtu import MAX_RTU_FRAME

    class EndlessSerial(SilentSerial):
        """A line that always has bytes to read."""
        in_waiting = 64

        def read(self, size: int = 1) -> bytes:
            return b'\x55' * size

    scanner = rtu_scan.Scanner(EndlessSerial())  # type: ignore[arg-type]
    assert len(scanner.read_frame(0.1, 0.01)) == MAX_RTU_FRAME


@pytest.mark.parametrize('args', [['--functions', '1,3', '--read-only'], ['--functions', '0x80']])
def test_rtu2tcp_invalid_filter_args(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_to_tcp_gw
    with pytest.raises(SystemExit) as exc:
        rtu_to_tcp_gw.main([*args, '/dev/null'])
    assert exc.value.code == 2


def test_rtu_scan_finds_slaves(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The first slave found gives the settings, --all lists the other slaves of the bus with them."""
    status, out, port = run_rtu_scan(capsys, monkeypatch, ['--all'])
    assert status == 0
    lines = out.splitlines()
    assert lines[0] == ('probe request: read holding registers (function 0x03) of holding register 0, '
                        'response timeout 0.001 s')
    # 9600 8N1, 8E1, 8O1, 8N2, then 19200 8N1, 8E1: slave 5 is the first address that answers
    assert [line for line in lines if line.startswith('test:')] == [
        'test: 9600 8N1, addresses 1-247', 'test: 9600 8E1, addresses 1-247', 'test: 9600 8O1, addresses 1-247',
        'test: 9600 8N2, addresses 1-247', 'test: 19200 8N1, addresses 1-247', 'test: 19200 8E1, addresses 1-247']
    assert 'slave found at address 5 (19200 8E1), answer: exception 0x02 (illegal data address)' in lines
    assert '  mbtcp-tcp2rtu -b 19200 --parity E --stopbits 1 COM9' in lines
    assert lines[-1] == 'slave found at address 17, answer: holding register 0 = 1234'
    # the addresses in ascending order, the requests of the function 3 (holding register 0)
    assert [frame[0] for *_, frame in port.requests[:247]] == list(range(1, 248))
    assert port.requests[0][3] == rtu_frame(1, bytes.fromhex('0300000001'))
    assert len(port.requests) == 5 * 247 + 5 + 246
    assert not port.is_open


def test_rtu_scan_options(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """-b, --parity, -a and -F restrict the scan, in the given order; --stopbits 2 also tries 8E2 and 8O2."""
    status, out, port = run_rtu_scan(capsys, monkeypatch, ['-b', '19200', '--parity', 'E', '-a', '20,17', '-F', '1'])
    assert status == 0
    assert out.splitlines()[0].startswith('probe request: read coils (function 0x01) of coil 0,')
    assert 'slave found at address 17 (19200 8E1), answer: coil 0 = 1' in out.splitlines()
    assert [(frame[0], frame[1]) for *_, frame in port.requests] == [(20, 1), (17, 1)]
    from pyModbusTCP.tools import rtu_scan
    formats = rtu_scan.serial_formats(argparse.Namespace(baudrate=[9600], parity=['N', 'E'], stopbits=[2, 1]))
    assert [str(fmt) for fmt in formats] == ['9600 8N2', '9600 8E2', '9600 8N1', '9600 8E1']


def test_rtu_scan_not_found(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """No slave: exit status 1, the answers with a bad CRC are listed as hints; a refused baud rate is skipped."""
    port = SlavesSerial(noise=True, refused=(9600,))
    status, out, _ = run_rtu_scan(capsys, monkeypatch, ['-b', '9600,19200', '--parity', 'N', '-a', '3'], port)
    assert status == 1
    assert '9600 8N1: refused by the serial port, skipped' in out
    assert 'no slave found' in out
    assert '19200 8N1, address 3: [3 bytes] ff 13 00' in out


def test_rtu_scan_echo(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The local echo of an adapter is not taken for an answer."""
    port = SlavesSerial(echo=True)
    status, out, _ = run_rtu_scan(capsys, monkeypatch, ['-b', '9600,19200', '--parity', 'E', '-a', '17'], port)
    assert status == 0
    assert 'slave found at address 17 (19200 8E1), answer: holding register 0 = 1234' in out
    assert 'no answer' not in out and 'bad CRC' not in out


def test_rtu_scan_answer_size() -> None:
    """A response is the answer of a slave only with the byte count of one value: 1 byte for a bit, 2 for a register.

    A byte count of 0 was taken for an answer, and its CRC printed as the value of the register.
    """
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_scan
    from pyModbusTCP.tools._rtu import ModbusRTUFrame
    cases = [
        # (function of the probe, PDU of the response, answer?)
        (3, '03021234', True), (4, '04021234', True), (1, '010101', True), (2, '020100', True),
        (3, '0300', False), (3, '030112', False), (3, '030412345678', False),  # 0, 1 or 4 bytes for one register
        (1, '0100', False), (1, '01020100', False),                            # 0 or 2 bytes for one coil
        (3, '8302', True), (3, '830200', False), (3, '8402', False),           # an exception: of this function only
    ]
    for function, pdu, expected in cases:
        scanner = rtu_scan.Scanner(SilentSerial(), function=function)  # type: ignore[arg-type]
        frame = ModbusRTUFrame(rtu_frame(7, bytes.fromhex(pdu)))
        assert scanner.is_answer(frame, 7) is expected, (function, pdu)
    assert not rtu_scan.Scanner(SilentSerial()).is_answer(  # type: ignore[arg-type]
        ModbusRTUFrame(rtu_frame(8, bytes.fromhex('03021234'))), 7)


def test_rtu_scan_types() -> None:
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_scan
    assert rtu_scan.address_list('1-3, 0x10,247') == [1, 2, 3, 16, 247]
    assert rtu_scan.ranges([1, 2, 3, 16, 247, 5]) == '1-3,16,247,5'
    assert rtu_scan.ranges(list(range(1, 248))) == '1-247'
    assert rtu_scan.baudrate_list('9600, 19200') == [9600, 19200]
    assert rtu_scan.parity_list('e,N') == ['E', 'N']
    assert rtu_scan.stopbits_list('2,1') == [2, 1]


@pytest.mark.skipif(os.name == 'nt', reason='needs a pseudo-terminal (POSIX only)')
def test_rtu_scan_rs485_refused(caplog: pytest.LogCaptureFixture) -> None:
    """A pseudo-terminal has no RS-485 mode: exit status 2, before any request."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools import rtu_scan
    master_fd, slave_fd = os.openpty()
    try:
        with caplog.at_level(logging.CRITICAL):
            assert rtu_scan.main(['--rs485', os.ttyname(slave_fd)]) == 2
        assert 'RS-485 mode' in caplog.text
    finally:
        os.close(master_fd)
        os.close(slave_fd)


# --- common ---

@pytest.mark.parametrize('command', ['server', 'client', 'tcp2rtu', 'rtu2tcp', 'rtu-scan'])
def test_help_and_version(command: str) -> None:
    if command in ('tcp2rtu', 'rtu2tcp', 'rtu-scan'):
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
    assert [_common.ip_or_network(v) for v in ('10.0.0.5', '10.0.0.0/24', '::1')] == ['10.0.0.5', '10.0.0.0/24', '::1']
    for bad in ('10.0.0.5/24', 'host.local', '10.0.0.0/33'):
        with pytest.raises(argparse.ArgumentTypeError):
            _common.ip_or_network(bad)


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


@pytest.mark.parametrize('command', ['tcp2rtu', 'rtu2tcp', 'rtu-scan'])
def test_gateway_without_pyserial(command: str) -> None:
    """Without pyserial, a serial tool exits with status 2 and the way to install it (the other tools still work)."""
    code = ('import sys; sys.modules["serial"] = None; '  # "import serial" now fails like a missing pyserial
            'from pyModbusTCP.tools import _entry; '
            'import pyModbusTCP.tools.client, pyModbusTCP.tools.server; '  # the other tools still work
            f'sys.exit(_entry.main([{command!r}, "--help"]))')
    proc = subprocess.run([sys.executable, '-c', code], check=False, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, proc.stderr
    assert 'pip install "pyModbusTCP[serial]"' in proc.stderr


# --- mbtcp-tcp2rtu: a late reply of a slave is never taken for the response of another request (audit S-02) ---

def test_tcp2rtu_response_matches() -> None:
    """The content of a response is checked against its request (RTU frames have no transaction id)."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools.tcp_to_rtu_gw import response_matches
    cases = [
        # read holding registers, 1 register: 2 bytes of data, no more no less
        ('0300640001', '03021234', True), ('0300640001', '030412345678', False), ('0300640001', '0302123456', False),
        # read coils, 10 bits: 2 bytes
        ('01000a000a', '01020300', True), ('01000a000a', '010103', False),
        # write single register / coil: the echo of the request
        ('0600010005', '0600010005', True), ('0600010005', '0600020005', False), ('0500010000', '05000100ff', False),
        # write multiple registers: address and quantity
        ('10000a00020400010002', '10000a0002', True),
        ('10000a00020400010002', '10000a0003', False),
        # mask write register: the echo
        ('16000a00ff0000', '16000a00ff0000', True), ('16000a00ff0000', '16000a00ff0001', False),
        # write/read: the size of the read
        ('170000000300100001020001', '1706000100020003', True), ('170000000300100001020001', '17020001', False),
        # read device identification: MEI type and read code echoed
        ('2b0e0100', '2b0e01830000010003414243', True), ('2b0e0100', '2b0e02830000010003414243', False),
        # exception: exactly the function code and the exception code
        ('0300640001', '8302', True), ('0300640001', '830200', False),
        # unknown function: accepted as is
        ('41', '4101020304', True),
    ]
    for req, resp, expected in cases:
        assert response_matches(bytes.fromhex(req), bytes.fromhex(resp)) is expected, (req, resp)


def test_tcp2rtu_find_response_in_merged_frames() -> None:
    """A late reply read with the response as one block (too short a silence between them): the response is found."""
    pytest.importorskip('serial')
    from pyModbusTCP.tools.tcp_to_rtu_gw import CheckedRTUFrame, find_response
    request = CheckedRTUFrame(rtu_frame(1, bytes.fromhex('0300640001')))
    late = rtu_frame(1, bytes.fromhex('030411112222'))
    good = rtu_frame(1, bytes.fromhex('0302abcd'))
    assert find_response(good, request) == good
    assert find_response(late + good, request) == good
    assert find_response(good + late, request) == good
    assert find_response(late, request) == b''
    assert find_response(late + late, request) == b''
    assert find_response(b'\x01\x03', request) == b''


@contextlib.contextmanager
def late_slave_gateway(late_reply: bytes) -> Generator[int, None, None]:
    """A gateway on a pseudo-terminal, with an RTU slave that answers the first request after the gateway timeout
    (with late_reply), then each request with the values of its addresses (0xAA00 + address).

    Timeline (in s, from the first request): the gateway gives up the first request at 1.0 and sends the next one,
    the late reply comes at 1.3, the response of the next request at 1.6, before its own timeout at 2.0. Every margin
    is 0.3 s or more: a loaded CI runner (macOS) is late by tens of ms (pty flush, serial timeouts, scheduling).

    :returns: the TCP port of the gateway
    """
    serial = pytest.importorskip('serial')
    if not hasattr(os, 'openpty'):
        pytest.skip('no pseudo-terminal on this OS')
    import select

    from pyModbusTCP.tools import tcp_to_rtu_gw
    master, slave = os.openpty()
    # pyserial sets the raw mode of the line (like in test_rtu2tcp_relay)
    port = serial.Serial(os.ttyname(slave), baudrate=115200)
    timeout = 1.0
    late_delay = timeout + 0.3
    response_delay = 0.6
    stop = threading.Event()

    def rtu_slave() -> None:
        first = True
        while not stop.is_set():
            # never block in read(): on macOS, close() of a descriptor waits for a read() of another thread on it,
            # the teardown below would hang; the thread is joined before the descriptors are closed
            if not select.select([master], [], [], 0.05)[0]:
                continue
            try:
                request = os.read(master, 256)
            except OSError:
                return
            if len(request) < 8:
                continue
            _, func_code, address, quantity = struct.unpack('>BBHH', request[:6])
            if first:
                first = False
                time.sleep(late_delay)
                os.write(master, late_reply)
                continue
            regs = [0xAA00 + address + i for i in range(quantity)]
            time.sleep(response_delay)
            os.write(master, rtu_frame(1, struct.pack(f'>BB{quantity}H', func_code, 2 * quantity, *regs)))

    slave_thread = threading.Thread(target=rtu_slave, daemon=True)
    slave_thread.start()
    worker = tcp_to_rtu_gw.TcpToRtuWorker(port, timeout=timeout)
    thread = threading.Thread(target=worker.loop, daemon=True)
    thread.start()
    srv = ModbusServer(host='127.0.0.1', port=0, no_block=True, store=MemoryStore(0), functions={},
                       fallback=worker.relay_request)
    _, tcp_port = srv.start()
    try:
        yield tcp_port
    finally:
        stop.set()
        worker.stop()
        srv.stop()
        thread.join(5.0)
        slave_thread.join(5.0)
        port.close()
        os.close(master)
        os.close(slave)


def test_tcp2rtu_late_reply_is_not_the_next_response() -> None:
    """A slave answers a read of 2 registers after the timeout: the next read (1 register) never gets this reply
    (refused by its content, S-02), it gets its own response."""
    with late_slave_gateway(rtu_frame(1, bytes.fromhex('030411112222'))) as tcp_port:
        results: dict[str, Any] = {}

        def client_a() -> None:
            c = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=5)
            results['a'] = (c.read_holding_registers(0, 2), c.last_except)
            c.close()

        def client_b() -> None:
            time.sleep(0.2)  # queued while the request of A is on the line (until 1.0 s)
            c = ModbusClient(host='127.0.0.1', port=tcp_port, timeout=5)
            results['b'] = c.read_holding_registers(100, 1)
            c.close()

        threads = [threading.Thread(target=client_a), threading.Thread(target=client_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10.0)
    # A: no valid answer before the timeout of the gateway; B: its own value, never the late reply of A
    assert results['a'] == (None, 0x0B), results
    assert results['b'] == [0xAA00 + 100], results
