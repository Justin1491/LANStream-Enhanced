import os
import socket
import unittest

import launcher


class LauncherTests(unittest.TestCase):
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
