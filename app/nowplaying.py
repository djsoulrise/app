"""Now-playing tracking: manual, Traktor History file watcher and an Icecast/Shoutcast-style Broadcast receiver."""
import asyncio
import difflib
import os
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs, unquote_plus, urlparse

from . import db, nml
from .config import SETTINGS
from .names import core_title, norm

STATE = {"track_id": None, "source": None, "ts": None, "raw": None}
BROADCAST = {"running": False, "port": None, "last_connect": None, "last_song": None, "error": None,
             "last_request": None, "last_match": None, "bytes": 0, "requests": 0}
HISTORY = {"file": None, "last_read": None, "error": None}
_lock = threading.Lock()


def match_song(text: str, threshold: float = 0.78) -> int | None:
    """Match 'Artist - Title' text from Broadcast to a library track."""
    s = core_title("", text)
    if not s:
        return None
    s_tokens = set(s.split())
    best, best_id = 0.0, None
    for r in db.rows("SELECT id, artist, title FROM tracks"):
        for c in (core_title(r["artist"] or "", r["title"] or ""), core_title("", r["title"] or "")):
            if not c:
                continue
            if c == s:
                return r["id"]
            ratio = difflib.SequenceMatcher(None, s, c).ratio()
            c_tokens = set(c.split())
            small = min(len(s_tokens), len(c_tokens))
            if small >= 2 and len(s_tokens & c_tokens) == small:
                ratio = max(ratio, 0.85 * small / max(len(s_tokens), len(c_tokens)) + 0.1)
            if ratio > best:
                best, best_id = ratio, r["id"]
    return best_id if best >= threshold else None


async def _on_song(song: str) -> None:
    BROADCAST.update(last_song=song, last_connect=time.time())
    loop = asyncio.get_running_loop()
    tid = await loop.run_in_executor(None, match_song, song)
    BROADCAST["last_match"] = tid
    if tid:
        await loop.run_in_executor(None, set_current, tid, "broadcast", song)


def set_current(track_id: int, source: str, raw: str | None = None) -> None:
    from .suggest import active_event_id
    with _lock:
        STATE.update(track_id=track_id, source=source, ts=time.time(), raw=raw)
        ev = active_event_id()
        c = db.conn()
        last = c.execute("SELECT track_id FROM plays WHERE event_id IS ? ORDER BY id DESC LIMIT 1", (ev,)).fetchone()
        if ev is not None and (not last or last["track_id"] != track_id):
            c.execute("INSERT INTO plays(event_id, track_id, ts, source) VALUES(?,?,?,?)",
                      (ev, track_id, datetime.now().isoformat(timespec="seconds"), source))
            c.commit()


# ---------- History watcher ----------

def _newest_history() -> str | None:
    hd = SETTINGS.get("history_dir")
    if not hd or not os.path.isdir(hd):
        return None
    files = [os.path.join(hd, f) for f in os.listdir(hd) if f.lower().endswith(".nml")]
    return max(files, key=os.path.getmtime) if files else None


def _history_loop() -> None:
    seen: tuple | None = None
    last_key = None
    while True:
        try:
            f = _newest_history()
            if f:
                st = os.stat(f)
                sig = (f, st.st_mtime, st.st_size)
                if sig != seen:
                    paths = nml.parse_history_file(f)
                    HISTORY.update(file=os.path.basename(f), last_read=time.time(), error=None)
                    if paths:
                        key = nml.pkey(paths[-1])
                        if seen is not None and key != last_key:
                            row = db.one("SELECT id FROM tracks WHERE pkey=?", (key,))
                            if row and STATE["source"] != "broadcast":
                                set_current(row["id"], "history")
                        last_key = key
                    seen = sig
        except Exception as e:
            HISTORY["error"] = str(e)
        time.sleep(3)


# ---------- Broadcast receiver ----------

_OGG_MARKERS = ((b"\x03vorbis", 7), (b"OpusTags", 8))


