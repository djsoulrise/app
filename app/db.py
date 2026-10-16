import sqlite3
import threading

from .config import DATA

DB_FILE = DATA / "library.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
 id INTEGER PRIMARY KEY, pkey TEXT UNIQUE, path TEXT, title TEXT, artist TEXT, album TEXT, genre TEXT,
 year INTEGER, bpm REAL, key_open TEXT, camelot TEXT, duration REAL, fmt TEXT, bitrate INTEGER, size INTEGER,
 energy INTEGER, energy_src TEXT, rating INTEGER DEFAULT 0, comment TEXT, import_date TEXT, last_played TEXT,
 playcount INTEGER DEFAULT 0, in_traktor INTEGER DEFAULT 0, on_disk INTEGER DEFAULT 0,
 user_cues INTEGER DEFAULT 0, loops INTEGER DEFAULT 0, grid_markers INTEGER DEFAULT 0, flex_grid INTEGER DEFAULT 0,
 loud_peak REAL, loud_perc REAL, folder TEXT, unsorted INTEGER DEFAULT 0, tags_imported INTEGER DEFAULT 0,
 avg_pos REAL, fp TEXT, search TEXT, title_n TEXT, no_artist INTEGER DEFAULT 0, energy_cue INTEGER);
CREATE INDEX IF NOT EXISTS ix_tracks_bpm ON tracks(bpm);
CREATE TABLE IF NOT EXISTS tag_defs(name TEXT PRIMARY KEY, display TEXT, junk INTEGER DEFAULT 0, ignored INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS track_tags(track_id INTEGER, tag TEXT, PRIMARY KEY(track_id, tag));
CREATE INDEX IF NOT EXISTS ix_tt_tag ON track_tags(tag);
CREATE TABLE IF NOT EXISTS src_playlists(id INTEGER PRIMARY KEY, name TEXT UNIQUE, source TEXT, use_learn INTEGER DEFAULT 1, n INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS src_entries(playlist_id INTEGER, pos INTEGER, track_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_se_pl ON src_entries(playlist_id);
CREATE TABLE IF NOT EXISTS transitions(a INTEGER, b INTEGER, n REAL DEFAULT 0, n2 REAL DEFAULT 0, PRIMARY KEY(a, b));
CREATE INDEX IF NOT EXISTS ix_tr_b ON transitions(b);
CREATE TABLE IF NOT EXISTS notes(a INTEGER, b INTEGER, note TEXT, PRIMARY KEY(a, b));
CREATE TABLE IF NOT EXISTS app_playlists(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, rules TEXT, track_ids TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, name TEXT, date TEXT, active INTEGER DEFAULT 0, requests_open INTEGER DEFAULT 1, venue TEXT);
CREATE TABLE IF NOT EXISTS event_parts(id INTEGER PRIMARY KEY, event_id INTEGER, pos INTEGER, name TEXT, energy INTEGER, minutes INTEGER);
CREATE TABLE IF NOT EXISTS plays(id INTEGER PRIMARY KEY, event_id INTEGER, track_id INTEGER, ts TEXT, source TEXT, rating INTEGER DEFAULT 0, part_id INTEGER);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, event_id INTEGER, track_id INTEGER, free_text TEXT, guest TEXT,
 message TEXT, guest_key TEXT, status TEXT DEFAULT 'novo', votes INTEGER DEFAULT 1, tip TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS votes(request_id INTEGER, guest_key TEXT, PRIMARY KEY(request_id, guest_key));
CREATE TABLE IF NOT EXISTS bans(id INTEGER PRIMARY KEY, event_id INTEGER, kind TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS trend_sources(id INTEGER PRIMARY KEY, name TEXT, url TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS trend_items(id INTEGER PRIMARY KEY, source_id INTEGER, title TEXT, artist TEXT, song TEXT,
 link TEXT, fit REAL, why TEXT, track_id INTEGER, fetched TEXT);
CREATE TABLE IF NOT EXISTS tag_suggestions(track_id INTEGER, tag TEXT, PRIMARY KEY(track_id, tag));
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_FILE, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)
    have = {r["name"] for r in conn().execute("PRAGMA table_info(tracks)")}
    for col in ("style", "style_override"):
        if col not in have:
            conn().execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")
    conn().commit()


def rows(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None
