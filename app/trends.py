"""Trends module (online only). Uses RSS/Atom feeds and links added by the user; HTML pages are never scraped."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from . import ai, db
from .config import SETTINGS
from .nowplaying import match_song

SONG_RE = re.compile(r"^\s*(?P<a>[^\-\u2013\u2014]{2,80}?)\s+[-\u2013\u2014]\s+(?P<t>.{2,120})$")


def _feed_items(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title, link = "", ""
        for ch in it:
            ct = ch.tag.split("}")[-1]
            if ct == "title":
                title = (ch.text or "").strip()
            elif ct == "link":
                link = ch.get("href") or (ch.text or "").strip()
        if title:
            items.append({"title": title, "link": link})
    return items


def fetch_source(src: dict) -> int:
    r = httpx.get(src["url"], timeout=20, follow_redirects=True, headers={"User-Agent": "DJ-asistent/1.0"})
    r.raise_for_status()
    items = _feed_items(r.text)[:60]
    songs = []
    if ai.available():
        system = ("Iz naslova sa liste izdvoji SAMO stvarne pesme (izvodjac - naslov) koje se pominju u tekstu. "
                  "Ne izmisljaj. Oceni 0-100 koliko pesma moze da prodje na zurkama za datu publiku. JSON: "
                  '{"songs":[{"i":0,"artist":"","title":"","fit":80,"why":"kratko"}]} gde je i indeks stavke.')
        user = f"Publika: {SETTINGS['audience']}\nStavke:\n" + "\n".join(f"{i}: {it['title']}" for i, it in enumerate(items))
        for s in ai.parse_json(ai.ask(system, user)).get("songs", []):
            if isinstance(s.get("i"), int) and 0 <= s["i"] < len(items) and s.get("artist") and s.get("title"):
                songs.append((items[s["i"]], s["artist"], s["title"], s.get("fit"), s.get("why", "")))
    else:
        for it in items:
            m = SONG_RE.match(it["title"])
            if m:
                songs.append((it, m["a"].strip(), m["t"].strip(), None, "bez AI ocene"))
    c = db.conn()
    n = 0
    for it, artist, title, fit, why in songs:
        if c.execute("SELECT 1 FROM trend_items WHERE source_id=? AND artist=? AND title=?",
                     (src["id"], artist, title)).fetchone():
            continue
        c.execute("INSERT INTO trend_items(source_id, title, artist, song, link, fit, why, track_id, fetched) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (src["id"], title, artist, f"{artist} - {title}", it["link"], fit, why,
                   match_song(f"{artist} - {title}"), datetime.now().isoformat(timespec="seconds")))
        n += 1
    c.commit()
    return n
