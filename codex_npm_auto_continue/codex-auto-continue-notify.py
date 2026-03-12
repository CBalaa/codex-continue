#!/usr/bin/env python3

import socket
import sys


def main() -> int:
    if len(sys.argv) < 3:
        return 1

    socket_path = sys.argv[1]
    payload = sys.argv[2]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload.encode("utf-8"), socket_path)
    except OSError:
        return 0
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
