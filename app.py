from __future__ import annotations

import json
import atexit
import hashlib
import mimetypes
import os
import shutil
import sys
import socket
import re
import secrets
import subprocess
import threading
import time
import io
import math
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request,
    send_file, session, stream_with_context, url_for, make_response,
)
from PIL import Image, ImageOps
import pymupdf
from werkzeug.exceptions import HTTPException
from hls_streams import HLSStreams
from directory_index import DirectoryIndex, FILE_TYPE_KINDS, root_id, _relative
from image_scheduler import ImageScheduler

def application_dir() -> Path:
    # For a PyInstaller onedir build, editable files belong beside LANStream.exe.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    # PyInstaller stores bundled templates/static under _MEIPASS.
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


APP_DIR = application_dir()
RESOURCE_DIR = resource_dir()
CONFIG_FILE = Path(os.environ.get("LANSTREAM_CONFIG_FILE", str(APP_DIR / "config.txt")))
CACHE_DIR = Path(os.environ.get("LANSTREAM_CACHE_DIR", str(APP_DIR / "cache")))
SESSION_SECRET = os.environ.get("LANSTREAM_SESSION_SECRET") or secrets.token_hex(32)
VAAPI_DEVICE = os.environ.get("LANSTREAM_VAAPI_DEVICE", "/dev/dri/renderD128")
# Serialize cache publication and PyMuPDF work across viewer/thumbnail threads.
VIEWER_RENDER_LOCK = threading.Lock()


def find_media_tool(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    local = APP_DIR / f"{name}{suffix}"
    if local.exists():
        return str(local)
    found = shutil.which(name) or shutil.which(f"{name}{suffix}")
    return found or f"{name}{suffix}"


FFMPEG = find_media_tool("ffmpeg")
FFPROBE = find_media_tool("ffprobe")


def read_config_lines() -> list[str]:
    if not CONFIG_FILE.exists():
        return []
    return CONFIG_FILE.read_text(encoding="utf-8").splitlines()


def load_settings() -> dict[str, str]:
    settings = {
        "port": "8061",
        "use_master_password": "false",
        "master_password": "",
        "transcoder": "auto",
        "directory_cache_ttl": "300",
        "directory_scan_timeout": "30",
        "automatic_indexing": "true",
        "thumbnail_workers": "2",
        "low_bandwidth_resolution": "854x480",
        "low_bandwidth_bitrate": "2M",
    }
    for raw in read_config_lines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("<") or line.startswith("-"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key in settings:
            settings[key] = value.strip()
    return settings


def bool_setting(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


SETTINGS = load_settings()
SERVER_PORT = int(os.environ.get("LANSTREAM_PORT", SETTINGS.get("port", "8061")))
APP_PASSWORD = os.environ.get("LANSTREAM_PASSWORD", SETTINGS.get("master_password", ""))
USE_MASTER_PASSWORD = bool_setting(
    os.environ.get("LANSTREAM_USE_MASTER_PASSWORD", SETTINGS.get("use_master_password", "false"))
)
TRANSCODER_PREFERENCE = os.environ.get(
    "LANSTREAM_TRANSCODER", SETTINGS.get("transcoder", "auto")
).strip().lower()


VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg",
    ".m2ts", ".mts", ".ts", ".wmv", ".flv", ".ogv",
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif",
}
PDF_EXTENSIONS = {".pdf"}
COMIC_EXTENSIONS = {".cbz", ".cbr"}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
COMIC_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS - {".avif"}
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}

app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "templates"),
    static_folder=str(RESOURCE_DIR / "static"),
)
app.secret_key = SESSION_SECRET


