import http.client
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from serve_tests import LEGACY_TYPE, MEDIA, make_server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.media = Path(cls.temp.name)
        cls.payload = bytes(range(256)) * 600
        for name in MEDIA:
            (cls.media / name).write_bytes(cls.payload)
        (cls.media / "secret.txt").write_text("private")
        cls.server = make_server("127.0.0.1", 0, cls.media)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_ranges(self):
        size = len(self.payload)
        for spec, expected, content_range in [
            ("bytes=0-1", self.payload[:2], "bytes 0-1/%d" % size),
            ("bytes=-3", self.payload[-3:], "bytes %d-%d/%d" % (size-3, size-1, size)),
            ("bytes=%d-" % (size-2), self.payload[-2:], "bytes %d-%d/%d" % (size-2, size-1, size)),
            ("bytes=0-%d" % (size+20), self.payload, "bytes 0-%d/%d" % (size-1, size)),
        ]:
            with self.subTest(spec=spec):
                status, headers, body = self.request("/media/h264-reference.mp4", headers={"Range": spec})
                self.assertEqual(status, 206)
                self.assertEqual(body, expected)
                self.assertEqual(headers["Content-Range"], content_range)
                self.assertEqual(int(headers["Content-Length"]), len(expected))
                self.assertEqual(headers["Accept-Ranges"], "bytes")

    def test_unsatisfiable_and_malformed_ranges(self):
        for spec in ["bytes=%d-" % len(self.payload), "bytes=8-2", "bytes=-0", "bytes=-", "bytes=0-1,3-4", "items=0-1"]:
            with self.subTest(spec=spec):
                status, headers, body = self.request("/media/h264-reference.mp4", headers={"Range": spec})
                self.assertEqual(status, 416)
                self.assertEqual(headers["Content-Range"], "bytes */%d" % len(self.payload))
                self.assertEqual(headers["Content-Length"], "0")
                self.assertEqual(body, b"")

    def test_head_and_full_file(self):
        status, headers, body = self.request("/media/sample-hevc.mkv")
        self.assertEqual((status, body), (200, self.payload))
        self.assertEqual(headers["Content-Type"], "video/x-matroska")
        status, headers, body = self.request("/media/h264-reference.mp4", "HEAD", {"Range": "bytes=0-1"})
        self.assertEqual((status, body), (206, b""))
        self.assertEqual(headers["Content-Length"], "2")
        self.assertEqual(headers["Content-Range"], "bytes 0-1/%d" % len(self.payload))

    def test_chunked_and_legacy(self):
        for mode, content_type in [("stream", "video/mp4"), ("legacy", LEGACY_TYPE)]:
            with self.subTest(mode=mode):
                status, headers, body = self.request("/%s/hevc-lanstream.mp4" % mode, headers={"Range": "bytes=0-1"})
                self.assertEqual((status, body), (200, self.payload))
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertEqual(headers["Transfer-Encoding"], "chunked")
                self.assertNotIn("Content-Length", headers)
                self.assertNotIn("Content-Range", headers)
                status, headers, body = self.request("/%s/h264-lanstream.mp4" % mode, "HEAD")
                self.assertEqual((status, body), (200, b""))
                self.assertNotIn("Content-Length", headers)

    def test_raw_chunk_framing(self):
        with socket.create_connection(self.server.server_address, timeout=3) as conn:
            conn.sendall(b"GET /stream/h264-lanstream.mp4 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        headers, body = data.split(b"\r\n\r\n", 1)
        self.assertTrue(headers.startswith(b"HTTP/1.1 200"))
        decoded = b""
        while True:
            length, body = body.split(b"\r\n", 1)
            size = int(length, 16)
            if size == 0:
                self.assertEqual(body, b"\r\n")
                break
            decoded += body[:size]
            self.assertEqual(body[size:size+2], b"\r\n")
            body = body[size+2:]
        self.assertEqual(decoded, self.payload)

    def test_allowlist(self):
        for path in ["/serve_tests.py", "/media/secret.txt", "/media/../serve_tests.py", "/media/%2e%2e/secret.txt", "/media/%68%32%36%34-reference.mp4", "/stream/sample-hevc.mkv", "/media/h264-reference.mp4/extra", "/source/config.json", "/media/"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Private playback probes", body)

    def test_empty_file(self):
        target = self.media / "h264-reference.mp4"
        target.write_bytes(b"")
        try:
            self.assertEqual(self.request("/media/h264-reference.mp4")[2], b"")
            self.assertEqual(self.request("/media/h264-reference.mp4", headers={"Range": "bytes=0-1"})[0], 416)
        finally:
            target.write_bytes(self.payload)


if __name__ == "__main__":
    unittest.main()
