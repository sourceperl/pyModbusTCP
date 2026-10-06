#!/usr/bin/env python

import socket
import time
from typing import Callable

from pyModbusTCP import __version__ as VERSION
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import ModbusServer

HOST = "127.0.0.1"


def get_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def benchmark(name: str, n: int, fn: Callable) -> None:
    start = time.perf_counter()

    for _ in range(n):
        if not fn():
            raise RuntimeError(f"Error during {name}")

    elapsed = time.perf_counter() - start
    rps = n / elapsed
    latency_ms = (elapsed / n) * 1000

    print(f"--- {name} ---")
    print(f"  Total requests : {n:_}")
    print(f"  Elapsed time   : {elapsed:.4f} s")
    print(f"  Throughput     : {rps:.2f} req/s")
    print(f"  Average latency: {latency_ms:.3f} ms\n")


if __name__ == "__main__":
    srv = ModbusServer(host=HOST, port=get_free_tcp_port(), no_block=True)
    srv.start()

    cli = ModbusClient(host=HOST, port=srv.port, auto_open=True, auto_close=False)

    try:
        print(f"Starting benchmark of version {VERSION} on ModbusServer ({HOST}:{srv.port})...\n")
        data = [False, True, False, True] * 200
        len_data = len(data)

        cli.read_coils(0, 1)

        benchmark("Read Benchmark", n=10_000,
                  fn=lambda: cli.read_coils(0, len_data) is not None)

        benchmark("Write Benchmark", n=10_000,
                  fn=lambda: cli.write_multiple_coils(0, data) is True)

    finally:
        cli.close()
        srv.stop()
