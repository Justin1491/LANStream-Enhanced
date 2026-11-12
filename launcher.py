from __future__ import annotations

import socket
import sys
import os
import atexit
from contextlib import ExitStack

from waitress import create_server
from waitress import wasyncore


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        value = sock.getsockname()[0]
        sock.close()
        return value
    except Exception:
        return "127.0.0.1"


def create_http_server(application, host, port, *, socket_map=None):
    if os.name != 'nt':
        return create_server(application, host=host, port=port, threads=8, map=socket_map)
    # Waitress enables SO_REUSEADDR. On Windows that can let a second process
    # share a live port and make localhost/LAN requests hit different instances.
    listener = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, port))
        return create_server(application, sockets=[listener], threads=8, map=socket_map)
    except BaseException:
        listener.close()
        raise


def main(stop_event=None, on_ready=None):
    import app as application

    server = None
    socket_map = {}
    try:
        if stop_event is not None and stop_event.is_set():
            return 0
        status = application.selected_transcoder()
        if stop_event is not None and stop_event.is_set():
            return 0
        port = application.SERVER_PORT
        try:
            server = create_http_server(application.app, os.environ.get('LANSTREAM_HOST', '0.0.0.0'),
                                        port, socket_map=socket_map)
        except OSError as exc:
            print(f'Unable to listen on port {port} (OS error {exc.errno}).')
            print('Close the existing LANStream instance before starting this one. No process was stopped.')
            return 1
        if stop_event is not None and stop_event.is_set():
            return 0
        identity = application.playback_server_identity()
        print("")
        print("LANStream")
        print("=========")
        print(f"Local:   http://127.0.0.1:{port}")
        print(f"Network: http://{local_ip()}:{port}")
        print(f"Transcoder: {status['label']}")
        print(f"Build: {identity['build'][:12]}   Instance: {identity['instance']}   PID: {os.getpid()}")
        print("")
        if stop_event is None:
            print("Leave this window open while LANStream is running.")
            print("Press Ctrl+C to stop.")
        else:
            print("Minimize or close this window to hide it to the system tray.")
            print("Use the tray menu's Exit LANStream to stop.")
        print("")
        if on_ready is not None:
            on_ready()
        if stop_event is None:
            server.run()
        else:
            # One owned socket map lets Exit stop idle and active connections.
            while socket_map and not stop_event.is_set():
                wasyncore.loop(timeout=0.1, count=1, map=socket_map)
    except KeyboardInterrupt:
        pass
    finally:
        # Every cleanup still runs if an earlier one raises. Desktop Exit keeps
        # the status window until owned requests and durable work have finished.
        with ExitStack() as closing:
            if stop_event is not None:
                closing.callback(application.cleanup_service.trash.close, wait=True)
                closing.callback(application.cleanup_service.close)
                closing.callback(application.image_scheduler.close, wait=True)
            if server is not None:
                closing.callback(server.task_dispatcher.shutdown,
                                 timeout=float('inf') if stop_event is not None else 5)
            if stop_event is not None:
                closing.callback(application.hls_streams.close)
                closing.callback(application.image_scheduler.close)
            closing.callback(wasyncore.close_all, socket_map)
            if server is not None:
                closing.callback(server.close)
                # handle_close also wakes workers waiting for output space.
                for channel in tuple(getattr(server, 'active_channels', {}).values()):
                    channel.handle_close()
        if stop_event is not None:
            for cleanup in (application.hls_streams.close, application.cleanup_service.close):
                atexit.unregister(cleanup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