class StreamTracker:
    """In-memory live stream metrics. No database or persistent state."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[str, dict[str, Any]] = {}

    def start(
        self,
        mode: str,
        source: Path,
        *,
        start_offset: float = 0.0,
        detail: str = "",
    ) -> str:
        stream_id = secrets.token_hex(5)
        now = time.monotonic()
        with self._lock:
            self._streams[stream_id] = {
                "id": stream_id,
                "mode": mode,
                "filename": source.name,
                "path": str(source),
                "client": request.remote_addr or "unknown",
                "started_monotonic": now,
                "started_epoch": time.time(),
                "last_sample_monotonic": now,
                "last_sample_bytes": 0,
                "bytes_sent": 0,
                "bandwidth_mbps": 0.0,
                "start_offset": float(start_offset),
                "detail": detail,
            }
        return stream_id

    def add_bytes(self, stream_id: str, amount: int) -> None:
        if amount <= 0:
            return
        now = time.monotonic()
        with self._lock:
            item = self._streams.get(stream_id)
            if not item:
                return
            item["bytes_sent"] += amount

            elapsed = now - item["last_sample_monotonic"]
            if elapsed >= 0.5:
                delta = item["bytes_sent"] - item["last_sample_bytes"]
                instant_mbps = (delta * 8.0) / elapsed / 1_000_000
                previous = item["bandwidth_mbps"]
                item["bandwidth_mbps"] = (
                    instant_mbps if previous <= 0 else (previous * 0.65 + instant_mbps * 0.35)
                )
                item["last_sample_monotonic"] = now
                item["last_sample_bytes"] = item["bytes_sent"]

    def stop(self, stream_id: str) -> None:
        with self._lock:
            self._streams.pop(stream_id, None)

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            result = []
            for item in self._streams.values():
                elapsed = max(0.001, now - item["started_monotonic"])
                average_mbps = (item["bytes_sent"] * 8.0) / elapsed / 1_000_000
                current_mbps = item["bandwidth_mbps"] or average_mbps
                result.append({
                    "id": item["id"],
                    "mode": item["mode"],
                    "filename": item["filename"],
                    "path": item["path"],
                    "client": item["client"],
                    "started_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(item["started_epoch"]),
                    ),
                    "elapsed_seconds": round(elapsed, 1),
                    "bytes_sent": item["bytes_sent"],
                    "bandwidth_mbps": round(current_mbps, 2),
                    "average_mbps": round(average_mbps, 2),
                    "start_offset_seconds": round(item["start_offset"], 1),
                    "detail": item["detail"],
                })
            result.sort(key=lambda row: row["started_at"])
            return result


stream_tracker = StreamTracker()
hls_streams = HLSStreams(CACHE_DIR / 'hls', stream_tracker)
atexit.register(hls_streams.close)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


image_scheduler = ImageScheduler(
    lambda *args: generate_image(*args), workers=int(SETTINGS['thumbnail_workers']),
    streaming=lambda: bool(stream_tracker.snapshot()),
)


def profile_id_for_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def config_has_password_sections() -> bool:
    if not CONFIG_FILE.exists():
        return False
    for raw in read_config_lines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("<") and line.endswith(">"):
            return True
    return False


def authentication_required() -> bool:
    if config_has_password_sections():
        return True

    # Explicit switch prevents stale deployment secrets from silently
    # re-enabling authentication.
    return USE_MASTER_PASSWORD and bool(APP_PASSWORD)


def load_profiles() -> list[dict]:
    """
    Parse config.txt.

    New format:
        <PasswordOne>
        /mnt/media/Movies
        /mnt/media/TV
        -/Blocked Show
        -/Blocked Folder/Nested

    A -/ exclusion is relative to the most recently listed positive root.
    A line beginning with - followed by an absolute path excludes that absolute
    subtree.

    Legacy format without <password> sections remains supported and uses the
    LANSTREAM_PASSWORD environment variable.
    """
    if not CONFIG_FILE.exists():
        return []

    lines = read_config_lines()
    has_sections = config_has_password_sections()

    if not has_sections:
        roots = []
        for raw in lines:
            line = raw.strip()
            if (
                line
                and not line.startswith("#")
                and "=" not in line
                and not (line.startswith("<") and line.endswith(">"))
            ):
                roots.append(Path(line).expanduser())
        return [{
            "id": profile_id_for_password(APP_PASSWORD),
            "password": APP_PASSWORD,
            "roots": roots,
            "excluded": [],
        }]

    profiles = []
    current = None
    last_root = None

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("<") and line.endswith(">"):
            password = line[1:-1]
            if not password:
                continue
            current = {
                "id": profile_id_for_password(password),
                "password": password,
                "roots": [],
                "excluded": [],
            }
            profiles.append(current)
            last_root = None
            continue

        if "=" in line and not line.startswith("-"):
            # Global config setting, not a media path.
            continue

        if current is None:
            continue

        if line.startswith("-"):
            value = line[1:].strip()
            if not value:
                continue

            # -/something is relative to the most recently listed positive root.
            if value.startswith("/") and last_root is not None:
                # If the exact value is itself underneath a configured root,
                # treat it as absolute; otherwise treat it as relative.
                candidate = Path(value).expanduser()
                is_absolute_config_path = any(
                    str(candidate).startswith(str(root).rstrip("/") + "/")
                    or candidate == root
                    for root in current["roots"]
                )
                if is_absolute_config_path:
                    excluded = candidate
                else:
                    excluded = last_root / value.lstrip("/")
            else:
                candidate = Path(value).expanduser()
                excluded = candidate if candidate.is_absolute() else (
                    last_root / candidate if last_root is not None else candidate
                )

            current["excluded"].append(excluded)
            continue

        root = Path(line).expanduser()
        current["roots"].append(root)
        last_root = root

    return profiles


def active_profile() -> dict | None:
    profiles = load_profiles()

    # In legacy passwordless mode there is exactly one implicit profile and no
    # login session. Use it directly.
    if not authentication_required():
        return profiles[0] if profiles else None

    profile_id = session.get("profile_id")
    if not profile_id:
        return None

    for profile in profiles:
        if profile["id"] == profile_id:
            return profile
    return None


def load_roots() -> list[Path]:
    profile = active_profile()
    return profile["roots"] if profile else []


def path_is_excluded(path: Path, profile=None) -> bool:
    profile = active_profile() if profile is None else profile
    if not profile:
        return True

    try:
        resolved = path.resolve(strict=False)
    except RuntimeError:
        return True

    for excluded in profile["excluded"]:
        try:
            excluded_resolved = excluded.resolve(strict=False)
            if resolved == excluded_resolved:
                return True
            resolved.relative_to(excluded_resolved)
            return True
        except ValueError:
            continue
        except RuntimeError:
            continue

    return False


def root_label(path: Path, index: int) -> str:
    return path.name.strip() or f"Media {index + 1}"


def get_root(index: int) -> Path:
    roots = load_roots()
    if index < 0 or index >= len(roots):
        abort(404)
    root = roots[index]
    if path_is_excluded(root):
        abort(404)
    return root


def safe_path(root: Path, relative: str, profile=None) -> Path:
    try:
        rr = root.resolve(strict=False)
        p = (root / relative).resolve(strict=False)
        p.relative_to(rr)
    except (ValueError, RuntimeError):
        abort(403)

    if path_is_excluded(p, profile):
        abort(404)

    return p


def media_kind(path: Path) -> str | None:
    if not path.is_file():
        return None
    return kind_for_suffix(path.suffix.lower())


def kind_for_suffix(suffix):
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in COMIC_EXTENSIONS:
        return "comic"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return None


def is_video(path: Path) -> bool:
    return media_kind(path) == "video"


directory_index = DirectoryIndex(
    CACHE_DIR / 'index.sqlite3', kind_for_suffix,
    ttl=max(1, float(SETTINGS['directory_cache_ttl'])),
    scan_timeout=max(1, float(SETTINGS['directory_scan_timeout'])),
    profiles=load_profiles if bool_setting(SETTINGS['automatic_indexing']) else None,
)


def natural_sort_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", value)]


def image_siblings(root: Path, source: Path) -> tuple[str | None, str | None]:
    try:
        items = [
            p for p in source.parent.iterdir()
            if media_kind(p) == "image" and not path_is_excluded(p)
        ]
    except OSError:
        return None, None
    items.sort(key=lambda p: natural_sort_key(p.name))
    try:
        index = next(i for i, item in enumerate(items) if item.resolve(strict=False) == source.resolve(strict=False))
    except (StopIteration, OSError, RuntimeError):
        return None, None
    previous = items[index - 1] if index > 0 else None
    following = items[index + 1] if index + 1 < len(items) else None
    return (
        previous.relative_to(root).as_posix() if previous else None,
        following.relative_to(root).as_posix() if following else None,
    )


def cache_root_key(root: Path) -> str:
    try:
        resolved = root.resolve(strict=False)
    except RuntimeError:
        resolved = root.absolute()

    normalized = os.path.normcase(os.path.normpath(str(resolved)))
    digest = hashlib.sha256(
        normalized.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]
    return f"root_{digest}"


def cache_root_dir(kind: str, root: Path) -> Path:
    return CACHE_DIR / kind / cache_root_key(root)


def cache_path(kind: str, root_index: int, root: Path, source: Path) -> Path:
    rel = source.relative_to(root)
    return cache_root_dir(kind, root) / rel.parent / f"{rel.name}.jpg"


def _viewer_cache_path(kind: str, root_index: int, root: Path, source: Path) -> Path:
    base = cache_path(kind, root_index, root, source)
    stat = source.stat()
    return base.with_name(f"{base.stem}.{stat.st_mtime_ns}-{stat.st_size}.jpg")


def probe(path: Path) -> dict[str, Any]:
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries",
        "format=duration,size,format_name:"
        "stream=index,codec_type,codec_name,width,height,channels:"
        "stream_tags=language,title",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    return json.loads(proc.stdout)


def duration_seconds(info: dict[str, Any]) -> float:
    try:
        return max(0.0, float(info.get("format", {}).get("duration", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def screenshot_candidates(info: dict[str, Any]) -> list[float]:
    """Return useful thumbnail sample times while avoiding intros/credits."""
    d = duration_seconds(info)
    if d <= 2:
        return [0.0]

    candidates = [
        max(2.0, d * 0.10),
        max(2.0, d * 0.20),
        max(2.0, d * 0.35),
        max(2.0, d * 0.50),
        max(2.0, d * 0.70),
        min(30.0, max(2.0, d * 0.05)),
    ]

    result: list[float] = []
    for value in candidates:
        value = min(max(0.0, value), max(0.0, d - 0.5))
        if not any(abs(value - existing) < 0.5 for existing in result):
            result.append(value)
    return result


def _save_pillow_jpeg(image: Image.Image, output: Path, width: int) -> None:
    if image.format == "TIFF":
        image.load()  # TIFF applies EXIF orientation during decoding.
    bounds = (width, max(width * 2, 240))
    if image.getexif().get(274) in {5, 6, 7, 8}:
        bounds = bounds[::-1]
    # Resize while JPEG decoder reduction is still available, before EXIF loads/copies it.
    image.thumbnail(bounds, Image.Resampling.LANCZOS)
    image = ImageOps.exif_transpose(image)
    if image.mode not in {"RGB", "L"}:
        background = Image.new("RGB", image.size, "black")
        if "A" in image.getbands():
            background.paste(image, mask=image.getchannel("A"))
        else:
            background.paste(image)
        image = background
    elif image.mode == "L":
        image = image.convert("RGB")
    image.save(output, "JPEG", quality=84, optimize=True)


def find_7zip() -> str | None:
    candidates: list[str] = []
    if os.name == "nt":
        candidates.extend(["7zz.exe", "7z.exe", "7za.exe"])
    else:
        candidates.extend(["7zz", "7z", "7za"])

    for name in candidates:
        local = APP_DIR / name
        if local.is_file():
            return str(local)

    for name in candidates:
        found = shutil.which(name)
        if found:
            return found

    return None


SEVEN_ZIP = find_7zip()


def _seven_zip_names(source: Path) -> list[str]:
    if not SEVEN_ZIP:
        raise RuntimeError(
            "7-Zip is required for CBR viewing. Install 7zip/7z "
            "or place the 7-Zip executable beside LANStream."
        )

    proc = subprocess.run(
        [SEVEN_ZIP, "l", "-slt", "-ba", str(source)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(
            "7-Zip could not read this CBR"
            + (f": {detail}" if detail else ".")
        )

    names: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("Path = "):
            continue
        name = line[7:].strip()
        if name and Path(name).suffix.lower() in COMIC_IMAGE_EXTENSIONS:
            names.append(name)

    names.sort(key=natural_sort_key)
    return names


def _seven_zip_read(source: Path, member: str) -> bytes:
    if not SEVEN_ZIP:
        raise RuntimeError(
            "7-Zip is required for CBR viewing. Install 7zip/7z "
            "or place the 7-Zip executable beside LANStream."
        )

    proc = subprocess.run(
        [SEVEN_ZIP, "x", "-so", "-y", str(source), member],
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "7-Zip could not extract a CBR page"
            + (f": {detail}" if detail else ".")
        )
    return proc.stdout


@lru_cache(maxsize=128)
def _comic_names_cached(path_text: str, mtime_ns: int, size: int) -> tuple[str, ...]:
    source = Path(path_text)
    names: list[str] = []
    suffix = source.suffix.lower()
    if suffix == ".cbz":
        with zipfile.ZipFile(source) as archive:
            names = [
                name for name in archive.namelist()
                if not name.endswith("/")
                and Path(name).suffix.lower() in COMIC_IMAGE_EXTENSIONS
            ]
    elif suffix == ".cbr":
        names = _seven_zip_names(source)
    names.sort(key=natural_sort_key)
    return tuple(names)


def _comic_names(source: Path) -> list[str]:
    stat = source.stat()
    return list(_comic_names_cached(str(source), stat.st_mtime_ns, stat.st_size))


def _comic_image_bytes(source: Path, page: int) -> bytes:
    names = _comic_names(source)
    if page < 0 or page >= len(names):
        raise IndexError(page)
    name = names[page]
    if source.suffix.lower() == ".cbz":
        with zipfile.ZipFile(source) as archive:
            return archive.read(name)
    if source.suffix.lower() == ".cbr":
        return _seven_zip_read(source, name)
    raise ValueError(f"Unsupported comic archive: {source.suffix}")


def _generate_nonvideo_thumbnail(source: Path, output: Path, width: int) -> bool:
    kind = media_kind(source)
    if kind == "image":
        with Image.open(source) as image:
            _save_pillow_jpeg(image, output, width)
        return True
    if kind == "pdf":
        with VIEWER_RENDER_LOCK, pymupdf.open(source) as document:
            if document.page_count < 1:
                return False
            page = document.load_page(0)
            scale = max(1.0, width / max(1.0, page.rect.width))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            with Image.open(io.BytesIO(pix.tobytes("png"))) as image:
                _save_pillow_jpeg(image, output, width)
        return True
    if kind == "comic":
        data = _comic_image_bytes(source, 0)
        with Image.open(io.BytesIO(data)) as image:
            _save_pillow_jpeg(image, output, width)
        return True
    return False


def generate_image(source: Path, output: Path, width: int, generation: int, scheduler: ImageScheduler):
    if not scheduler.is_current(source, output):
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.jpg")

    try:
        if media_kind(source) != "video":
            if _generate_nonvideo_thumbnail(source, tmp, width):
                if scheduler.is_current(source, output):
                    tmp.replace(output)
            return

        info = probe(source)
        candidates = screenshot_candidates(info)

        for stamp in candidates:
            if not scheduler.is_current(source, output):
                return
            cmd = [
                FFMPEG, "-hide_banner", "-loglevel", "info",
                "-ss", f"{stamp:.3f}", "-i", str(source),
                "-frames:v", "1",
                "-vf", f"blackframe=amount=0:threshold=32,scale={width}:-2:force_original_aspect_ratio=decrease",
                "-q:v", "3", "-y", str(tmp),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=90)
            match = re.search(rb"pblack:(\d+)", proc.stderr or b"")
            if match and int(match.group(1)) >= 85:
                tmp.unlink(missing_ok=True)
                continue
            if (
                proc.returncode == 0
                and tmp.exists()
                and scheduler.is_current(source, output)
            ):
                tmp.replace(output)
                return
            tmp.unlink(missing_ok=True)

        fallback = candidates[0] if candidates else 0.0
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-ss", f"{fallback:.3f}", "-i", str(source),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2:force_original_aspect_ratio=decrease",
            "-q:v", "3", "-y", str(tmp),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=90)
        if (
            proc.returncode == 0
            and tmp.exists()
            and scheduler.is_current(source, output)
        ):
            tmp.replace(output)
    except Exception as exc:
        scheduler.mark_failed(generation, output)
        app.logger.warning("Thumbnail generation failed for %s: %s", source, exc)
    finally:
        tmp.unlink(missing_ok=True)


def fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return str(size)


def fmt_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def stream_lists(info):
    videos, audios, subtitles = [], [], []
    for stream in info.get("streams", []):
        tags = stream.get("tags") or {}
        item = {
            "index": stream.get("index"),
            "codec": stream.get("codec_name") or "unknown",
            "language": tags.get("language") or "und",
            "title": tags.get("title") or "",
            "width": stream.get("width"),
            "height": stream.get("height"),
        }
        kind = stream.get("codec_type")
        if kind == "video":
            videos.append(item)
        elif kind == "audio":
            audios.append(item)
        elif kind == "subtitle":
            item["text_supported"] = item["codec"] in TEXT_SUBTITLE_CODECS
            subtitles.append(item)
    return videos, audios, subtitles


def direct_playback_ok(path: Path, info, audios) -> bool:
    videos, _, _ = stream_lists(info)
    if not videos or len(audios) > 1:
        return False
    v = videos[0]["codec"]
    a = audios[0]["codec"] if audios else None
    ext = path.suffix.lower()
    if ext in {".mp4", ".m4v", ".mov"}:
        return v == "h264" and a in {None, "aac", "mp3"}
    if ext == ".webm":
        return v in {"vp8", "vp9", "av1"} and a in {None, "opus", "vorbis"}
    return False


def parent_relpath(relpath: str) -> str:
    parent = Path(relpath).parent
    return "" if str(parent) == "." else parent.as_posix()


def run_encoder_test(encoder: str, extra_before_input=None, video_filter=None) -> tuple[bool, str]:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if extra_before_input:
        cmd += extra_before_input
    cmd += ["-f", "lavfi", "-i", "color=size=640x360:rate=1", "-frames:v", "1"]
    if video_filter:
        cmd += ["-vf", video_filter]
    cmd += ["-c:v", encoder, "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return proc.returncode == 0, (proc.stderr or "").strip()
    except Exception as exc:
        return False, str(exc)


def available_transcoder_tests() -> list[tuple[str, str, tuple[bool, str]]]:
    tests = []

    if os.name == "nt":
        tests.append(("qsv", "Intel QSV", run_encoder_test("h264_qsv", video_filter="format=nv12")))
        tests.append(("nvenc", "NVIDIA NVENC", run_encoder_test("h264_nvenc", video_filter="format=nv12")))
        tests.append(("amf", "AMD AMF", run_encoder_test("h264_amf", video_filter="format=nv12")))
    else:
        if Path(VAAPI_DEVICE).exists():
            tests.append((
                "vaapi",
                "VAAPI",
                run_encoder_test(
                    "h264_vaapi",
                    extra_before_input=["-vaapi_device", VAAPI_DEVICE],
                    video_filter="format=nv12,hwupload",
                ),
            ))
        tests.append(("nvenc", "NVIDIA NVENC", run_encoder_test("h264_nvenc", video_filter="format=nv12")))

    tests.append(("software", "Software libx264", run_encoder_test("libx264")))
    return tests


_transcoder_cache = None


def selected_transcoder(force_refresh: bool = False) -> dict:
    global _transcoder_cache
    if _transcoder_cache is not None and not force_refresh:
        return _transcoder_cache

    tests = available_transcoder_tests()
    preference = TRANSCODER_PREFERENCE

    if preference != "auto":
        for key, label, result in tests:
            if key == preference:
                ok, detail = result
                _transcoder_cache = {
                    "key": key if ok else None,
                    "label": label if ok else "Unavailable",
                    "ok": ok,
                    "detail": detail,
                    "tests": [(k, l, r[0], r[1]) for k, l, r in tests],
                }
                return _transcoder_cache

    for key, label, (ok, detail) in tests:
        if ok:
            _transcoder_cache = {
                "key": key,
                "label": label,
                "ok": True,
                "detail": detail,
                "tests": [(k, l, r[0], r[1]) for k, l, r in tests],
            }
            return _transcoder_cache

    _transcoder_cache = {
        "key": None,
        "label": "No usable transcoder",
        "ok": False,
        "detail": "\n".join(f"{label}: {result[1]}" for _, label, result in tests),
        "tests": [(k, l, r[0], r[1]) for k, l, r in tests],
    }
    return _transcoder_cache


@app.before_request
def password_gate():
    if request.path.startswith('/api/') and '//' in request.path:
        abort(403)
    if request.endpoint in {"login", "static"}:
        return

    if not authentication_required():
        # Passwordless LAN mode. No session is needed.
        return

    if not session.get("authenticated") or not active_profile():
        if request.path.startswith("/api/"):
            return jsonify(error="Sign in required"), 401
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not authentication_required():
        return redirect(url_for("home"))

    error = None
    if request.method == "POST":
        supplied = request.form.get("password", "")
        matched = None
        for profile in load_profiles():
            if secrets.compare_digest(supplied, profile["password"]):
                matched = profile
                break

        if matched:
            session.clear()
            session["authenticated"] = True
            session["profile_id"] = matched["id"]
            dest = request.args.get("next") or url_for("home")
            if not dest.startswith("/"):
                dest = url_for("home")
            return redirect(dest)

        error = "Incorrect password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def explorer_template_context():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(24)
    favorite_current = False
    if request.endpoint in {'video_detail', 'player'}:
        profile, root, relative, _ = explorer_context(request.view_args['root_index'], request.view_args['relpath'])
        favorite_current = directory_index.favorite_flags(profile['id'], [{'root_id': root_id(root), 'rel': relative}])[0]
    return {'csrf_token': session['csrf_token'], 'favorite_current': favorite_current,
            'automatic_indexing': directory_index.profiles is not None}


@app.after_request
def private_media_cache(response):
    if request.endpoint != 'static':
        response.headers['Cache-Control'] = 'private, no-store'
    return response


def explorer_context(root_index, relative=''):
    # Cached metadata must be authorized without waiting for a network filesystem.
    try:
        relative = _relative(relative)
    except ValueError:
        abort(403)
    relative = '' if relative == '.' else relative
    profile = active_profile()
    if not profile or root_index < 0 or root_index >= len(profile['roots']):
        abort(404)
    root = profile['roots'][root_index]
    base = os.path.normcase(os.path.abspath(root))
    excluded = []
    for entry in profile['excluded']:
        candidate = os.path.normcase(os.path.abspath(entry))
        try:
            common = os.path.commonpath([base, candidate])
        except ValueError:
            continue
        if common == candidate:
            abort(404)
        if common == base:
            item = os.path.relpath(candidate, base).replace(os.sep, '/')
            excluded.append(item)
            if relative.casefold() == item.casefold() or relative.casefold().startswith(item.casefold() + '/'):
                abort(404)
    return profile, root, relative, tuple(excluded)


def explorer_roots():
    profile = active_profile()
    roots, excluded, indexes = [], {}, {}
    for number, root in enumerate(profile['roots'] if profile else []):
        try:
            _, _, _, blocked = explorer_context(number)
        except Exception as exc:
            if getattr(exc, 'code', None) == 404:
                continue
            raise
        roots.append(root)
        excluded[root_id(root)] = blocked
        indexes[root_id(root)] = number
    return profile, roots, excluded, indexes


def explorer_page_args():
    file_type = request.args.get('file_type', 'all')
    if file_type not in FILE_TYPE_KINDS:
        abort(400)
    try:
        page = max(1, min(1000000, int(request.args.get('page', '1'))))
    except ValueError:
        abort(400)
    return dict(page=page, page_size=100, file_type=file_type)


def explorer_items(result):
    profile, roots, excluded, indexes = explorer_roots()
    flags = directory_index.favorite_flags(profile['id'], result['items']) if profile else []
    routes = dict(folder='browse', video='video_detail', image='image_viewer',
                  pdf='document_viewer', comic='document_viewer', text='text_viewer')
    for number, item in enumerate(result['items']):
        index = indexes[item['root_id']]
        root = profile['roots'][index]
        item.update(root_index=index, root_label=root_label(root, index),
                    favorite=flags[number],
                    url=url_for(routes[item['kind']], root_index=index, relpath=item['rel']),
                    folder_url=url_for('browse', root_index=index, relpath=item['parent']),
                    location='/'.join(filter(None, (root_label(root, index), item['parent']))))
        if item['kind'] not in {'folder', 'text'}:
            item['thumb_url'] = url_for('thumbnail', root_index=index, relpath=item['rel'])
            item['ready_url'] = url_for('image_ready', kind='thumbs', root_index=index, relpath=item['rel'])
    return result


def explorer_write_check():
    token = session.get('csrf_token', '')
    if not token or not secrets.compare_digest(request.headers.get('X-LANStream-CSRF', ''), token):
        abort(403)


@app.route('/')
def home():
    profile, roots, excluded, indexes = explorer_roots()
    items = [dict(index=indexes[root_id(root)], label=root_label(root, indexes[root_id(root)])) for root in roots]
    items.sort(key=lambda item: item['label'].casefold())
    return render_template('home.html', roots=items)


@app.route('/browse/<int:root_index>/', defaults={'relpath': ''})
@app.route('/browse/<int:root_index>/<path:relpath>')
def browse(root_index, relpath):
    _, root, relpath, _ = explorer_context(root_index, relpath)
    crumbs = [dict(name=root_label(root, root_index), url=url_for('browse', root_index=root_index))]
    parts = relpath.split('/') if relpath else []
    for index, part in enumerate(parts):
        crumbs.append(dict(name=part, url=url_for('browse', root_index=root_index, relpath='/'.join(parts[:index + 1]))))
    return render_template('browse.html', root_index=root_index, relpath=relpath,
                           crumbs=crumbs, mode='browse',
                           api_url=url_for('browse_api', root_index=root_index, relpath=relpath),
                           up_url=url_for('browse', root_index=root_index, relpath='/'.join(parts[:-1])) if parts else url_for('home'))


@app.route('/api/browse/<int:root_index>/', defaults={'relpath': ''})
@app.route('/api/browse/<int:root_index>/<path:relpath>')
def browse_api(root_index, relpath):
    _, root, relpath, excluded = explorer_context(root_index, relpath)
    result = directory_index.listing(root, relpath, **explorer_page_args(),
        sort=request.args.get('sort', 'name'), descending=request.args.get('direction') == 'desc',
        query=request.args.get('q', '')[:500], excluded=excluded)
    return jsonify(explorer_items(result))


@app.post('/api/refresh/<int:root_index>/', defaults={'relpath': ''})
@app.post('/api/refresh/<int:root_index>/<path:relpath>')
def refresh_api(root_index, relpath):
    explorer_write_check()
    _, root, relpath, excluded = explorer_context(root_index, relpath)
    return jsonify(directory_index.listing(root, relpath, excluded=excluded, refresh=True))


@app.route('/search')
@app.route('/favorites')
def collection_page():
    mode = 'favorites' if request.path == '/favorites' else 'search'
    return render_template('browse.html', mode=mode, crumbs=[], root_index=None,
                           relpath='', api_url=url_for('favorites_api' if mode == 'favorites' else 'search_api'),
                           up_url=url_for('home'))


@app.route('/api/search')
def search_api():
    _, roots, excluded, _ = explorer_roots()
    scope_root, scope_relative, recursive = None, '', True
    scope = request.args.get('scope', 'all')
    if scope not in {'all', 'subtree', 'folder'}:
        abort(400)
    if scope != 'all':
        try:
            index = int(request.args.get('root', '0'))
        except ValueError:
            abort(400)
        _, scope_root, scope_relative, _ = explorer_context(index, request.args.get('relative', ''))
        recursive = scope == 'subtree'
    result = directory_index.search(roots, query=request.args.get('q', '')[:500],
        excluded=excluded, scope_root=scope_root, scope_relative=scope_relative,
        recursive=recursive, **explorer_page_args())
    result['crawls'] = directory_index.crawl_status(active_profile()['id'], roots) if active_profile() else []
    return jsonify(explorer_items(result))


@app.route('/api/favorites', methods=['GET', 'POST'])
def favorites_api():
    profile, roots, excluded, _ = explorer_roots()
    if not profile:
        abort(404)
    if request.method == 'POST':
        explorer_write_check()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or type(data.get('root_index')) is not int or type(data.get('enabled')) is not bool:
            abort(400)
        _, root, relative, _ = explorer_context(data['root_index'], data.get('rel', ''))
        kind = data.get('kind')
        if not relative or kind not in {'folder', 'video'}:
            abort(400)
        if data['enabled']:
            source = safe_path(root, relative)
            if (kind == 'folder' and not source.is_dir()) or (kind == 'video' and not is_video(source)):
                abort(404)
        directory_index.set_favorite(profile['id'], root, relative, kind, Path(relative).name, data['enabled'])
        return jsonify(favorite=data['enabled'])
    return jsonify(explorer_items(directory_index.favorites(profile['id'], roots, excluded=excluded, **explorer_page_args())))


@app.route('/api/index/<int:root_index>', methods=['GET', 'POST'])
def indexing_api(root_index):
    profile, root, relative, excluded = explorer_context(root_index, request.args.get('relative', ''))
    if request.method == 'POST':
        explorer_write_check()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        action = data.get('action')
        if action in {'start', 'resume'}:
            directory_index.crawl_start(profile['id'], root, excluded=excluded)
        elif action == 'pause':
            directory_index.crawl_pause(profile['id'], root, True)
        else:
            abort(400)
    return jsonify(crawls=directory_index.crawl_status(profile['id'], [root]))


def thumbnail_jobs(root_index, profile, root, relative, excluded, stop):
    folder = safe_path(root, relative, profile)
    if not folder.is_dir():
        raise OSError('Thumbnail preparation requires a folder')
    while not stop.is_set():
        listing = directory_index.listing(root, relative, excluded=excluded)
        if listing['error']:
            raise OSError('Folder snapshot unavailable')
        if listing['version'] and not listing['refreshing']:
            break
        if stop.wait(.2):
            return
    for rel in directory_index.thumbnail_sources(root, relative, excluded):
        if stop.is_set():
            return
        try:
            source = safe_path(root, rel, profile)
            if media_kind(source) in {'video', 'image', 'pdf', 'comic'}:
                yield source, cache_path('thumbs', root_index, root, source), 360
        except (HTTPException, OSError):
            continue


@app.route('/api/prepare-thumbnails/<int:root_index>', methods=['GET', 'POST'])
def prepare_thumbnails_api(root_index):
    profile, root, relative, excluded = explorer_context(root_index, request.args.get('relative', ''))
    key = (profile['id'], root_id(root), relative)
    if request.method == 'POST':
        explorer_write_check()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('action') not in {'start', 'stop'}:
            abort(400)
        if data['action'] == 'start':
            if not image_scheduler.start_preparation(key, lambda stop: thumbnail_jobs(
                    root_index, profile, root, relative, excluded, stop)):
                return jsonify(error='A folder is already being prepared. Let it finish before starting another.'), 409
        else:
            image_scheduler.stop_preparation(key)
    return jsonify(preparation=image_scheduler.preparation_status(key))


@app.route("/image/<int:root_index>/<path:relpath>")
def image_viewer(root_index, relpath):
    image_scheduler.new_generation()
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if media_kind(source) != "image":
        abort(404)
    previous_rel, next_rel = image_siblings(root, source)
    return render_template(
        "image_viewer.html",
        root_index=root_index,
        relpath=relpath,
        filename=source.name,
        parent_rel=parent_relpath(relpath),
        previous_rel=previous_rel,
        next_rel=next_rel,
    )


@app.route("/api/image-navigation/<int:root_index>/<path:relpath>")
def image_navigation(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if media_kind(source) != "image":
        abort(404)
    previous_rel, next_rel = image_siblings(root, source)
    return jsonify({
        "relpath": relpath,
        "filename": source.name,
        "image_url": url_for(
            "image_file",
            root_index=root_index,
            relpath=relpath,
        ),
        "viewer_url": url_for(
            "image_viewer",
            root_index=root_index,
            relpath=relpath,
        ),
        "previous": (
            {
                "relpath": previous_rel,
                "viewer_url": url_for(
                    "image_viewer",
                    root_index=root_index,
                    relpath=previous_rel,
                ),
            }
            if previous_rel else None
        ),
        "next": (
            {
                "relpath": next_rel,
                "viewer_url": url_for(
                    "image_viewer",
                    root_index=root_index,
                    relpath=next_rel,
                ),
            }
            if next_rel else None
        ),
    })


@app.route("/image-file/<int:root_index>/<path:relpath>")
def image_file(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if media_kind(source) != "image":
        abort(404)

    # Most configured image formats can be sent directly to modern browsers.
    # TIFF is converted lazily because browser support is inconsistent.
    if source.suffix.lower() in {".tif", ".tiff"}:
        output = _viewer_cache_path("image-view", root_index, root, source)
        with VIEWER_RENDER_LOCK:
            if not output.exists():
                output.parent.mkdir(parents=True, exist_ok=True)
                tmp = output.with_suffix(".tmp.jpg")
                try:
                    with Image.open(source) as image:
                        _save_pillow_jpeg(image, tmp, 2400)
                    tmp.replace(output)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    abort(415)
        return send_file(output, mimetype="image/jpeg", conditional=True, max_age=86400)

    mimetype = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
    return send_file(source, mimetype=mimetype, conditional=True, max_age=3600)


def _page_cache_path(kind: str, root_index: int, root: Path, source: Path, page: int) -> Path:
    base = _viewer_cache_path(kind, root_index, root, source)
    return base.parent / f"{base.stem}.page-{page + 1:05d}.jpg"


def _render_pdf_page(source: Path, page: int, output: Path, width: int = 1600) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.jpg")
    try:
        with pymupdf.open(source) as document:
            if page < 0 or page >= document.page_count:
                raise IndexError(page)
            p = document.load_page(page)
            scale = max(1.0, width / max(1.0, p.rect.width))
            pix = p.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            pix.save(str(tmp))
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)


def _render_comic_page(source: Path, page: int, output: Path, width: int = 1800) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.jpg")
    try:
        data = _comic_image_bytes(source, page)
        with Image.open(io.BytesIO(data)) as image:
            _save_pillow_jpeg(image, tmp, width)
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)


@app.route("/document/<int:root_index>/<path:relpath>")
def document_viewer(root_index, relpath):
    image_scheduler.new_generation()
    root = get_root(root_index)
    source = safe_path(root, relpath)
    kind = media_kind(source)
    if kind not in {"pdf", "comic"}:
        abort(404)
    if source.suffix.lower() == ".cbr" and not SEVEN_ZIP:
        abort(503, description="CBR viewing requires 7-Zip. Install 7-Zip or place its executable beside LANStream.")
    try:
        if kind == "pdf":
            with VIEWER_RENDER_LOCK, pymupdf.open(source) as document:
                page_count = document.page_count
        else:
            page_count = len(_comic_names(source))
    except (zipfile.BadZipFile, pymupdf.FileDataError, RuntimeError, ValueError):
        return render_template("error.html", message="Unable to open this document. It may be damaged or unsupported."), 415
    except Exception:
        app.logger.exception("Document open failed")
        return render_template("error.html", message="Unable to open this document. Please try again."), 500
    if page_count < 1:
        return render_template("error.html", message="This document has no viewable pages."), 415
    return render_template(
        "document_viewer.html",
        root_index=root_index,
        relpath=relpath,
        filename=source.name,
        parent_rel=parent_relpath(relpath),
        page_count=page_count,
        kind=kind,
    )


@app.route("/document-page/<int:root_index>/<path:relpath>")
def document_page(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    kind = media_kind(source)
    if kind not in {"pdf", "comic"}:
        abort(404)
    if source.suffix.lower() == ".cbr" and not SEVEN_ZIP:
        abort(503, description="CBR viewing requires 7-Zip. Install 7-Zip or place its executable beside LANStream.")
    try:
        page = int(request.args.get("page", "0"))
    except ValueError:
        abort(400)
    if page < 0:
        abort(404)
    output = _page_cache_path("document-pages", root_index, root, source, page)
    with VIEWER_RENDER_LOCK:
        if not output.exists():
            try:
                if kind == "pdf":
                    _render_pdf_page(source, page, output)
                else:
                    _render_comic_page(source, page, output)
            except IndexError:
                abort(404)
            except (zipfile.BadZipFile, pymupdf.FileDataError, RuntimeError, ValueError, Image.UnidentifiedImageError):
                abort(415, description="Unable to read this page. The document may be damaged or unsupported.")
            except Exception as exc:
                app.logger.warning("Document page render failed for %s page %s: %s", source, page, exc)
                abort(500)
    return send_file(output, mimetype="image/jpeg", conditional=True, max_age=86400)


@app.route("/text/<int:root_index>/<path:relpath>")
def text_viewer(root_index, relpath):
    image_scheduler.new_generation()
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if media_kind(source) != "text":
        abort(404)
    try:
        raw = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        app.logger.exception("Text file read failed")
        return render_template("error.html", message="Unable to read this text file. Please try again."), 500

    return render_template(
        "text_viewer.html",
        root_index=root_index,
        relpath=relpath,
        filename=source.name,
        parent_rel=parent_relpath(relpath),
        text=raw,
    )


@app.route("/video/<int:root_index>/<path:relpath>")
def video_detail(root_index, relpath):
    generation = image_scheduler.new_generation()
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)

    info = probe(source)
    videos, audios, subtitles = stream_lists(info)
    poster = cache_path("posters", root_index, root, source)
    image_scheduler.submit(generation, 0, source, poster, 960)
    v = videos[0] if videos else {}

    return render_template(
        "video.html",
        root_index=root_index, relpath=relpath, filename=source.name,
        parent_rel=parent_relpath(relpath),
        size=fmt_size(source.stat().st_size),
        duration_text=fmt_duration(duration_seconds(info)),
        width=v.get("width"), height=v.get("height"),
        video_codec=v.get("codec", "unknown"),
        audios=audios, subtitles=subtitles,
        native=direct_playback_ok(source, info, audios),
        poster_ready=poster.exists(), generation=generation,
    )


@app.route("/play/<int:root_index>/<path:relpath>")
def player(root_index, relpath):
    image_scheduler.new_generation()
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)
    info = probe(source)
    videos, audios, subtitles = stream_lists(info)
    d = duration_seconds(info)
    return render_template(
        "player.html", root_index=root_index, relpath=relpath,
        filename=source.name, duration=d, duration_text=fmt_duration(d),
        audios=audios, subtitles=subtitles,
        native=direct_playback_ok(source, info, audios),
        hevc=bool(videos and videos[0]["codec"] == "hevc"),
        hls_token=secrets.token_hex(16),
    )


def parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Return inclusive start/end for a single HTTP byte range."""
    if not range_header or not range_header.startswith("bytes="):
        return None

    value = range_header[6:].strip()
    # Browsers normally request one range. Keep the implementation deliberately
    # small and reject multipart ranges.
    if "," in value:
        return None

    try:
        start_text, end_text = value.split("-", 1)

        if start_text == "":
            # Suffix range, e.g. bytes=-500
            suffix = int(end_text)
            if suffix <= 0:
                return None
            suffix = min(suffix, file_size)
            return file_size - suffix, file_size - 1

        start = int(start_text)
        if start < 0 or start >= file_size:
            return None

        if end_text == "":
            end = file_size - 1
        else:
            end = min(int(end_text), file_size - 1)

        if end < start:
            return None

        return start, end
    except (ValueError, TypeError):
        return None


