#!/usr/bin/env python3
"""Run LANStream against only this tool's private media and work directories."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def prepare_launch(tool_dir, repo_dir, host, port, environ):
    tool_dir, repo_dir = Path(tool_dir).resolve(), Path(repo_dir).resolve()
    media = tool_dir / "media"
    if not (repo_dir / "launcher.py").is_file():
        raise ValueError("LANStream launcher.py is missing from %s" % repo_dir)
    if not media.is_dir():
        raise ValueError("Place the private samples in %s first" % media)
    if any(char in str(media) for char in "\r\n="):
        raise ValueError("Media path cannot contain newlines or '='")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise ValueError("%s is missing from PATH; install it before launching" % name)
    work = tool_dir / "work"
    work.mkdir(exist_ok=True)
    config = work / "config.txt"
    config.write_text(
        "port=%d\nuse_master_password=false\nmaster_password=\n"
        "transcoder=software\nautomatic_indexing=false\n%s\n" % (port, media),
        encoding="utf-8",
    )
    child_env = {key: value for key, value in environ.items() if not key.upper().startswith("LANSTREAM_")}
    child_env.update({
        "LANSTREAM_CONFIG_FILE": str(config),
        "LANSTREAM_CACHE_DIR": str(work / "cache"),
        "LANSTREAM_HOST": host,
        "LANSTREAM_PORT": str(port),
        "LANSTREAM_TRANSCODER": "software",
    })
    return child_env


def run_child(repo_dir, child_env):
    child = subprocess.Popen([sys.executable, str(repo_dir / "launcher.py")], cwd=repo_dir, env=child_env)
    try:
        return child.wait()
    except KeyboardInterrupt:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 130


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; use Mac LAN IP for iPhone access")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    tool_dir = Path(__file__).resolve().parent
    repo_dir = Path(__file__).resolve().parents[2]
    try:
        child_env = prepare_launch(tool_dir, repo_dir, args.host, args.port, os.environ)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print("Private LANStream: http://%s:%d/" % (args.host, args.port), flush=True)
    return run_child(repo_dir, child_env)


if __name__ == "__main__":
    sys.exit(main())
