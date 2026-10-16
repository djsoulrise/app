"""Read-only Traktor NML parsing. The collection is never modified; a temp copy is read."""
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET

from . import keys
from .names import split_filename


def traktor_path(dir_: str, file: str, volume: str) -> str:
    return f"{volume or ''}{(dir_ or '').replace('/:', chr(92))}{file or ''}"


def playlist_key_to_path(key: str) -> str:
    return (key or "").replace("/:", "\\")


def pkey(path: str) -> str:
    return os.path.normpath(path).lower() if path else ""


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _date(s):
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s or "")
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else None


def _read_copy(path: str) -> ET.Element:
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "copy.nml")
        shutil.copyfile(path, dst)
        return ET.parse(dst).getroot()


def parse_entry(e: ET.Element) -> dict | None:
    loc = e.find("LOCATION")
    if loc is None:
        return None
    path = traktor_path(loc.get("DIR"), loc.get("FILE"), loc.get("VOLUME"))
    info = e.find("INFO") if e.find("INFO") is not None else ET.Element("INFO")
    tempo = e.find("TEMPO")
    loud = e.find("LOUDNESS")
    album = e.find("ALBUM")
    fname = loc.get("FILE") or ""
    artist, title = e.get("ARTIST") or "", e.get("TITLE") or ""
    no_artist = 0
    if not artist:
        fa, ft = split_filename(title, False) if title else split_filename(fname)
        if not fa and title:
            fa, ft = split_filename(fname)
        artist, title = fa, ft or title
        no_artist = 0 if artist else 1

    k = keys.parse_key(info.get("KEY")) or keys.from_musical_value(
        (e.find("MUSICAL_KEY").get("VALUE") if e.find("MUSICAL_KEY") is not None else None))

    cues = e.findall("CUE_V2")
    grid_cues = [c for c in cues if c.get("TYPE") == "4"]
    grid_bpms = {round(float(g.get("BPM")), 2) for c in grid_cues for g in c.findall("GRID") if g.get("BPM")}
    user_cues = loops = 0
    energy = None
    for c in cues:
        t, hot, name = c.get("TYPE"), _int(c.get("HOTCUE"), -1), c.get("NAME") or ""
        m = re.search(r"energy\s*(\d{1,2})", name, re.I)
        if m and 1 <= int(m[1]) <= 10:
            energy = int(m[1])
        if t == "5":
            loops += 1
        elif t != "4" and not (t == "0" and hot == 0):
            user_cues += 1
    comment = info.get("COMMENT") or ""
    if energy is None:
        m = re.search(r"energy\s*(\d{1,2})", comment, re.I)
        if m and 1 <= int(m[1]) <= 10:
            energy = int(m[1])

    ry = re.match(r"(\d{4})", info.get("RELEASE_DATE") or "")
    bpm = _float(tempo.get("BPM")) if tempo is not None else None
    return {
        "path": path, "pkey": pkey(path), "title": title, "artist": artist, "no_artist": no_artist,
        "album": album.get("TITLE") if album is not None else "", "genre": info.get("GENRE") or "",
        "year": int(ry[1]) if ry else None, "bpm": bpm,
        "key_open": k[0] if k else None, "camelot": k[1] if k else None,
        "duration": _float(info.get("PLAYTIME_FLOAT")) or _float(info.get("PLAYTIME")),
        "bitrate": _int(info.get("BITRATE")), "size": _int(info.get("FILESIZE")) * 1024,
        "rating": _int(info.get("RANKING")), "comment": comment,
        "import_date": _date(info.get("IMPORT_DATE")), "last_played": _date(info.get("LAST_PLAYED")),
        "playcount": _int(info.get("PLAYCOUNT")), "user_cues": user_cues, "loops": loops,
        "grid_markers": len(grid_cues), "flex_grid": 1 if len(grid_cues) > 1 else 0,
        "loud_peak": _float_signed(loud, "PEAK_DB"), "loud_perc": _float_signed(loud, "PERCEIVED_DB"),
        "energy_cue": energy, "grid_bpms": len(grid_bpms),
    }


def _float_signed(el, attr):
    if el is None:
        return None
    try:
        return float(el.get(attr))
    except (TypeError, ValueError):
        return None


def _walk_playlists(node: ET.Element, out: list, in_smart=False):
    for sub in node.findall("./SUBNODES/NODE"):
        t = sub.get("TYPE")
        if t == "PLAYLIST":
            keys_ = [playlist_key_to_path(k.get("KEY")) for k in sub.findall("./PLAYLIST/ENTRY/PRIMARYKEY")]
            out.append((sub.get("NAME") or "", keys_))
        elif t == "FOLDER":
            _walk_playlists(sub, out)


def parse_collection(path: str) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    root = _read_copy(path)
    tracks = [d for e in root.findall("./COLLECTION/ENTRY") if (d := parse_entry(e))]
    playlists: list = []
    top = root.find("./PLAYLISTS/NODE")
    if top is not None:
        _walk_playlists(top, playlists)
    return tracks, playlists


def parse_history_file(path: str) -> list[str]:
    """Ordered track paths from a Traktor History file."""
    try:
        root = _read_copy(path)
    except (OSError, ET.ParseError):
        return []
    out = []
    for e in root.iter("ENTRY"):
        pk = e.find("PRIMARYKEY")
        if pk is not None and pk.get("KEY"):
            out.append(playlist_key_to_path(pk.get("KEY")))
    return out
