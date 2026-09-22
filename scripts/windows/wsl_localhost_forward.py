"""Forward Windows 127.0.0.1:PORT to WSL backend (same PORT on WSL IP).

Used when WSL2 localhost forwarding / wslrelay is unavailable after stop_web.
Run detached from run_web_backend_wsl.ps1; logs to repo logs/wsl_port_forward.log
"""
from __future__ import annotations

import logging
import os
import select
import socket
import sys
import threading


def _relay(client: socket.socket, target_host: str, target_port: int) -> None:
    remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    remote.settimeout(300)
    try:
        remote.connect((target_host, target_port))
    except OSError:
        client.close()
        return

    client.settimeout(300)
    sockets = [client, remote]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 300)
            if not readable:
                break
            for sock in readable:
                other = remote if sock is client else client
                data = sock.recv(65536)
                if not data:
                    return
                other.sendall(data)
    finally:
        client.close()
        remote.close()


def main() -> int:
    listen_host = os.environ.get("LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.environ.get("LISTEN_PORT", "8000"))
    target_host = os.environ["TARGET_HOST"]
    target_port = int(os.environ.get("TARGET_PORT", str(listen_port)))

    log_path = os.environ.get("LOG_PATH")
    if log_path:
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port))
    server.listen(128)
    logging.info("forward %s:%s -> %s:%s", listen_host, listen_port, target_host, target_port)

    while True:
        client, _addr = server.accept()
        threading.Thread(
            target=_relay,
            args=(client, target_host, target_port),
            daemon=True,
        ).start()


if __name__ == "__main__":
    if "TARGET_HOST" not in os.environ:
        sys.stderr.write("TARGET_HOST env required\n")
        raise SystemExit(2)
    main()