def ranged_file_response(source: Path, mimetype: str):
    file_size = source.stat().st_size
    requested = request.headers.get("Range")

    # Native playback startup may begin with a normal GET. Stream it in chunks
    # rather than handing the entire file to Flask/Werkzeug.
    if not requested:
        stream_id = stream_tracker.start(
            "direct",
            source,
            detail=f"full file, {file_size} bytes",
        )

        def whole_file():
            try:
                with source.open("rb", buffering=0) as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        stream_tracker.add_bytes(stream_id, len(chunk))
                        yield chunk
            finally:
                stream_tracker.stop(stream_id)

        response = Response(stream_with_context(whole_file()), status=200, mimetype=mimetype)
        response.headers["Content-Length"] = str(file_size)
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "private, max-age=0"
        return response

    byte_range = parse_range_header(requested, file_size)
    if byte_range is None:
        response = Response(status=416)
        response.headers["Content-Range"] = f"bytes */{file_size}"
        response.headers["Accept-Ranges"] = "bytes"
        return response

    start, end = byte_range
    length = end - start + 1

    app.logger.info(
        "Native range request: %s bytes %d-%d/%d",
        source.name, start, end, file_size
    )

    stream_id = stream_tracker.start(
        "direct",
        source,
        detail=f"bytes {start}-{end}/{file_size}",
    )

    def partial_file():
        remaining = length
        try:
            with source.open("rb", buffering=0) as fh:
                # Jump directly to the requested offset, which is important
                # for NAS-backed files.
                fh.seek(start, os.SEEK_SET)
                while remaining > 0:
                    chunk = fh.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    stream_tracker.add_bytes(stream_id, len(chunk))
                    yield chunk
        finally:
            stream_tracker.stop(stream_id)

    response = Response(
        stream_with_context(partial_file()),
        status=206,
        mimetype=mimetype,
        direct_passthrough=True,
    )
    response.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response.headers["Content-Length"] = str(length)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "private, max-age=0"
    return response


