"""Duplicate detection. Candidates by duration + name; confirmed by Chromaprint fingerprint when fpcalc exists.
Nothing is deleted: files are only moved to a 'Duplikati' folder after explicit confirmation."""
import difflib
import os
import shutil
import subprocess

from . import db
from .config import SETTINGS
from .names import core_title, version_signature

FMT_RANK = {"flac": 0, "wav": 1, "aiff": 1, "aif": 1, "m4a": 2, "mp3": 3}


def _fpcalc() -> str | None:
    return shutil.which("fpcalc")


def _fingerprint(path: str) -> list[int] | None:
    try:
        r = subprocess.run([_fpcalc(), "-raw", "-length", "90", path], capture_output=True, text=True, timeout=60)
        for line in r.stdout.splitlines():
            if line.startswith("FINGERPRINT="):
                return [int(x) for x in line[12:].split(",") if x]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def fp_similarity(a: list[int], b: list[int]) -> float:
    best = 0.0
    for off in range(-8, 9):
        x, y = (a[off:], b) if off >= 0 else (a, b[-off:])
        n = min(len(x), len(y))
        if n < 20:
            continue
        diff = sum(((p ^ q) & 0xFFFFFFFF).bit_count() for p, q in zip(x[:n], y[:n]))
        best = max(best, 1 - diff / (32 * n))
    return best


def protected(t: dict) -> bool:
    return bool(t["user_cues"] or t["loops"] or t["rating"])


def _recommend(group: list[dict]) -> dict:
    ranked = sorted(group, key=lambda t: (FMT_RANK.get(t["fmt"], 9), -(t["bitrate"] or 0), t["id"]))
    keep = ranked[0]
    prot = [t for t in group if protected(t)]
    conflict = bool(prot) and keep["id"] not in {t["id"] for t in prot}
    if conflict:
        msg = ("Sukob: preporuceni FLAC/najbolji fajl nema cue tacke, loop-ove ni ocenu iz Traktora, a drugi fajl ima. "
               "Cue tacke ne mogu da se prenesu bez izmene Traktor kolekcije, pa odluci sam.")
    else:
        msg = "Preporuka: ostavi " + keep["fmt"].upper() + " verziju."
    return {"keep_id": keep["id"], "conflict": conflict, "message": msg}


def find_duplicates(progress=None) -> list[dict]:
    tracks = db.rows("SELECT * FROM tracks WHERE duration IS NOT NULL AND duration>0 ORDER BY duration")
    for t in tracks:
        t["core"] = core_title(t["artist"] or "", t["title"] or "")
        t["sig"] = version_signature(t["title"] or "")
    parent = {t["id"]: t["id"] for t in tracks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    use_fp = _fpcalc() is not None
    fps: dict[int, list[int] | None] = {}
    by_id = {t["id"]: t for t in tracks}
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            if b["duration"] - a["duration"] > 2.5:
                break
            if a["path"] == b["path"]:
                continue
            sim = difflib.SequenceMatcher(None, a["core"], b["core"]).ratio() if a["core"] and b["core"] else 0
            if sim < 0.6:
                continue
            if a["sig"] != b["sig"]:
                continue
            ok = sim >= 0.9
            if use_fp and a["on_disk"] and b["on_disk"]:
                for t in (a, b):
                    if t["id"] not in fps:
                        fps[t["id"]] = _fingerprint(t["path"])
                if fps[a["id"]] and fps[b["id"]]:
                    ok = fp_similarity(fps[a["id"]], fps[b["id"]]) >= 0.9
            if ok:
                parent[find(a["id"])] = find(b["id"])
        if progress and i % 200 == 0:
            progress(i, len(tracks), "Trazim duplikate")
    groups: dict[int, list[dict]] = {}
    for t in tracks:
        groups.setdefault(find(t["id"]), []).append(t)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        rec = _recommend(g)
        files = [{k: t[k] for k in ("id", "path", "fmt", "bitrate", "duration", "size", "user_cues", "loops", "rating", "in_traktor", "on_disk")}
                 | {"protected": protected(t)} for t in g]
        out.append({"title": (f"{g[0]['artist']} - " if g[0]["artist"] else "") + (g[0]["title"] or ""), "files": files, **rec,
                    "method": "audio otisak + trajanje" if use_fp else "trajanje + metapodaci (fpcalc nije instaliran)"})
    return sorted(out, key=lambda x: x["title"].lower())


def move_to_dupes(ids: list[int]) -> list[dict]:
    dest = SETTINGS.get("dupes_dir")
    if not dest:
        raise ValueError("Folder za duplikate nije podesen.")
    os.makedirs(dest, exist_ok=True)
    result = []
    for tid in ids:
        t = db.one("SELECT id, path FROM tracks WHERE id=?", (tid,))
        if not t or not os.path.isfile(t["path"]):
            result.append({"id": tid, "ok": False, "msg": "fajl ne postoji"})
            continue
        target = os.path.join(dest, os.path.basename(t["path"]))
        base, ext = os.path.splitext(target)
        k = 1
        while os.path.exists(target):
            target, k = f"{base} ({k}){ext}", k + 1
        shutil.move(t["path"], target)
        db.conn().execute("UPDATE tracks SET on_disk=0 WHERE id=?", (tid,))
        result.append({"id": tid, "ok": True, "to": target})
    db.conn().commit()
    return result
