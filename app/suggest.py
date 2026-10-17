"""Next-track suggestions.

Pipeline: hard gates (style family, BPM, key, energy) -> scoring -> history-first picks.
History from the DJ's own playlists only counts among tracks that pass the gates; a history transition that
clashes in style is ignored."""
import json

from . import db, library, styles
from .config import SETTINGS
from .keys import key_relation, key_score
from .names import norm, core_title

MODE_TARGET = {"ravno": 0.0, "podizanje": 1.5, "spustanje": -1.5}
HISTORY_SLOTS = 3
GATES = {
    "strogo": {"style": 0.5, "bpm": 6.0, "key": 0.45, "energy": 2.5, "need_bpm": True},
    "normalno": {"style": 0.3, "bpm": 10.0, "key": 0.2, "energy": 3.5, "need_bpm": False},
    "slobodno": {"style": 0.0, "bpm": 25.0, "key": 0.0, "energy": 9.0, "need_bpm": False},
}


def bpm_score(a, b) -> tuple[float, float | None]:
    if not a or not b:
        return 0.4, None
    best = min(((b * m) / a - 1 for m in (1, 2, 0.5)), key=abs)
    d = abs(best)
    return (1.0 if d <= 0.02 else max(0.0, 1 - (d - 0.02) / 0.10)), best * 100


def _jaccard_min(a: set, b: set) -> float:
    if not a or not b:
        return 0.3
    return len(a & b) / min(len(a), len(b))


def active_event_id() -> int | None:
    e = db.one("SELECT id FROM events WHERE active=1 ORDER BY id DESC LIMIT 1")
    return e["id"] if e else None


def banned(event_id: int | None) -> tuple[set[int], set[str], list[str]]:
    ids, genres, texts = set(), set(), []
    if event_id is None:
        return ids, genres, texts
    for r in db.rows("SELECT kind, value FROM bans WHERE event_id=?", (event_id,)):
        if r["kind"] == "track":
            ids.add(int(r["value"]))
        elif r["kind"] == "genre":
            genres.add(norm(r["value"]))
        else:
            texts.append(norm(r["value"]))
    return ids, genres, texts


def is_banned(t: dict, ban) -> bool:
    ids, genres, texts = ban
    if t["id"] in ids or (genres and norm(t.get("genre") or "") in genres):
        return True
    return any(x and x in (t.get("search") or "") for x in texts)


def _vec(t: dict) -> dict[str, float]:
    try:
        return json.loads(t.get("style") or "{}")
    except ValueError:
        return {}


def _passes(c: dict, g: dict) -> bool:
    if c["style_c"] is not None and c["style_c"] < g["style"]:
        return False
    if c["bpm_pct"] is None:
        if g["need_bpm"]:
            return False
    elif abs(c["bpm_pct"]) > g["bpm"]:
        return False
    if c["ks"] < g["key"]:
        return False
    return abs(c["de"] - c["target"]) <= g["energy"]