@app.route("/media/<int:root_index>/<path:relpath>")
def media_file(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)

    mimetype = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
    return ranged_file_response(source, mimetype)


def best_cached_image(root_index: int, root: Path, source: Path, requested_kind: str) -> Path | None:
    requested = cache_path(requested_kind, root_index, root, source)
    if image_scheduler.cached(source, requested):
        return requested
    if requested_kind == "thumbs":
        poster = cache_path("posters", root_index, root, source)
        if image_scheduler.cached(source, poster):
            return poster
    return None


def folder_cached_preview(root_index: int, folder_rel: str, recursive: bool = False) -> Path | None:
    root = get_root(root_index)
    for kind in ('thumbs', 'posters'):
        cache_base = cache_root_dir(kind, root)
        try:
            for current, directories, names in os.walk(cache_base / Path(folder_rel)):
                if not recursive:
                    directories.clear()
                directories.sort(key=natural_sort_key)
                for name in sorted(names, key=natural_sort_key):
                    if not name.lower().endswith('.jpg'):
                        continue
                    preview = Path(current) / name
                    relative = preview.relative_to(cache_base).with_name(name[:-4]).as_posix()
                    try:
                        source = safe_path(root, relative)
                    except HTTPException:
                        continue
                    if image_scheduler.cached(source, preview):
                        return preview
        except OSError:
            continue
    return None