def _parse_ogg_comments(buf: bytes, start: int) -> str | None:
    """'Artist - Title' from a Vorbis/Opus comment header; raises IndexError/struct.error if incomplete."""
    import struct
    pos = start
    vlen = struct.unpack_from("<I", buf, pos)[0]
    pos += 4 + vlen
    count = struct.unpack_from("<I", buf, pos)[0]
    pos += 4
    tags = {}
    for _ in range(min(count, 50)):
        n = struct.unpack_from("<I", buf, pos)[0]
        if pos + 4 + n > len(buf):
            raise IndexError
        k, _, v = buf[pos + 4:pos + 4 + n].decode("utf-8", "replace").partition("=")
        tags[k.upper()] = v
        pos += 4 + n
    title, artist = tags.get("TITLE", ""), tags.get("ARTIST", "")
    return f"{artist} - {title}" if artist and title else (title or None)


async def _consume_stream(reader: asyncio.StreamReader) -> None:
    """Discard audio, but pick up track names from Ogg comment headers."""
    buf = b""
    loop = asyncio.get_running_loop()
    while True:
        data = await reader.read(65536)
        if not data:
            return
        BROADCAST["bytes"] += len(data)
        buf = (buf + data)[-70000:]
        for marker, skip in _OGG_MARKERS:
            i = buf.find(marker)
            if i == -1:
                continue
            try:
                song = _parse_ogg_comments(buf, i + skip)
            except (IndexError, Exception):
                if len(buf) - i < 30000:
                    continue  # header probably continues in the next chunk
                song = None
            buf = buf[i + skip:]
            if song:
                await _on_song(song)

async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        text = head.decode("latin1")
        first = text.split("\r\n")[0]
        BROADCAST["requests"] += 1
        BROADCAST["last_request"] = text.strip()[:400]
        BROADCAST.setdefault("log", []).append(
            "\n".join(l for l in text.strip().split("\r\n") if not l.lower().startswith("authorization"))[:500])
        del BROADCAST["log"][:-6]
        parts = first.split(" ")
        if len(parts) < 2 or "HTTP" not in first and parts[0] not in ("SOURCE", "PUT"):
            BROADCAST["last_connect"] = time.time()
            writer.write(b"OK2\r\nicy-caps:11\r\n\r\n")  # Shoutcast v1 source handshake
            await writer.drain()
            await _consume_stream(reader)
            return
        method, target = parts[0], parts[1]
        if method in ("SOURCE", "PUT") and "/admin" not in target:
            BROADCAST["last_connect"] = time.time()
            writer.write(b"HTTP/1.0 200 OK\r\nServer: DJ asistent\r\n\r\n")
            await writer.drain()
            await _consume_stream(reader)  # audio payload is discarded
            return
        q = parse_qs(urlparse(target).query)
        if "/admin" not in target and not q:
            body = (b"<html><body style='font-family:sans-serif'><h3>DJ asistent - Broadcast prijemnik radi.</h3>"
                    b"<p>Podesi Traktor: Preferences - Broadcasting - 127.0.0.1, port " + str(BROADCAST["port"]).encode() +
                    b", mount /live.</p></body></html>")
            writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " +
                         str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
            return
        song = (q.get("song") or [""])[0] or unquote_plus((q.get("title") or [""])[0])
        if song:
            await _on_song(song)
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/xml\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


def _broadcast_thread() -> None:
    async def main():
        port = int(SETTINGS["broadcast_port"])
        try:
            server = await asyncio.start_server(_handle, "127.0.0.1", port, limit=1 << 16)
        except OSError as e:
            BROADCAST["error"] = f"Port {port} nije dostupan: {e}"
            return
        BROADCAST.update(running=True, port=port, error=None)
        async with server:
            await server.serve_forever()
    asyncio.run(main())


def start() -> None:
    threading.Thread(target=_history_loop, daemon=True, name="history").start()
    if SETTINGS.get("broadcast_enabled"):
        threading.Thread(target=_broadcast_thread, daemon=True, name="broadcast").start()
