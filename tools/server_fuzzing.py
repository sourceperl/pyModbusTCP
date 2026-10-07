#!/usr/bin/env python3

"""
Modbus TCP fuzzer: sends random/malformed frames to stress-test a Modbus server.
Useful for finding crashes, hangs, or unexpected behavior in Modbus implementations.
"""

import argparse
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from random import randint
from typing import Optional


@dataclass
class FuzzerConfig:
    """Configuration for the fuzzer."""
    host: str
    port: int
    min_frame_size: int
    max_frame_size: int
    socket_timeout: float
    display_limit: int
    reconnect_delay: float = 0.5
    max_retries: int = 3


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return a logger instance."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)7s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger(__name__)


def format_frame(frame: bytes | bytearray, limit: int = 30) -> str:
    """
    Format bytes frame as hex string with MBAP header highlighted.

    Example: "[00-01-00-00-00-05-01] 03-00-01-00-02"
    Returns: "[00-01-00-00-00-05-01] 03-00-01-00-02..." (if truncated)
    """
    truncated = False
    if len(frame) > limit:
        frame = frame[:limit]
        truncated = True

    if len(frame) >= 7:
        mbap_part = frame[:7].hex("-").upper()
        pdu_part = frame[7:].hex("-").upper()
        result = f"[{mbap_part}] {pdu_part}".strip()
    else:
        result = frame.hex("-").upper()

    return f"{result}..." if truncated else result


def generate_frame(config: FuzzerConfig) -> bytearray:
    """Generate a random (probably malformed) Modbus TCP frame."""
    frame = bytearray(os.urandom(randint(config.min_frame_size, config.max_frame_size)))

    # Modbus TCP MBAP header structure (7 bytes):
    # Bytes 0-1: Transaction ID
    # Bytes 2-3: Protocol ID (should be 0x0000 for Modbus)
    # Bytes 4-5: Length field (payload size, max 252)
    # Byte 6: Unit ID

    if len(frame) >= 7:
        frame[2:4] = b'\x00\x00'  # Enforce protocol ID
        frame[4:6] = (len(frame) - 6).to_bytes(2, byteorder='big')  # Fix length field

    return frame


def create_socket(config: FuzzerConfig) -> Optional[socket.socket]:
    """Create and configure a TCP socket."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(config.socket_timeout)
        sock.connect((config.host, config.port))
        return sock
    except (ConnectionRefusedError, OSError):
        return None


def send_and_receive(sock: socket.socket, frame: bytearray, request_num: int,
                     config: FuzzerConfig, logger: logging.Logger,) -> bool:
    """
    Send a frame and attempt to receive a response.

    Returns True if connection is still alive, False if closed.
    """
    try:
        sock.sendall(frame)
        logger.info(f"[{request_num:>8}] send: {format_frame(frame, config.display_limit)}")

        try:
            recv_frame = sock.recv(1024)
            if not recv_frame:
                logger.warning(f"[{request_num:>8}] server closed connection (recv return null)")
                return False
            logger.info(f"[{request_num:>8}] recv: {format_frame(recv_frame, config.display_limit)}")
            return True
        except socket.timeout:
            logger.info(f"[{request_num:>8}] recv: timeout (no response)")
            return True

    except (BrokenPipeError, ConnectionResetError):
        logger.warning(f"[{request_num:>8}] server closed connection")
        return False
    except OSError as e:
        logger.warning(f"[{request_num:>8}] send error: {e}")
        return False


def do_fuzzing(config: FuzzerConfig, logger: logging.Logger) -> None:
    """Main fuzzing loop."""
    request_num = 0
    connection_failures = 0

    logger.info(f"Starting fuzzer against {config.host}:{config.port}")
    logger.info(f"Frame size: {config.min_frame_size}-{config.max_frame_size} bytes")

    try:
        while True:
            # Connect to server
            sock = create_socket(config)
            if not sock:
                connection_failures += 1
                if connection_failures >= config.max_retries:
                    logger.error(
                        f"Failed to connect {config.max_retries} times. "
                        "Server may have crashed or be unreachable."
                    )
                    break
                logger.warning(
                    f"Connection failed ({connection_failures}/{config.max_retries}). "
                    f"Retrying in {config.reconnect_delay}s..."
                )
                time.sleep(config.reconnect_delay)
                continue

            connection_failures = 0  # Reset on successful connection

            # Fuzz on this connection until it closes
            try:
                while True:
                    request_num += 1
                    frame = generate_frame(config)

                    if not send_and_receive(sock, frame, request_num, config, logger):
                        # Server closed connection, then reconnect
                        break

            finally:
                sock.close()

    except KeyboardInterrupt:
        logger.info("Fuzzing stopped by user")
    finally:
        logger.info(f"Fuzzer stopped after {request_num} requests")


if __name__ == "__main__":
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Modbus TCP fuzzer: sends random/malformed frames to a Modbus server")
    parser.add_argument("-H", "--host", default="127.0.0.1", help="Target host (default: 127.0.0.1)")
    parser.add_argument("-p", "--port", type=int, default=5020, help="Target port (default: 5020)")
    parser.add_argument("-m", "--min-size", type=int, default=7, help="Minimum frame size in bytes (default: 7)")
    parser.add_argument("-M", "--max-size", type=int, default=260, help="Maximum frame size in bytes (default: 260)")
    parser.add_argument("-t", "--timeout", type=float, default=0.1, help="Socket timeout in seconds (default: 0.1)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    fuzzer_config = FuzzerConfig(host=args.host, port=args.port, min_frame_size=args.min_size,
                                 max_frame_size=args.max_size, socket_timeout=args.timeout, display_limit=30,)

    logger = setup_logging(verbose=args.verbose)

    try:
        do_fuzzing(fuzzer_config, logger)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