@app.route(
    "/folder-preview/<int:root_index>/",
    defaults={"relpath": ""},
)
@app.route("/folder-preview/<int:root_index>/<path:relpath>")
def folder_preview(root_index, relpath):
    root = get_root(root_index)
    folder = root if not relpath else safe_path(root, relpath)
    if not folder.is_dir():
        abort(404)

    preview = folder_cached_preview(
        root_index,
        relpath,
        recursive=not bool(relpath),
    )
    if not preview:
        abort(404)

    return send_file(
        preview,
        mimetype="image/jpeg",
        conditional=True,
        max_age=86400,
    )


@app.route("/thumb/<int:root_index>/<path:relpath>")
def thumbnail(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if media_kind(source) not in {"video", "image", "pdf", "comic"}:
        abort(404)
    out = best_cached_image(root_index, root, source, "thumbs")
    if not out:
        abort(404)
    return send_file(out, mimetype="image/jpeg", conditional=True, max_age=86400)


@app.route("/poster/<int:root_index>/<path:relpath>")
def poster(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)
    out = cache_path("posters", root_index, root, source)
    if not image_scheduler.cached(source, out):
        abort(404)
    return send_file(out, mimetype="image/jpeg", conditional=True, max_age=0)


@app.route("/api/image-ready/<kind>/<int:root_index>/<path:relpath>")
def image_ready(kind, root_index, relpath):
    if kind not in {"thumbs", "posters"}:
        abort(404)
    try:
        generation = int(request.args.get("generation", "0"))
    except ValueError:
        abort(400)

    root = get_root(root_index)
    source = safe_path(root, relpath)
    source_kind = media_kind(source)
    if kind == "thumbs":
        if source_kind not in {"video", "image", "pdf", "comic"}:
            abort(404)
        found = best_cached_image(root_index, root, source, "thumbs")
        out = cache_path("thumbs", root_index, root, source)
        width, priority = 360, 10
    else:
        if source_kind != "video":
            abort(404)
        out = cache_path("posters", root_index, root, source)
        found = out if image_scheduler.cached(source, out) else None
        width, priority = 960, 0

    current = generation == image_scheduler.current_generation()
    failed = image_scheduler.failed(generation, out) if current else False
    if not found and current:
        image_scheduler.submit(generation, priority, source, out, width)
        failed = image_scheduler.failed(generation, out)
    return jsonify({
        "ready": bool(found),
        "current": current,
        "failed": failed,
    })


def transcode_command(
    backend: str,
    source: Path,
    audio_index: int | None,
    start: float,
    low: bool = False,
    hls_dir: Path | None = None,
) -> list[str]:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if hls_dir is not None:
        cmd += ["-nostdin", "-readrate", "1"]

    if backend == "hevc-copy":
        # shortcut: copy seeks land on nearby keyframes; use Normal for precise seeking.
        cmd += ["-noaccurate_seek"]
    elif backend == "vaapi":
        cmd += ["-vaapi_device", VAAPI_DEVICE]

    cmd += [
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(source),
        "-map", "0:v:0",
    ]

    if audio_index is not None:
        cmd += ["-map", f"0:{audio_index}"]
    else:
        cmd += ["-an"]

    if backend == "hevc-copy":
        if low:
            raise ValueError("HEVC video copy cannot resize or reduce the video bitrate")
        cmd += ["-c:v", "copy", "-tag:v", "hvc1"]
    elif backend == "vaapi":
        cmd += [
            "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi",
            "-qp", "24",
            "-bf", "0",
            "-g", "48",
        ]
    elif backend == "qsv":
        cmd += [
            "-vf", "format=nv12",
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-global_quality", "24",
            "-look_ahead", "0",
            "-bf", "0",
            "-g", "48",
        ]
    elif backend == "nvenc":
        cmd += [
            "-vf", "format=nv12",
            "-c:v", "h264_nvenc",
            "-preset", "p3",
            "-tune", "ll",
            "-rc", "vbr",
            "-cq", "23",
            "-b:v", "0",
            "-bf", "0",
            "-g", "48",
        ]
    elif backend == "amf":
        cmd += [
            "-vf", "format=nv12",
            "-c:v", "h264_amf",
            "-quality", "speed",
            "-rc", "cqp",
            "-qp_i", "24",
            "-qp_p", "24",
            "-bf", "0",
            "-g", "48",
        ]
    else:
        cmd += [
            "-vf", "format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-tune", "zerolatency",
            "-bf", "0",
            "-g", "48",
        ]

    if low:
        resolution = SETTINGS['low_bandwidth_resolution']
        bitrate = SETTINGS['low_bandwidth_bitrate']
        if not re.fullmatch(r'[1-9][0-9]{1,3}x[1-9][0-9]{1,3}', resolution):
            resolution = '854x480'
        if not re.fullmatch(r'[1-9][0-9]*(?:[kKmM])?', bitrate):
            bitrate = '2M'
        width, height = resolution.split('x')
        scale = f"scale=w='min(iw,{width})':h='min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2"
        position = cmd.index('-vf') + 1
        cmd[position] = scale + ',' + cmd[position]
        if '-b:v' in cmd:
            position = cmd.index('-b:v')
            del cmd[position:position + 2]
        if backend == 'amf':
            cmd[cmd.index('-rc') + 1] = 'vbr_peak'
        cmd += ['-b:v', bitrate, '-maxrate', bitrate, '-bufsize', bitrate]
        for option in ('-global_quality', '-crf', '-cq', '-qp', '-qp_i', '-qp_p'):
            if option in cmd:
                position = cmd.index(option)
                del cmd[position:position + 2]
    if audio_index is not None:
        cmd += ['-c:a', 'aac', '-b:a', '128k' if low else '160k', '-ac', '2']

    if hls_dir is not None:
        if backend != 'hevc-copy':
            cmd += ['-force_key_frames', 'expr:gte(t,n_forced*2)']
        return cmd + [
            '-f', 'hls', '-hls_time', '2', '-hls_list_size', '6',
            '-hls_delete_threshold', '3', '-hls_flags', 'delete_segments+temp_file',
            '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
            '-hls_segment_filename', str(hls_dir / 'segment%06d.m4s'),
            str(hls_dir / 'index.m3u8'),
        ]

    cmd += [
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset",
        "-frag_duration", "500000",
        "-flush_packets", "1",
        "-f", "mp4", "pipe:1",
    ]
    return cmd


@app.route("/hls/<int:root_index>/<path:relpath>/index.m3u8", endpoint="hls_media", defaults={"hls": True})
@app.route("/transcode/<int:root_index>/<path:relpath>")
def transcode(root_index, relpath, hls=False):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)

    if request.args.get("hevc") == "1":
        if request.args.get("low") == "1":
            abort(400, description="Choose H.265 or Low Bandwidth, not both")
        videos, _, _ = stream_lists(probe(source))
        if not videos or videos[0]["codec"] != "hevc":
            abort(400, description="H.265 playback requires an HEVC source")
        transcoder = {"key": "hevc-copy", "label": "H.265 video copy", "ok": True}
    else:
        transcoder = selected_transcoder()
    if not transcoder["ok"]:
        app.logger.error("No usable transcoder: %s", transcoder["detail"])
        return Response(
            "No usable transcoder is available.\n" + transcoder["detail"],
            status=503, mimetype="text/plain"
        )

    try:
        start = float(request.args.get("start", "0"))
    except ValueError:
        start = 0.0

    audio_index = None
    if request.args.get("audio"):
        try:
            audio_index = int(request.args["audio"])
        except ValueError:
            abort(400)

    if hls:
        if not math.isfinite(start):
            abort(400)
        stream_id = request.args.get('stream', '')
        low = request.args.get('low') == '1'
        stream = None
        try:
            stream = hls_streams.get(
                stream_id, (str(source), transcoder['key'], start, audio_index, low),
                lambda directory: transcode_command(transcoder['key'], source, audio_index, start, low, directory),
                source=source, backend=transcoder['key'], start=start,
            )
            playlist = stream.wait_for('index.m3u8').read_text(encoding='utf-8')
            if stream.process.poll() not in (None, 0):
                raise RuntimeError('HLS encoder exited with an error')
        except ValueError as exc:
            abort(400, description=str(exc))
        except (OSError, TimeoutError, RuntimeError) as exc:
            app.logger.error('HLS startup failed: %s', stream.error_detail() if stream else str(exc))
            return Response('Unable to start HLS playback. Check the server log.', status=503, mimetype='text/plain')

        def segment_url(name):
            if not re.fullmatch(r'init\.mp4|segment[0-9]{6}\.m4s', name):
                abort(503)
            return url_for('hls_segment', root_index=root_index, relpath=relpath,
                           stream_id=stream_id, filename=name)

        lines = []
        for line in playlist.splitlines():
            if line.startswith('#EXT-X-MAP:'):
                line = re.sub(r'URI="([^"]+)"', lambda match: 'URI="' + segment_url(match[1]) + '"', line)
            elif line and not line.startswith('#'):
                line = segment_url(line)
            lines.append(line)
        return Response('\n'.join(lines) + '\n', mimetype='application/vnd.apple.mpegurl',
                        headers={'Cache-Control': 'private, no-store'})

    cmd = transcode_command(transcoder["key"], source, audio_index, start, low=request.args.get("low") == "1")
    app.logger.info("Starting %s transcode: %s", transcoder["label"], " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    stream_id = stream_tracker.start(
        transcoder["key"],
        source,
        start_offset=start,
        detail=transcoder["label"],
    )

    def generate():
        cancelled = False
        stderr = b""
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(256 * 1024)
                if not chunk:
                    break
                stream_tracker.add_bytes(stream_id, len(chunk))
                yield chunk
        except GeneratorExit:
            # Normal browser behavior: seeking, changing tracks, navigating
            # away, or closing playback abandons the old HTTP stream.
            cancelled = True
            raise
        except (BrokenPipeError, ConnectionResetError):
            cancelled = True
        finally:
            stream_tracker.stop(stream_id)

            if proc.poll() is None:
                cancelled = True
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass

            if proc.stderr:
                try:
                    stderr = proc.stderr.read()
                except Exception:
                    pass

            # A terminated FFmpeg process is expected when the browser stops
            # consuming a stream. Only report genuine unexpected failures.
            if not cancelled and proc.returncode not in (0, None):
                app.logger.error(
                    "FFmpeg transcode exited %s: %s",
                    proc.returncode, stderr.decode("utf-8", "replace")[-4000:]
                )

    response = Response(stream_with_context(generate()))
    response.headers["Content-Type"] = "video/mp4"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/stream/<int:root_index>/<path:relpath>/video.mp4")
