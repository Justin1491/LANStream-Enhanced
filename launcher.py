from __future__ import annotations

import socket
import sys
import os

from waitress import serve

from app import SERVER_PORT, app, selected_transcoder


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        value = sock.getsockname()[0]
        sock.close()
        return value
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    status = selected_transcoder()
    print("")
    print("LANStream")
    print("=========")
    print(f"Local:   http://127.0.0.1:{SERVER_PORT}")
    print(f"Network: http://{local_ip()}:{SERVER_PORT}")
    print(f"Transcoder: {status['label']}")
    print("")
    print("Leave this window open while LANStream is running.")
    print("Press Ctrl+C to stop.")
    print("")
    try:
        serve(app, host=os.environ.get('LANSTREAM_HOST', '0.0.0.0'), port=SERVER_PORT, threads=8)
    except KeyboardInterrupt:
        pass
