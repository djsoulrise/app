"""Library import (NML + folder scan), tags, energy estimate and learning from playlists."""
import os
import re
import json
from collections import defaultdict

import mutagen

from . import db, nml, styles
from .config import SETTINGS
from .keys import parse_key
from .names import norm, song_key, split_filename

AUDIO_EXT = {".mp3", ".flac", ".wav", ".m4a", ".aiff", ".aif", ".ogg", ".wma"}
JUNK_RE = re.compile(r"www\.|\.com|\.net|\.org|http|release by|compiled by|ripped|^by\s|download|djpool|promo", re.I)
LOW_WORDS = {"start", "lagano", "sunset", "after", "chill", "ulazak", "vecera"}
HIGH_WORDS = {"party", "kraj", "kola", "trubaci", "eurodance", "saxo", "hits"}

NML_COLS = ["title", "artist", "no_artist", "album", "genre", "year", "bpm", "key_open", "camelot", "duration",
            "bitrate", "size", "rating", "comment", "import_date", "last_played", "playcount", "user_cues",
            "loops", "grid_markers", "flex_grid", "loud_peak", "loud_perc", "energy_cue"]


def _fmt(path: str) -> str:
    return os.path.splitext(path)[1].lstrip(".").lower()


def _is_unsorted(path: str) -> int:
    p = path.lower()
    if "soulseek" in p or "\\downloads" in p:
        return 1
    hint = SETTINGS.get("main_folder_hint", "").lower()
    if hint and hint in p:
        return 0
    return 0 if any(p.startswith(os.path.normpath(f).lower()) for f in SETTINGS["music_folders"]) else 1


def _search_fields(artist: str, title: str) -> tuple[str, str]:
    return song_key(artist, title), norm(title)


def import_nml(progress=None) -> dict:
    path = SETTINGS["nml_path"]
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("Putanja do collection.nml nije podesena ili fajl ne postoji.")
    tracks, playlists = nml.parse_collection(path)
    c = db.conn()
    keep = {"bpm", "key_open", "camelot", "duration", "year"}
    sets = ", ".join(f"{k}=COALESCE(excluded.{k}, tracks.{k})" if k in keep else f"{k}=excluded.{k}"
                     for k in NML_COLS)
    cols = ["pkey", "path", "fmt", "folder", "unsorted", "search", "title_n", "in_traktor", "on_disk"] + NML_COLS
    sql = (f"INSERT INTO tracks({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) "
           f"ON CONFLICT(pkey) DO UPDATE SET path=excluded.path, in_traktor=1, search=excluded.search, "
           f"title_n=excluded.title_n, unsorted=excluded.unsorted, on_disk=MAX(tracks.on_disk, excluded.on_disk), {sets}")
    for i, d in enumerate(tracks):
        s, t = _search_fields(d["artist"], d["title"])
        row = [d["pkey"], d["path"], _fmt(d["path"]), os.path.dirname(d["path"]), _is_unsorted(d["path"]),
               s, t, 1, 1 if os.path.exists(d["path"]) else 0] + [d.get(k) for k in NML_COLS]
        c.execute(sql, row)
        if progress and i % 200 == 0:
            progress(i, len(tracks), "Citam Traktor kolekciju")
    c.commit()
    _import_comment_tags()
    n_pl = import_playlists(playlists, "traktor")
    n_hist = import_history_files()
    rebuild_learning()
    recompute_energy()
    return {"tracks": len(tracks), "playlists": n_pl, "history_files": n_hist}


def _read_audio(path: str) -> dict:
    out = {"duration": None, "bitrate": 0, "title": "", "artist": "", "album": "", "genre": "", "year": None,
           "bpm": None, "key": None}
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            f = mutagen.File(path)
        if f is None:
            return out
        out["duration"] = getattr(f.info, "length", None)
        out["bitrate"] = int(getattr(f.info, "bitrate", 0) or 0)
        tags = f.tags or {}

        def g(k):
            try:
                v = tags.get(k)
                return str(v[0]) if isinstance(v, list) and v else (str(v) if v else "")
            except Exception:
                return ""
        out.update(title=g("title"), artist=g("artist"), album=g("album"), genre=g("genre"), key=g("initialkey"))
        m = re.match(r"(\d{4})", g("date"))
        out["year"] = int(m[1]) if m else None
        try:
            out["bpm"] = float(g("bpm")) or None
        except ValueError:
            pass
    except Exception:
        pass
    return out


