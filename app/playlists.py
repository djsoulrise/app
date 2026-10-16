"""App-side playlists (rule based or static) and M3U export. Traktor's collection is never touched."""
import json
import os
import re

from . import db, library
from .config import SETTINGS
from .names import norm

BUILTIN = [{"id": 0, "name": "Najnovije", "kind": "rule",
            "rules": {"sort": "import", "dir": "desc", "limit": 100, "exclude_unsorted": False}}]


def rule_tracks(rules: dict) -> list[dict]:
    where, args = ["1=1"], []
    for tag in rules.get("tags_all", []):
        where.append("EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id AND x.tag=?)")
        args.append(library.norm_tag(tag))
    any_ = [library.norm_tag(x) for x in rules.get("tags_any", []) if x.strip()]
    if any_:
        where.append(f"EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id AND x.tag IN ({','.join('?' * len(any_))}))")
        args += any_
    for tag in rules.get("tags_none", []):
        where.append("NOT EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id AND x.tag=?)")
        args.append(library.norm_tag(tag))
    for key, col, op in (("bpm_min", "bpm", ">="), ("bpm_max", "bpm", "<="), ("energy_min", "energy", ">="),
                         ("energy_max", "energy", "<="), ("year_min", "year", ">="), ("year_max", "year", "<=")):
        if rules.get(key) not in (None, ""):
            where.append(f"t.{col}{op}?")
            args.append(float(rules[key]))
    if rules.get("import_days"):
        where.append("t.import_date >= date('now', ?)")
        args.append(f"-{int(rules['import_days'])} day")
    if rules.get("genre"):
        where.append("LOWER(t.genre) LIKE ?")
        args.append(f"%{norm(rules['genre'])}%")
    if rules.get("camelot"):
        where.append("t.camelot=?")
        args.append(rules["camelot"].upper())
    if rules.get("fmt"):
        where.append("t.fmt=?")
        args.append(rules["fmt"].lower())
    if rules.get("flex_grid"):
        where.append("t.flex_grid=1")
    if rules.get("exclude_unsorted"):
        where.append("t.unsorted=0")
    order = library.SORTS.get(rules.get("sort"), "artist COLLATE NOCASE")
    direction = "DESC" if rules.get("dir") == "desc" else "ASC"
    limit = int(rules.get("limit") or 500)
    items = db.rows(f"SELECT t.* FROM tracks t WHERE {' AND '.join(where)} ORDER BY {order} {direction} LIMIT ?",
                    args + [limit])
    library.attach_tags(items)
    return items


def playlist_tracks(pl: dict) -> list[dict]:
    if pl["kind"] == "static":
        ids = json.loads(pl["track_ids"] or "[]")
        if not ids:
            return []
        by_id = {t["id"]: t for t in db.rows(f"SELECT * FROM tracks WHERE id IN ({','.join('?' * len(ids))})", ids)}
        items = [by_id[i] for i in ids if i in by_id]
        library.attach_tags(items)
        return items
    return rule_tracks(json.loads(pl["rules"]) if isinstance(pl["rules"], str) else pl["rules"])


def export_m3u(name: str, tracks: list[dict]) -> str:
    folder = SETTINGS.get("export_dir")
    if not folder:
        raise ValueError("Folder za izvoz nije podesen.")
    os.makedirs(folder, exist_ok=True)
    fn = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "plejlista"
    path = os.path.join(folder, fn + ".m3u8")
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            f.write(f"#EXTINF:{int(t['duration'] or 0)},{t['artist']} - {t['title']}\n{t['path']}\n")
    return path
