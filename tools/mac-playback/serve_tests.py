#!/usr/bin/env python3
"""Private, standard-library playback probes; no uploads or directory listing."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import urlsplit


MEDIA = {
    "sample-hevc.mkv": "video/x-matroska",
    "hevc-apple-mp4.mp4": "video/mp4",
    "hevc-lanstream.mp4": "video/mp4",
    "h264-lanstream.mp4": "video/mp4",
    "h264-reference.mp4": "video/mp4",
}
LEGACY_TYPE = 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"'


def make_server(host, port, media_dir):
    media_dir = Path(media_dir).resolve()
    index_path = Path(__file__).with_name("index.html")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_HEAD(self):
            self.serve(head=True)

        def do_GET(self):
            self.serve(head=False)

        def serve(self, head):
            path = urlsplit(self.path).path
            if path == "/":
                data = index_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if not head:
                    self.wfile.write(data)
                return
            parts = path.split("/")
            if len(parts) != 3 or parts[1] not in ("media", "stream", "legacy"):
                self.send_error(404)
                return
            mode, name = parts[1:]
            if name not in MEDIA or (mode != "media" and not name.endswith(".mp4")):
                self.send_error(404)
                return
            target = media_dir / name
            # Fixed filenames must not become symlink escapes into private files.
            if target.is_symlink() or target.resolve().parent != media_dir:
                self.send_error(404)
                return
            try:
                source = target.open("rb")
            except (OSError, ValueError):
                self.send_error(404, "Sample missing")
                return
            with source:
                source.seek(0, 2)
                size = source.tell()
                source.seek(0)
                if mode in ("stream", "legacy"):
                    self.send_response(200)
                    self.send_header("Content-Type", LEGACY_TYPE if mode == "legacy" else MEDIA[name])
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if not head:
                        try:
                            while True:
                                chunk = source.read(64 * 1024)
                                if not chunk:
                                    break
                                self.wfile.write(("%X\r\n" % len(chunk)).encode("ascii"))
                                self.wfile.write(chunk + b"\r\n")
                                self.wfile.flush()
                            self.wfile.write(b"0\r\n\r\n")
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                    return
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                if range_header is not None:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                    valid = match is not None and size > 0
                    if valid:
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), size - 1) if last else size - 1
                            valid = start <= end and start < size
                        elif last and int(last) > 0:
                            start = max(0, size - int(last))
                        else:
                            valid = False
                    if not valid:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if range_header is not None else 200)
                self.send_header("Content-Type", MEDIA[name])
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.send_header("Cache-Control", "no-store")
                if range_header is not None:
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.end_headers()
                if not head:
                    source.seek(start)
                    remaining = end - start + 1
                    try:
                        while remaining > 0:
                            chunk = source.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; use Mac LAN IP for iPhone access")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.host, args.port, Path(__file__).with_name("media"))
    print("Playback probes: http://%s:%d/" % server.server_address, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
