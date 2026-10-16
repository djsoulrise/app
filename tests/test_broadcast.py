import asyncio
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import db, nowplaying  # noqa: E402
from app.config import SETTINGS  # noqa: E402
from tests.test_core import lib  # noqa: E402,F401  (fixture)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_traktor_like_source_and_metadata(lib, monkeypatch):
    port = _free_port()
    monkeypatch.setitem(SETTINGS, "broadcast_port", port)
    threading.Thread(target=nowplaying._broadcast_thread, daemon=True).start()
    for _ in range(50):
        if nowplaying.BROADCAST["running"]:
            break
        time.sleep(0.1)
    assert nowplaying.BROADCAST["running"]

    src = socket.create_connection(("127.0.0.1", port))
    src.sendall(b"SOURCE /live HTTP/1.0\r\nAuthorization: Basic c291cmNlOnB3\r\nContent-Type: audio/mpeg\r\n\r\n")
    assert src.recv(100).startswith(b"HTTP/1.0 200")
    src.sendall(b"\xff\xfb\x90\x00" * 100)

    meta = socket.create_connection(("127.0.0.1", port))
    meta.sendall(b"GET /admin/metadata?mode=updinfo&mount=/live&song=Artist+-+Song+C HTTP/1.0\r\n\r\n")
    assert meta.recv(100).startswith(b"HTTP/1.0 200")
    for _ in range(50):
        if nowplaying.STATE["track_id"]:
            break
        time.sleep(0.1)
    src.close()
    meta.close()
    assert nowplaying.STATE["track_id"] == db.one("SELECT id FROM tracks WHERE title='Song C'")["id"]
    assert nowplaying.BROADCAST["last_song"] == "Artist - Song C"
    assert nowplaying.STATE["source"] == "broadcast"
