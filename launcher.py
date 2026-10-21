from __future__ import annotations

import socket
import sys
import os

from waitress import create_server


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        value = sock.getsockname()[0]
        sock.close()
        return value
    except Exception:
        return "127.0.0.1"


def create_http_server(application, host, port):
    if os.name != 'nt':
        return create_server(application, host=host, port=port, threads=8)
    # Waitress enables SO_REUSEADDR. On Windows that can let a second process
    # share a live port and make localhost/LAN requests hit different instances.
    listener = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, port))
        return create_server(application, sockets=[listener], threads=8)
    except BaseException:
        listener.close()
        raise


def main():
    from app import SERVER_PORT, app, playback_server_identity, selected_transcoder

    status = selected_transcoder()
    try:
        server = create_http_server(app, os.environ.get('LANSTREAM_HOST', '0.0.0.0'), SERVER_PORT)
    except OSError as exc:
        print(f'Unable to listen on port {SERVER_PORT} (OS error {exc.errno}).')
        print('Close the existing LANStream instance before starting this one. No process was stopped.')
        return 1
    identity = playback_server_identity()
    print("")
    print("LANStream")
    print("=========")
    print(f"Local:   http://127.0.0.1:{SERVER_PORT}")
    print(f"Network: http://{local_ip()}:{SERVER_PORT}")
    print(f"Transcoder: {status['label']}")
    print(f"Build: {identity['build'][:12]}   Instance: {identity['instance']}   PID: {os.getpid()}")
    print("")
    print("Leave this window open while LANStream is running.")
    print("Press Ctrl+C to stop.")
    print("")
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        server.task_dispatcher.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