def scan_folders(progress=None) -> dict:
    files = []
    for folder in SETTINGS["music_folders"]:
        for root, _, names in os.walk(folder):
            files += [os.path.join(root, n) for n in names if os.path.splitext(n)[1].lower() in AUDIO_EXT]
    c = db.conn()
    existing = {r["pkey"]: r["id"] for r in c.execute("SELECT pkey, id FROM tracks")}
    added = 0
    for i, f in enumerate(files):
        pk = nml.pkey(f)
        a = _read_audio(f)
        size = os.path.getsize(f)
        if pk in existing:
            c.execute("""UPDATE tracks SET on_disk=1, size=?, duration=COALESCE(duration, ?),
                         bitrate=CASE WHEN bitrate IS NULL OR bitrate=0 THEN ? ELSE bitrate END,
                         bpm=COALESCE(NULLIF(bpm,0), ?), genre=COALESCE(NULLIF(genre,''), ?),
                         year=COALESCE(year, ?), album=COALESCE(NULLIF(album,''), ?) WHERE id=?""",
                      (size, a["duration"], a["bitrate"], a["bpm"], a["genre"], a["year"], a["album"], existing[pk]))
            if a["key"]:
                k = parse_key(a["key"])
                if k:
                    c.execute("UPDATE tracks SET key_open=?, camelot=? WHERE id=? AND key_open IS NULL",
                              (k[0], k[1], existing[pk]))
        else:
            fa, ft = split_filename(os.path.basename(f))
            artist, title = a["artist"] or fa, a["title"] or ft
            k = parse_key(a["key"]) or (None, None)
            s, t = _search_fields(artist, title)
            c.execute("""INSERT INTO tracks(pkey, path, title, artist, no_artist, album, genre, year, bpm, key_open, camelot,
                         duration, fmt, bitrate, size, folder, unsorted, search, title_n, on_disk, in_traktor)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)""",
                      (pk, f, title, artist, 0 if artist else 1, a["album"], a["genre"], a["year"], a["bpm"],
                       k[0], k[1], a["duration"], _fmt(f), a["bitrate"], size, os.path.dirname(f),
                       _is_unsorted(f), s, t))
            added += 1
        if progress and i % 100 == 0:
            progress(i, len(files), "Skeniram muzicke foldere")
    c.commit()
    recompute_energy()
    return {"files": len(files), "added": added}


# ---------- tags ----------

def parse_comment_tags(comment: str) -> list[str]:
    out = []
    for tok in re.split(r"[,;|]", comment or ""):
        tok = re.sub(r"^\d{1,2}[ABab]\s*-\s*", "", tok.strip()).strip()
        if not tok or re.fullmatch(r"\d{1,2}[ABab]", tok) or re.fullmatch(r"energy\s*\d+", tok, re.I):
            continue
        out.append(tok)
    return out


