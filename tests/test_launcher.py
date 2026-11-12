import os
import socket
import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import launcher


class LauncherTests(unittest.TestCase):
    def application(self, selected=None):
        return SimpleNamespace(
            SERVER_PORT=0, app=lambda environ, start_response: [],
            playback_server_identity=lambda: dict(build='test-build', instance='test'),
            selected_transcoder=selected or (lambda: dict(label='test')),
            cleanup_service=SimpleNamespace(close=lambda: None,
                                            trash=SimpleNamespace(close=lambda **kwargs: None)),
            hls_streams=SimpleNamespace(close=lambda: None),
            image_scheduler=SimpleNamespace(close=lambda **kwargs: None),
        )

    def test_exit_unblocks_an_http_worker_waiting_for_a_slow_client(self):
        stop, ready, writing, closed = (threading.Event() for _ in range(4))
        module = self.application()
        def application(environ, start_response):
            start_response('200 OK', [('Content-Type', 'application/octet-stream')])
            def body():
                try:
                    for _ in range(10000):
                        writing.set()
                        yield b'x' * (1024 * 1024)
                finally:
                    closed.set()
            return body()
        module.app = application
        servers = []
        create = launcher.create_http_server
        def bind(*args, **kwargs):
            server = create(*args, **kwargs)
            servers.append(server)
            return server
        with self.assertNoLogs('waitress', level='ERROR'), \
             patch.dict('sys.modules', app=module), \
             patch.object(launcher, 'create_http_server', side_effect=bind):
            worker = threading.Thread(target=launcher.main, kwargs=dict(stop_event=stop, on_ready=ready.set))
            worker.start()
            self.assertTrue(ready.wait(3))
            with socket.create_connection(('127.0.0.1', int(servers[0].effective_port)), timeout=1) as client:
                client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
                self.assertTrue(writing.wait(3))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if any(channel.total_outbufs_len >= channel.adj.outbuf_high_watermark
                           for channel in tuple(servers[0].active_channels.values())):
                        break
                    time.sleep(.01)
                else:
                    self.fail('Fixture did not backpressure the HTTP worker')
                try:
                    stop.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive(), 'Exit did not wake the blocked HTTP worker')
                    self.assertTrue(closed.wait(1), 'Response generator did not close')
                finally:
                    client.close()
                    worker.join(8)

    def test_exit_requested_during_startup_never_binds(self):
        stop = threading.Event()
        def selected():
            stop.set()
            return dict(label='test')
        with patch.dict('sys.modules', app=self.application(selected)), \
             patch.object(launcher, 'create_http_server') as create:
            self.assertEqual(launcher.main(stop_event=stop), 0)
        create.assert_not_called()

    def test_exit_requested_while_binding_closes_listener_without_ready(self):
        stop = threading.Event()
        servers, ready = [], []
        create = launcher.create_http_server
        def bind(*args, **kwargs):
            server = create(*args, **kwargs)
            servers.append(server)
            stop.set()
            return server
        with patch.dict('sys.modules', app=self.application()), \
             patch.object(launcher, 'create_http_server', side_effect=bind):
            self.assertEqual(launcher.main(stop_event=stop, on_ready=lambda: ready.append(True)), 0)
        self.assertFalse(ready)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', int(servers[0].effective_port)))

    def test_desktop_exit_stops_serving_and_releases_port(self):
        stop, ready = threading.Event(), threading.Event()
        servers, results = [], []
        create = launcher.create_http_server
        def bind(*args, **kwargs):
            server = create(*args, **kwargs)
            servers.append(server)
            return server
        with patch.dict('sys.modules', app=self.application()), \
             patch.object(launcher, 'create_http_server', side_effect=bind):
            worker = threading.Thread(target=lambda: results.append(launcher.main(stop_event=stop, on_ready=ready.set)))
            worker.start()
            try:
                self.assertTrue(ready.wait(3))
                with socket.create_connection(('127.0.0.1', int(servers[0].effective_port)), timeout=1):
                    pass
            finally:
                stop.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results, [0])
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', int(servers[0].effective_port)))

    def test_listener_is_bound_before_startup_is_reported(self):
        server = launcher.create_http_server(lambda environ, start_response: [], '127.0.0.1', 0)
        try:
            self.assertGreater(int(server.effective_port), 0)
            with socket.create_connection(('127.0.0.1', int(server.effective_port)), timeout=1):
                pass
        finally:
            server.close()
            server.task_dispatcher.shutdown()

    @unittest.skipUnless(os.name == 'nt', 'Windows exclusive listener')
    def test_windows_refuses_a_second_instance_even_with_reuseaddr(self):
        server = launcher.create_http_server(lambda environ, start_response: [], '127.0.0.1', 0)
        try:
            self.assertEqual(server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
            with socket.socket() as other:
                other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with self.assertRaises(OSError):
                    other.bind(('127.0.0.1', int(server.effective_port)))
            with self.assertRaises(OSError):
                launcher.create_http_server(lambda environ, start_response: [], '127.0.0.1', int(server.effective_port))
        finally:
            server.close()
            server.task_dispatcher.shutdown()