def suggest(current_id: int, mode: str | None = None, n: int = 5, surprise: bool = True,
            strict: str | None = None) -> dict:
    cur = db.one("SELECT * FROM tracks WHERE id=?", (current_id,))
    if not cur:
        return {"current": None, "items": []}
    w = SETTINGS["weights"]
    mode = mode or SETTINGS.get("mode", "ravno")
    strict = strict if strict in GATES else SETTINGS.get("style_strictness", "normalno")
    gate = dict(GATES[strict])
    target = MODE_TARGET.get(mode, 0.0)
    ev = active_event_id()
    played = {r["track_id"] for r in db.rows("SELECT track_id FROM plays WHERE event_id=?", (ev,))} if ev else set()
    ban = banned(ev)

    out_tr = {r["b"]: r for r in db.rows("SELECT b, n, n2 FROM transitions WHERE a=?", (current_id,))}
    in_tr = {r["a"]: r["n"] for r in db.rows("SELECT a, n FROM transitions WHERE b=?", (current_id,))}
    notes = {r["b"]: r["note"] for r in db.rows("SELECT b, note FROM notes WHERE a=?", (current_id,))}
    fb = {r["track_id"]: r["s"] for r in db.rows("SELECT track_id, SUM(rating) s FROM plays GROUP BY track_id")}
    tm = library.tag_map()
    ctags = tm.get(current_id, set())
    cvec = _vec(cur)
    scanned = db.one("SELECT COUNT(*) n FROM tracks WHERE on_disk=1")["n"] > 0

    cands, blocked_hist = [], []
    for t in db.rows("SELECT * FROM tracks WHERE id!=?", (current_id,)):
        if t["id"] in played or is_banned(t, ban) or (scanned and not t["on_disk"] and not t["in_traktor"]):
            continue
        o = out_tr.get(t["id"])
        hist_raw = (o["n"] + 0.5 * o["n2"] if o else 0) + 0.3 * in_tr.get(t["id"], 0)
        bs, bpm_pct = bpm_score(cur["bpm"], t["bpm"])
        de = (t["energy"] or 5) - (cur["energy"] or 5)
        tags = tm.get(t["id"], set())
        vec = _vec(t)
        sc = styles.compat(cvec, vec)
        pos = (1 - min(1.0, abs(t["avg_pos"] - cur["avg_pos"]) / 0.3)
               if t["avg_pos"] is not None and cur["avg_pos"] is not None else 0.4)
        c = {
            "t": t, "o": o, "hist": min(1.0, hist_raw / 1.5), "bs": bs, "bpm_pct": bpm_pct,
            "ks": key_score(cur["camelot"], t["camelot"]), "de": de, "target": target,
            "es": max(0.0, 1 - abs(de - target) / 4),
            "vibe": 0.6 * _jaccard_min(ctags, tags) + 0.4 * pos, "common": ctags & tags,
            "ys": (1 - min(1.0, abs(t["year"] - cur["year"]) / 20)) if t["year"] and cur["year"] else 0.4,
            "gs": ((1.0 if norm(t["genre"] or "") == norm(cur["genre"] or "") else 0.0) if t["genre"] and cur["genre"] else 0.4),
            "fbs": max(-1.0, min(1.0, 0.5 * fb.get(t["id"], 0))), "rev": in_tr.get(t["id"], 0),
            "style_c": sc, "vec": vec,
        }
        if o and o["n"] and sc is not None and sc < gate["style"]:
            blocked_hist.append((o["n"], t))
        cands.append(c)

    relaxed = []
    ok = [c for c in cands if _passes(c, gate)]
    if len(ok) < n and strict != "slobodno":
        for step in ("key", "bpm"):
            gate[step] = 0.0 if step == "key" else gate["bpm"] + 4
            gate["need_bpm"] = False
            ok = [c for c in cands if _passes(c, gate)]
            relaxed.append("key" if step == "key" else "BPM")
            if len(ok) >= n:
                break

    den = sum(w.get(k, 0) for k in ("hist", "key", "bpm", "vibe", "energy", "year", "genre", "style"))
    for c in ok:
        style_s = c["style_c"] if c["style_c"] is not None else 0.5
        total = (w["hist"] * c["hist"] + w["key"] * c["ks"] + w["bpm"] * c["bs"] + w["vibe"] * c["vibe"]
                 + w["energy"] * c["es"] + w["year"] * c["ys"] + w["genre"] * c["gs"]
                 + w.get("style", 2.0) * style_s + w["feedback"] * c["fbs"])
        c["score"] = total / den * 100

    def safe(c):
        same_style = c["style_c"] is None or c["style_c"] >= 0.7
        return same_style and (c["hist"] > 0 or (c["ks"] >= 0.9 and c["bs"] >= 0.8))

    ok.sort(key=lambda c: -c["score"])
    picks, per_artist = [], {}
    identities = {core_title(cur["artist"] or "", cur["title"] or "")}
    if played:
        identities.update(core_title(t["artist"] or "", t["title"] or "")
                          for t in db.rows("SELECT artist,title,id FROM tracks") if t["id"] in played)

    def take(c):
        identity = core_title(c["t"]["artist"] or "", c["t"]["title"] or "") or str(c["t"]["id"])
        if identity in identities:
            return False
        a = norm(c["t"]["artist"] or c["t"]["title"])
        if per_artist.get(a, 0) >= 2:
            return False
        per_artist[a] = per_artist.get(a, 0) + 1
        picks.append(c)
        identities.add(identity)
        return True

    def picked(c):
        return any(p is c for p in picks)
    for c in sorted([x for x in ok if x["o"] and x["o"]["n"] > 0], key=lambda x: (-x["o"]["n"], -x["score"]))[:HISTORY_SLOTS]:
        take(c)
    for c in ok:
        if len(picks) >= (n - 1 if surprise else n):
            break
        if not picked(c):
            take(c)
    if surprise:
        for c in ok[:80]:
            if not picked(c) and not safe(c) and c["ks"] >= 0.45 and c["bs"] >= 0.5 and c["hist"] == 0:
                if take(c):
                    break
    for c in ok:
        if len(picks) >= n:
            break
        if not picked(c):
            take(c)
    picks.sort(key=lambda c: -c["score"])

    items = []
    for c in picks:
        t, o = c["t"], c["o"]
        why = []
        if o and o["n"]:
            why.append(f"u tvojim listama ide odmah posle ove pesme ({int(o['n'])}x)")
        elif o and o["n2"]:
            why.append(f"u tvojim listama je u istom bloku, preskace jednu ({int(o['n2'])}x)")
        elif c["rev"]:
            why.append(f"u listama je isla pre ove pesme ({int(c['rev'])}x)")
        if c["style_c"] is not None:
            why.append(f"stil: {styles.describe(c['vec'])} (slaganje {round(c['style_c'] * 100)}%)")
        why.append(key_relation(cur["camelot"], t["camelot"]))
        if c["bpm_pct"] is not None:
            why.append(f"BPM {c['bpm_pct']:+.1f}%")
        why.append(f"energija {cur['energy'] or '?'} -> {t['energy'] or '?'}")
        if c["common"]:
            why.append("zajednicki tagovi: " + ", ".join(sorted(c["common"])[:4]))
        if c["fbs"] > 0.2:
            why.append("ranije 'palo' na zurkama")
        elif c["fbs"] < -0.2:
            why.append("ranije 'nije palo'")
        if t["flex_grid"]:
            why.append("UPOZORENJE: flexibilan beatgrid")
        t = dict(t)
        t.update(score=round(c["score"], 1), why=why, label="siguran izbor" if safe(c) else "iznenađenje",
                 note=notes.get(t["id"]), style_label=styles.describe(c["vec"]),
                 parts={"hist": round(c["hist"], 2), "key": round(c["ks"], 2), "bpm": round(c["bs"], 2),
                        "vibe": round(c["vibe"], 2), "energy": round(c["es"], 2)})
        items.append(t)
    # Preserve original first four; audio can replace only the fifth slot.
    # Unavailable/weak audio evidence leaves the deterministic original fifth.
    audio_status = None
    audio_diagnostics = {}
    if n == 5 and len(items) >= 4:
        from . import auto_audio
        first_four = items[:4]
        excluded_names = identities | {core_title(t["artist"] or "", t["title"] or "") for t in first_four + [cur]}
        # Allow replacing the original fifth itself; only the first four stay reserved.
        if len(items) == 5:
            excluded_names.discard(core_title(items[4]["artist"] or "", items[4]["title"] or ""))
        pool = [c["t"] for c in ok
                if core_title(c["t"]["artist"] or "", c["t"]["title"] or "") not in excluded_names]
        audio, audio_status = auto_audio.fifth(cur, pool, {t["id"] for t in first_four}, audio_diagnostics)
        if audio:
            audio.update(style_label=styles.describe(_vec(audio)), note=notes.get(audio["id"]), parts={"audio_rhythm": audio["score"]/100})
            items = first_four + [audio]
    library.attach_tags(items)
    library.attach_tags([cur])
    cur["style_label"] = styles.describe(cvec)
    blocked_hist.sort(key=lambda x: -x[0])
    return {"current": cur, "mode": mode, "strict": strict, "relaxed": relaxed, "candidates": len(ok),
            "blocked_history": [f"{t['artist']} - {t['title']} ({styles.describe(_vec(t))})" for _, t in blocked_hist[:3]],
            "items": items, "audio_status": audio_status, "audio_diagnostics": audio_diagnostics}
