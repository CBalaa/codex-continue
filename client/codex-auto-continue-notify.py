#!/usr/bin/env python3

import socket
import sys


def send_udp(host: str, port: int, payload: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload.encode("utf-8"), (host, port))
    except OSError:
        return 0
    finally:
        sock.close()

    return 0


def send_unix(socket_path: str, payload: str) -> int:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload.encode("utf-8"), socket_path)
    except OSError:
        return 0
    finally:
        sock.close()

    return 0


def main() -> int:
    if len(sys.argv) == 3:
        return send_unix(sys.argv[1], sys.argv[2])

    if len(sys.argv) >= 4:
        try:
            port = int(sys.argv[2])
        except ValueError:
            return 1
        return send_udp(sys.argv[1], port, sys.argv[3])

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