def transcoded_media(root_index, relpath):
    # The URL names the output container, not the original file's format.
    return transcode(root_index, relpath)


@app.route('/hls-segment/<int:root_index>/<stream_id>/<path:relpath>/<filename>')
def hls_segment(root_index, stream_id, relpath, filename):
    source = safe_path(get_root(root_index), relpath)
    if not is_video(source) or not re.fullmatch(r'init\.mp4|segment[0-9]{6}\.m4s', filename):
        abort(404)
    try:
        stream = hls_streams.lookup(stream_id, source)
        path = stream.wait_for(filename)
    except ValueError:
        abort(400)
    except (KeyError, FileNotFoundError):
        abort(404)
    except TimeoutError:
        abort(503)
    response = send_file(path, mimetype='video/mp4', conditional=True)
    response.headers['Cache-Control'] = 'private, no-store'
    body = response.response

    def counted():
        try:
            for chunk in body:
                stream_tracker.add_bytes(stream.tracker_id, len(chunk))
                yield chunk
        finally:
            if hasattr(body, 'close'):
                body.close()

    response.response = counted()
    return response


@app.route("/subtitle/<int:root_index>/<path:relpath>")
def subtitle(root_index, relpath):
    root = get_root(root_index)
    source = safe_path(root, relpath)
    if not is_video(source):
        abort(404)
    try:
        idx = int(request.args["stream"])
        start = float(request.args.get("start", "0"))
    except (KeyError, ValueError):
        abort(400)

    proc = subprocess.run([
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(source), "-map", f"0:{idx}", "-f", "webvtt", "pipe:1"
    ], capture_output=True, timeout=30)
    if proc.returncode:
        abort(415)
    return Response(proc.stdout, mimetype="text/vtt")


