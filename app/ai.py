"""Optional AI features (Claude API). The model may only pick tracks by ID from a library shortlist."""
import json
import re

import httpx

from . import db, library
from .config import SETTINGS

API = "https://api.anthropic.com/v1/messages"


class AIError(Exception):
    pass


def available() -> bool:
    return bool(SETTINGS.get("anthropic_key"))


def ask(system: str, user: str, max_tokens: int = 3000) -> str:
    if not available():
        raise AIError("Nije podesen Anthropic API kljuc (Podesavanja).")
    try:
        r = httpx.post(API, timeout=90, headers={
            "x-api-key": SETTINGS["anthropic_key"], "anthropic-version": "2023-06-01",
            "content-type": "application/json"},
            json={"model": SETTINGS["model"], "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]})
    except httpx.HTTPError as e:
        raise AIError(f"Nema veze sa AI servisom: {e}") from e
    if r.status_code != 200:
        raise AIError(f"AI servis je vratio gresku {r.status_code}: {r.text[:200]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def parse_json(text: str):
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    if not m:
        raise AIError("AI odgovor nije u ocekivanom formatu.")
    try:
        return json.loads(m[0])
    except ValueError as e:
        raise AIError("AI odgovor nije ispravan JSON.") from e


def _line(t: dict, tags: list[str]) -> str:
    return (f"{t['id']}|{t['artist']} - {t['title']}|{round(t['bpm'] or 0)}|{t['camelot'] or ''}|E{t['energy'] or '?'}|"
            f"{t['year'] or ''}|{t['genre'] or ''}|{','.join(tags)}")


def situation(text: str, count: int = 15) -> list[dict]:
    tm = library.tag_map()
    rows = db.rows("SELECT * FROM tracks WHERE in_traktor=1 OR on_disk=1 ORDER BY playcount DESC, rating DESC LIMIT 700")
    byid = {r["id"]: r for r in rows}
    lib = "\n".join(_line(r, sorted(tm.get(r["id"], set()))[:6]) for r in rows)
    aud = SETTINGS["audience"]
    system = ("Ti si asistent profesionalnom DJ-u za svadbe, rodjendane i zurke. Birajes ISKLJUCIVO pesme iz priloznog "
              "spiska, po ID-u. Nikad ne izmisljaj pesme ni ID-ove. Odgovaraj samo JSON-om: "
              '{"tracks":[{"id":123,"why":"kratko obrazlozenje na srpskom"}]} u redosledu u kom treba da se puste.')
    user = (f"Situacija: {text}\nProfil publike: {json.dumps(aud, ensure_ascii=False)}\nBroj pesama: {count}\n"
            f"Format spiska: id|izvodjac - naslov|BPM|Camelot|energija|godina|zanr|tagovi\n{lib}")
    data = parse_json(ask(system, user))
    out = []
    for item in data.get("tracks", []):
        t = byid.get(item.get("id"))
        if t:
            out.append(dict(t, why=item.get("why", "")))
    library.attach_tags(out)
    return out


def suggest_tags(limit: int = 40) -> int:
    c = db.conn()
    vocab = [r["display"] for r in db.rows(
        "SELECT d.display, COUNT(*) n FROM tag_defs d JOIN track_tags t ON t.tag=d.name WHERE d.junk=0 AND d.ignored=0 "
        "GROUP BY d.name ORDER BY n DESC LIMIT 60")]
    rows = db.rows("SELECT * FROM tracks t WHERE NOT EXISTS(SELECT 1 FROM track_tags x WHERE x.track_id=t.id) "
                   "AND NOT EXISTS(SELECT 1 FROM tag_suggestions s WHERE s.track_id=t.id) "
                   "ORDER BY playcount DESC, import_date DESC LIMIT ?", (limit,))
    if not rows:
        return 0
    system = ("Predlazes tagove za pesme DJ biblioteke. Koristi iskljucivo tagove iz recnika. "
              'Odgovori samo JSON-om: {"items":[{"id":1,"tags":["folk","kola"]}]}. Ako nisi siguran, ostavi prazno.')
    user = ("Recnik tagova: " + ", ".join(vocab) + "\nPesme (id|izvodjac - naslov|BPM|Camelot|energija|godina|zanr):\n" +
            "\n".join(_line(r, []).rsplit("|", 1)[0] for r in rows))
    data = parse_json(ask(system, user, 2000))
    allowed = {library.norm_tag(v): v for v in vocab}
    ids = {r["id"] for r in rows}
    n = 0
    for item in data.get("items", []):
        if item.get("id") not in ids:
            continue
        for tg in item.get("tags", []):
            if library.norm_tag(tg) in allowed:
                c.execute("INSERT OR IGNORE INTO tag_suggestions VALUES(?,?)", (item["id"], library.norm_tag(tg)))
                n += 1
    c.commit()
    return n