def norm_tag(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def ensure_tag(c, display: str) -> str:
    name = norm_tag(display)
    c.execute("INSERT OR IGNORE INTO tag_defs(name, display, junk) VALUES(?,?,?)",
              (name, display.strip(), 1 if JUNK_RE.search(name) else 0))
    return name


def _import_comment_tags() -> None:
    c = db.conn()
    for r in c.execute("SELECT id, comment FROM tracks WHERE tags_imported=0").fetchall():
        for tok in parse_comment_tags(r["comment"]):
            name = ensure_tag(c, tok)
            c.execute("INSERT OR IGNORE INTO track_tags VALUES(?,?)", (r["id"], name))
        c.execute("UPDATE tracks SET tags_imported=1 WHERE id=?", (r["id"],))
    c.commit()


def tag_map(only_useful=True) -> dict[int, set[str]]:
    sql = ("SELECT tt.track_id, tt.tag FROM track_tags tt JOIN tag_defs d ON d.name=tt.tag" +
           (" WHERE d.junk=0 AND d.ignored=0" if only_useful else ""))
    m = defaultdict(set)
    for r in db.conn().execute(sql):
        m[r["track_id"]].add(r["tag"])
    return m


# ---------- playlists & learning ----------

def import_playlists(playlists, source: str) -> int:
    c = db.conn()
    idmap = {r["pkey"]: r["id"] for r in c.execute("SELECT pkey, id FROM tracks")}
    n = 0
    for name, paths in playlists:
        ids = [idmap[nml.pkey(p)] for p in paths if nml.pkey(p) in idmap]
        if not ids:
            continue
        c.execute("INSERT OR IGNORE INTO src_playlists(name, source, use_learn) VALUES(?,?,?)",
                  (name, source, 1 if len(ids) >= 8 and not name.startswith("_") else 0))
        pid = c.execute("SELECT id FROM src_playlists WHERE name=?", (name,)).fetchone()["id"]
        c.execute("DELETE FROM src_entries WHERE playlist_id=?", (pid,))
        c.executemany("INSERT INTO src_entries VALUES(?,?,?)", [(pid, i, t) for i, t in enumerate(ids)])
        c.execute("UPDATE src_playlists SET n=? WHERE id=?", (len(ids), pid))
        n += 1
    c.commit()
    return n


def import_history_files() -> int:
    hd = SETTINGS.get("history_dir")
    if not hd or not os.path.isdir(hd):
        return 0
    pls = []
    for fn in sorted(os.listdir(hd)):
        if fn.lower().endswith(".nml"):
            pls.append((f"history:{fn}", nml.parse_history_file(os.path.join(hd, fn))))
    return import_playlists(pls, "history")


def rebuild_learning() -> None:
    """Transition counts (A->B direct and A->C skip-one) and average position in evening."""
    c = db.conn()
    seqs = []
    for p in c.execute("SELECT id FROM src_playlists WHERE use_learn=1").fetchall():
        seqs.append([r["track_id"] for r in c.execute(
            "SELECT track_id FROM src_entries WHERE playlist_id=? ORDER BY pos", (p["id"],))])
    by_event = defaultdict(list)
    for r in c.execute("SELECT event_id, track_id FROM plays ORDER BY ts, id"):
        by_event[r["event_id"]].append(r["track_id"])
    seqs += [s for s in by_event.values() if len(s) >= 2]

    n1, n2, pos = defaultdict(float), defaultdict(float), defaultdict(list)
    for s in seqs:
        for i, a in enumerate(s):
            if len(s) >= 8:
                pos[a].append(i / (len(s) - 1))
            if i + 1 < len(s) and s[i + 1] != a:
                n1[(a, s[i + 1])] += 1
            if i + 2 < len(s) and s[i + 2] != a:
                n2[(a, s[i + 2])] += 1
    c.execute("DELETE FROM transitions")
    for k in set(n1) | set(n2):
        c.execute("INSERT INTO transitions VALUES(?,?,?,?)", (k[0], k[1], n1.get(k, 0), n2.get(k, 0)))
    c.execute("UPDATE tracks SET avg_pos=NULL")
    c.executemany("UPDATE tracks SET avg_pos=? WHERE id=?", [(sum(v) / len(v), k) for k, v in pos.items()])
    c.commit()


# ---------- energy ----------

def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def estimate_energy(t: dict, tags: set[str]) -> int:
    bpm = t["bpm"] or 0
    if bpm:
        while bpm < 70:
            bpm *= 2
        while bpm > 180:
            bpm /= 2
    bpm_n = _clamp((bpm - 70) / 80) if bpm else 0.5
    loud_n = _clamp((t["loud_perc"] + 8) / 10) if t["loud_perc"] is not None else 0.5
    words = {w for tg in tags for w in tg.split()}
    shift = (0.08 if words & HIGH_WORDS else 0) - (0.08 if words & LOW_WORDS else 0)
    if t["avg_pos"] is not None:
        score = 0.25 * bpm_n + 0.2 * loud_n + 0.55 * t["avg_pos"] + shift
    else:
        score = 0.55 * bpm_n + 0.45 * loud_n + shift
    return int(_clamp(round(1 + 9 * score), 1, 10))


def recompute_energy() -> None:
    c = db.conn()
    tm = tag_map()
    rows = c.execute("SELECT id, bpm, loud_perc, avg_pos, energy_cue, energy_src FROM tracks "
                     "WHERE energy_src IS NULL OR energy_src != 'ručno'").fetchall()
    upd = []
    for r in rows:
        if r["energy_cue"]:
            upd.append((r["energy_cue"], "cue", r["id"]))
        else:
            upd.append((estimate_energy(dict(r), tm.get(r["id"], set())), "procena", r["id"]))
    c.executemany("UPDATE tracks SET energy=?, energy_src=? WHERE id=?", upd)
    c.commit()
    recompute_styles()


def recompute_styles() -> None:
    c = db.conn()
    tm = tag_map()
    upd = []
    for r in c.execute("SELECT id, path, genre, title, style_override FROM tracks").fetchall():
        v = styles.style_vector(r["path"], r["genre"], tm.get(r["id"], set()), r["title"], r["style_override"])
        upd.append((json.dumps({k: round(x, 3) for k, x in v.items()}), r["id"]))
    c.executemany("UPDATE tracks SET style=? WHERE id=?", upd)
    c.commit()


# ---------- queries ----------

SORTS = {"artist": "artist COLLATE NOCASE", "title": "title COLLATE NOCASE", "bpm": "bpm", "key": "camelot",
         "energy": "energy", "year": "year", "import": "import_date", "played": "last_played",
         "rating": "rating", "playcount": "playcount"}

VIEWS = {
    "flex_grid": "t.flex_grid=1",
    "bez_bpm": "(t.bpm IS NULL OR t.bpm=0)",
    "bez_key": "t.key_open IS NULL",
    "bez_taga": "NOT EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id)",
    "nesortirano": "t.unsorted=1",
    "bez_izvodjaca": "t.no_artist=1",
    "zasticene": "(t.user_cues>0 OR t.loops>0 OR t.rating>0)",
    "nedostaje": "(t.on_disk=0 AND t.in_traktor=1)",
}


def query_tracks(p: dict) -> dict:
    where, args = ["1=1"], []
    if p.get("q"):
        for w in norm(p["q"]).split():
            where.append("(t.search LIKE ? OR LOWER(t.genre) LIKE ?)")
            args += [f"%{w}%", f"%{w}%"]
    if p.get("view") in VIEWS:
        where.append(VIEWS[p["view"]])
    for tag in [norm_tag(x) for x in (p.get("tag") or "").split(",") if x.strip()]:
        where.append("EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id AND x.tag=?)")
        args.append(tag)
    for col, key in (("bpm", "bpm"), ("energy", "energy"), ("year", "year")):
        if p.get(f"{key}_min") not in (None, ""):
            where.append(f"t.{col}>=?")
            args.append(float(p[f"{key}_min"]))
        if p.get(f"{key}_max") not in (None, ""):
            where.append(f"t.{col}<=?")
            args.append(float(p[f"{key}_max"]))
    if p.get("key"):
        where.append("t.camelot=?")
        args.append(p["key"].upper())
    if p.get("fmt"):
        where.append("t.fmt=?")
        args.append(p["fmt"].lower())
    cond = " AND ".join(where)
    order = SORTS.get(p.get("sort"), "artist COLLATE NOCASE")
    direction = "DESC" if p.get("dir") == "desc" else "ASC"
    size = min(int(p.get("size") or 100), 500)
    page = max(int(p.get("page") or 1), 1)
    total = db.one(f"SELECT COUNT(*) n FROM tracks t WHERE {cond}", args)["n"]
    items = db.rows(f"SELECT t.* FROM tracks t WHERE {cond} ORDER BY {order} {direction}, t.id LIMIT ? OFFSET ?",
                    args + [size, (page - 1) * size])
    attach_tags(items)
    return {"total": total, "page": page, "size": size, "items": items}


def attach_tags(items: list[dict]) -> None:
    if not items:
        return
    ids = [i["id"] for i in items]
    m = defaultdict(list)
    for r in db.conn().execute(
            f"SELECT tt.track_id, d.display, d.junk FROM track_tags tt JOIN tag_defs d ON d.name=tt.tag "
            f"WHERE tt.track_id IN ({','.join('?' * len(ids))})", ids):
        m[r["track_id"]].append({"t": r["display"], "junk": r["junk"]})
    for i in items:
        i["tags"] = m.get(i["id"], [])


def get_track(tid: int) -> dict | None:
    t = db.one("SELECT * FROM tracks WHERE id=?", (tid,))
    if t:
        attach_tags([t])
    return t


def set_tags(ids: list[int], add: list[str], remove: list[str]) -> None:
    c = db.conn()
    for tid in ids:
        for a in add:
            if a.strip():
                c.execute("INSERT OR IGNORE INTO track_tags VALUES(?,?)", (tid, ensure_tag(c, a)))
        for r in remove:
            c.execute("DELETE FROM track_tags WHERE track_id=? AND tag=?", (tid, norm_tag(r)))
    c.commit()
    recompute_energy()