def transcoder_status_payload(force_refresh: bool = False) -> dict[str, Any]:
    status = selected_transcoder(force_refresh=force_refresh)
    streams = stream_tracker.snapshot()
    return {
        "transcoder_ok": status["ok"],
        "backend": status["label"],
        "preference": TRANSCODER_PREFERENCE,
        "ffmpeg": FFMPEG,
        "ffprobe": FFPROBE,
        "vaapi_device": VAAPI_DEVICE if os.name != "nt" else None,
        "active_stream_count": len(streams),
        "streams": streams,
        "tests": [
            {"key": key, "label": label, "ok": ok, "detail": detail[-1000:]}
            for key, label, ok, detail in status["tests"]
        ],
    }


@app.route("/transcoder-status")
def transcoder_status_page():
    # Backend tests are cached. The page polls only lightweight in-memory
    # stream metrics unless the user explicitly requests a retest.
    return render_template("status.html")


@app.route("/api/transcoder-status")
def transcoder_status_api():
    refresh = request.args.get("refresh", "").strip().lower() in {"1", "true", "yes"}
    return jsonify(transcoder_status_payload(force_refresh=refresh))


@app.route("/hw-status")
@app.route("/qsv-status")
def legacy_hardware_status():
    return jsonify(transcoder_status_payload(force_refresh=False))


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", message="That item was not found."), 404


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", message="Access outside the configured media roots is not allowed."), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SERVER_PORT, threaded=True)
