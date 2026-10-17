import time
import json
import os
import socket
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from io import BytesIO

import qrcode
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import history_context, event_exports, auto_audio, chorus, shortcuts, native_files, audio_rhythm, grid_scan, ai, config, db, duplicates, library, nowplaying, playlists, styles, suggest, trends
from .config import DATA, SETTINGS
from .scan_control import ScanCancelled
from .keys import key_score
from .names import norm
from .suggest import bpm_score

STATIC = os.path.join(os.path.dirname(__file__), "static")
JOB = {"running": False, "phase": "", "done": 0, "total": 0, "msg": "", "error": None, "result": None}
JOB_LOCK = threading.Lock()
JOB_STOP = threading.Event()
PUBLIC_PREFIXES = ("/g", "/api/guest/", "/static/guest", "/login")


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def guest_url() -> str:
    return f"http://{lan_ip()}:{SETTINGS['port']}/g"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    if not db.one("SELECT 1 FROM events WHERE active=1"):
        db.conn().execute("INSERT INTO events(name, date, active) VALUES(?,?,1)",
                          (f"Vece {datetime.now():%d.%m.%Y}", datetime.now().date().isoformat()))
        db.conn().commit()
    nowplaying.start()
    print(f"\n  DJ asistent: http://127.0.0.1:{SETTINGS['port']}   |  tablet PIN: {SETTINGS['dj_pin']}"
          f"  (http://{lan_ip()}:{SETTINGS['port']}/login?pin=...)\n  Gosti: {guest_url()}\n")
    yield
    auto_audio.STOP.set()
    JOB_STOP.set()


app = FastAPI(title="DJ Miljan Assistant", lifespan=lifespan)


@app.middleware("http")
async def access_control(request: Request, call_next):
    path = request.url.path
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost") or path.startswith(PUBLIC_PREFIXES) \
            or request.cookies.get("dj") == SETTINGS["dj_pin"]:
        return await call_next(request)
    return JSONResponse({"error": "Pristup samo sa DJ racunara ili uz PIN (/login?pin=...)."}, status_code=403)


def _local(request: Request) -> bool:
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1")


@app.get("/login")
def login(pin: str = ""):
    if pin != SETTINGS["dj_pin"]:
        raise HTTPException(403, "Pogresan PIN")
    r = RedirectResponse("/")
    r.set_cookie("dj", pin, httponly=True, samesite="lax", max_age=86400 * 30)
    return r


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/g")
def guest_page():
    return FileResponse(os.path.join(STATIC, "guest.html"))


# ---------- jobs ----------

def _progress(done, total, msg):
    JOB.update(done=done, total=total, phase=msg)


def start_job(name: str, fn, kind=None, cancellable=False) -> dict:
    with JOB_LOCK:
        if JOB['running']:
            raise HTTPException(409, 'Posao je vec u toku.')
        JOB_STOP.clear()
        JOB.update(running=True, phase=name, done=0, total=0, error=None, result=None,
                   kind=kind, cancellable=cancellable, stop_requested=False, cancelled=False)
    def run():
        try:
            JOB['result'] = fn()
        except ScanCancelled as exc:
            JOB.update(cancelled=True, result=exc.result, phase='Zaustavljeno — rezultati sacuvani')
        except Exception as exc:
            JOB['error'] = str(exc)
        finally:
            JOB['running'] = False
    threading.Thread(target=run, daemon=True).start()
    return {'started': True}


@app.post('/api/job/stop')
def stop_job(body: dict = Body(...)):
    with JOB_LOCK:
        if not JOB['running']:
            return {'stopping': False, 'already_finished': True}
        if not JOB.get('cancellable') or body.get('kind') != JOB.get('kind'):
            raise HTTPException(409, 'Ova kontrola ne zaustavlja trenutno aktivan posao.')
        JOB_STOP.set()
        JOB['stop_requested'] = True
    return {'stopping': True}


@app.post("/api/scan")
def scan():
    return start_job("Skeniranje", lambda: library.scan_folders(_progress))


@app.post("/api/import-nml")
def import_nml():
    return start_job("Traktor kolekcija", lambda: library.import_nml(_progress))


@app.post("/api/refresh")
def refresh():
    def both():
        r = {}
        if SETTINGS["nml_path"]:
            r["nml"] = library.import_nml(_progress)
        if SETTINGS["music_folders"]:
            r["scan"] = library.scan_folders(_progress)
        return r
    return start_job("Osvezavanje", both)


@app.get("/api/status")
def status():
    c = db.one("SELECT COUNT(*) n, SUM(on_disk) disk, SUM(in_traktor) trk, SUM(flex_grid) flex FROM tracks")
    cur = nowplaying.STATE
    auto_audio.ensure(cur.get("track_id"))
    return {"job": JOB, "counts": c, "now": cur, "broadcast": nowplaying.BROADCAST, "history": nowplaying.HISTORY,
            "ai": ai.available(), "fpcalc": duplicates._fpcalc() is not None, "guest_url": guest_url(),
            "event": db.one("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 1"),
            "audio_revision": f"{audio_rhythm.SCAN_REVISION}:{auto_audio.REVISION}:{auto_audio.STATE.get('phase')}:{auto_audio.STATE.get('error')}", "audio_threshold": json.dumps(auto_audio.settings(),sort_keys=True)}


# ---------- settings ----------

SETTING_KEYS = ["music_folders", "nml_path", "history_dir", "dupes_dir", "export_dir", "main_folder_hint", "port",
                "broadcast_enabled", "broadcast_port", "model", "weights", "mode", "style_strictness", "guest_limit", "tip_text",
                "tip_ips_payload", "audience", "audio_similarity_threshold"]


@app.get("/api/settings")
def get_settings(request: Request):
    s = {k: SETTINGS[k] for k in SETTING_KEYS}
    s["anthropic_key_set"] = bool(SETTINGS["anthropic_key"])
    s["dj_pin"] = SETTINGS["dj_pin"] if _local(request) else ""
    return s


@app.post("/api/settings")
def set_settings(body: dict = Body(...)):
    if 'audio_similarity_threshold' in body:
        try:
            body['audio_similarity_threshold'] = audio_rhythm.threshold(body['audio_similarity_threshold'])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
    for k in SETTING_KEYS:
        if k in body:
            SETTINGS[k] = body[k]
    if body.get("anthropic_key"):
        SETTINGS["anthropic_key"] = body["anthropic_key"].strip()
    if body.get("clear_key"):
        SETTINGS["anthropic_key"] = ""
    config.save(SETTINGS)
    return {"ok": True}


# ---------- library ----------

@app.get("/api/tracks")
def tracks(request: Request):
    return library.query_tracks(dict(request.query_params))


@app.get("/api/search")
def search(q: str):
    return library.query_tracks({"q": q, "size": 15, "sort": "artist"})["items"]


@app.get("/api/track/{tid}")
def track(tid: int):
    t = library.get_track(tid)
    if not t:
        raise HTTPException(404)
    return t


@app.post("/api/track/{tid}")
def edit_track(tid: int, body: dict = Body(...)):
    c = db.conn()
    if "tags" in body:
        c.execute("DELETE FROM track_tags WHERE track_id=?", (tid,))
        for t in body["tags"]:
            if t.strip():
                c.execute("INSERT OR IGNORE INTO track_tags VALUES(?,?)", (tid, library.ensure_tag(c, t)))
    if "energy" in body:
        e = body["energy"]
        if e in (None, ""):
            c.execute("UPDATE tracks SET energy_src=NULL WHERE id=?", (tid,))
        else:
            c.execute("UPDATE tracks SET energy=?, energy_src='ručno' WHERE id=?", (max(1, min(10, int(e))), tid))
    if "style" in body:
        c.execute("UPDATE tracks SET style_override=? WHERE id=?", (body["style"] or None, tid))
    c.commit()
    library.recompute_energy()
    return library.get_track(tid)


@app.post("/api/tracks/tag")
def bulk_tag(body: dict = Body(...)):
    library.set_tags(body.get("ids", []), body.get("add", []), body.get("remove", []))
    return {"ok": True}


def local_track_file(tid: int, request: Request):
    if not _local(request) or sys.platform != 'win32':
        raise HTTPException(400, 'Opcija radi samo na Windows DJ racunaru.')
    try:
        return native_files.resolve_track_path(tid)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc))


@app.get('/api/track-path/{tid}')
def track_path(tid: int, request: Request):
    return {'path': local_track_file(tid, request)}


@app.post('/api/reveal/{tid}')
def reveal(tid: int, request: Request):
    path = local_track_file(tid, request)
    try:
        native_files.reveal_file(path)
    except OSError as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'path': path}


@app.post('/api/copy-song/{tid}')
def copy_song(tid: int, request: Request):
    path = local_track_file(tid, request)
    try:
        native_files.copy_song(path)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'path': path}


@app.post('/api/drag-song/{tid}')
def drag_song(tid: int, request: Request):
    path = local_track_file(tid, request)
    try:
        native_files.drag_song(path)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'path': path}


@app.get('/api/file/{tid}')
def file(tid: int, request: Request):
    t = db.one('SELECT path FROM tracks WHERE id=?', (tid,))
    if not _local(request) or not t:
        raise HTTPException(404)
    try:
        path = native_files.resolve_track_path(tid)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(path, filename=os.path.basename(path))


# ---------- tags ----------

@app.get("/api/tags")
def tags():
    return db.rows("SELECT d.name, d.display, d.junk, d.ignored, COUNT(t.track_id) n FROM tag_defs d "
                   "LEFT JOIN track_tags t ON t.tag=d.name GROUP BY d.name ORDER BY n DESC")


@app.post("/api/tags/rename")
def rename_tag(body: dict = Body(...)):
    old, new_disp = library.norm_tag(body["name"]), body["new"].strip()
    new = library.norm_tag(new_disp)
    if not new:
        raise HTTPException(400, "Prazan naziv")
    c = db.conn()
    if new == old:
        c.execute("UPDATE tag_defs SET display=? WHERE name=?", (new_disp, old))
    else:
        library.ensure_tag(c, new_disp)
        c.execute("UPDATE OR IGNORE track_tags SET tag=? WHERE tag=?", (new, old))
        c.execute("DELETE FROM track_tags WHERE tag=?", (old,))
        c.execute("DELETE FROM tag_defs WHERE name=?", (old,))
    c.commit()
    return {"ok": True}


@app.post("/api/tags/flag")
def flag_tags(body: dict = Body(...)):
    c = db.conn()
    for n in body.get("names", []):
        if "junk" in body:
            c.execute("UPDATE tag_defs SET junk=? WHERE name=?", (int(body["junk"]), n))
        if "ignored" in body:
            c.execute("UPDATE tag_defs SET ignored=? WHERE name=?", (int(body["ignored"]), n))
    c.commit()
    library.recompute_energy()
    return {"ok": True}


@app.post("/api/tags/delete")
def delete_tag(body: dict = Body(...)):
    c = db.conn()
    c.execute("DELETE FROM track_tags WHERE tag=?", (body["name"],))
    c.execute("DELETE FROM tag_defs WHERE name=?", (body["name"],))
    c.commit()
    return {"ok": True}


@app.post("/api/ai/tag-suggest")
def ai_tag_suggest():
    try:
        return {"added": ai.suggest_tags()}
    except ai.AIError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tag-suggestions")
def tag_suggestions():
    rows = db.rows("SELECT s.track_id, s.tag, t.artist, t.title, d.display FROM tag_suggestions s "
                   "JOIN tracks t ON t.id=s.track_id JOIN tag_defs d ON d.name=s.tag ORDER BY t.artist")
    return rows


@app.post("/api/tag-suggestions/apply")
def apply_suggestions(body: dict = Body(...)):
    c = db.conn()
    for it in body.get("items", []):
        if body.get("accept", True):
            c.execute("INSERT OR IGNORE INTO track_tags VALUES(?,?)", (it["track_id"], it["tag"]))
        c.execute("DELETE FROM tag_suggestions WHERE track_id=? AND tag=?", (it["track_id"], it["tag"]))
    c.commit()
    library.recompute_energy()
    return {"ok": True}


# ---------- duplicates ----------

@app.get("/api/dupes")
def dupes():
    return duplicates.find_duplicates()


@app.post("/api/dupes/move")
def dupes_move(body: dict = Body(...)):
    if not body.get("confirm"):
        raise HTTPException(400, "Potrebna je potvrda.")
    try:
        return duplicates.move_to_dupes(body.get("ids", []))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- now playing & suggestions ----------

@app.get("/api/now")
def now():
    cur = nowplaying.STATE
    auto_audio.ensure(cur.get("track_id"))
    return {**cur, "track": library.get_track(cur["track_id"]) if cur["track_id"] else None}


@app.post("/api/now")
def set_now(body: dict = Body(...)):
    if not db.one("SELECT 1 FROM tracks WHERE id=?", (body["track_id"],)):
        raise HTTPException(404)
    nowplaying.set_current(body["track_id"], "rucno")
    return {"ok": True}


@app.get("/api/styles")
def style_list():
    return styles.LABELS


@app.get("/api/suggest")
def get_suggest(track_id: int, mode: str | None = None, strict: str | None = None):
    result=suggest.suggest(track_id, mode, strict=strict)
    audio=result.get('audio_diagnostics',{}).get('accepted_top',[])[:3]
    result['history_context']=history_context.neighbors(track_id,{t['id'] for t in audio})
    return result


@app.get('/api/history-context')
def get_history_context(track_id: int):
    return history_context.neighbors(track_id)


@app.get("/api/plays")
def plays(event_id: int | None = None):
    ev = event_id or suggest.active_event_id()
    rows = db.rows("SELECT p.id, p.track_id, p.ts, p.source, p.rating, t.artist, t.title, t.bpm, t.camelot, t.energy "
                   "FROM plays p JOIN tracks t ON t.id=p.track_id WHERE p.event_id=? ORDER BY p.id", (ev,))
    return rows


@app.post("/api/plays/{pid}/rate")
def rate_play(pid: int, body: dict = Body(...)):
    db.conn().execute("UPDATE plays SET rating=? WHERE id=?", (int(body["rating"]), pid))
    db.conn().commit()
    return {"ok": True}


@app.delete("/api/plays/{pid}")
def delete_play(pid: int):
    db.conn().execute("DELETE FROM plays WHERE id=?", (pid,))
    db.conn().commit()
    return {"ok": True}


@app.post("/api/notes")
def save_note(body: dict = Body(...)):
    c = db.conn()
    if body.get("note", "").strip():
        c.execute("INSERT OR REPLACE INTO notes VALUES(?,?,?)", (body["a"], body["b"], body["note"].strip()))
    else:
        c.execute("DELETE FROM notes WHERE a=? AND b=?", (body["a"], body["b"]))
    c.commit()
    return {"ok": True}


@app.post("/api/learning/rebuild")
def rebuild():
    library.rebuild_learning()
    library.recompute_energy()
    return {"ok": True}


@app.get("/api/src-playlists")
def src_playlists():
    return db.rows("SELECT * FROM src_playlists ORDER BY source, name")


@app.post("/api/src-playlists/{pid}")
def src_playlist_toggle(pid: int, body: dict = Body(...)):
    db.conn().execute("UPDATE src_playlists SET use_learn=? WHERE id=?", (int(body["use"]), pid))
    db.conn().commit()
    library.rebuild_learning()
    return {"ok": True}


# ---------- app playlists ----------

@app.get("/api/playlists")
def list_playlists():
    return playlists.BUILTIN + db.rows("SELECT * FROM app_playlists ORDER BY name")


def _get_playlist(pid: int) -> dict:
    if pid == 0:
        return playlists.BUILTIN[0]
    pl = db.one("SELECT * FROM app_playlists WHERE id=?", (pid,))
    if not pl:
        raise HTTPException(404)
    return pl


@app.post("/api/playlists")
def save_playlist(body: dict = Body(...)):
    c = db.conn()
    rules = json.dumps(body.get("rules") or {})
    ids = json.dumps(body.get("track_ids") or [])
    if body.get("id"):
        c.execute("UPDATE app_playlists SET name=?, kind=?, rules=?, track_ids=? WHERE id=?",
                  (body["name"], body["kind"], rules, ids, body["id"]))
        pid = body["id"]
    else:
        pid = c.execute("INSERT INTO app_playlists(name, kind, rules, track_ids) VALUES(?,?,?,?)",
                        (body["name"], body["kind"], rules, ids)).lastrowid
    c.commit()
    return {"id": pid}


@app.delete("/api/playlists/{pid}")
def delete_playlist(pid: int):
    db.conn().execute("DELETE FROM app_playlists WHERE id=?", (pid,))
    db.conn().commit()
    return {"ok": True}


@app.get("/api/playlists/{pid}/tracks")
def playlist_tracks(pid: int):
    return playlists.playlist_tracks(_get_playlist(pid))


@app.post("/api/playlists/{pid}/export")
def export_playlist(pid: int):
    pl = _get_playlist(pid)
    try:
        return {"path": playlists.export_m3u(pl["name"], playlists.playlist_tracks(pl))}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/playlists/preview")
def preview_rules(body: dict = Body(...)):
    return playlists.rule_tracks(body)[:200]


# ---------- AI situation ----------

@app.post("/api/ai/situation")
def ai_situation(body: dict = Body(...)):
    try:
        return ai.situation(body["text"], int(body.get("count") or 15))
    except ai.AIError as e:
        raise HTTPException(400, str(e))


# ---------- trends ----------

@app.get("/api/trends/sources")
def trend_sources():
    return db.rows("SELECT * FROM trend_sources ORDER BY id")


@app.post("/api/trends/sources")
def add_trend_source(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL mora poceti sa http:// ili https://")
    db.conn().execute("INSERT INTO trend_sources(name, url, kind) VALUES(?,?,?)",
                      (body.get("name") or url, url, body.get("kind", "rss")))
    db.conn().commit()
    return {"ok": True}


@app.delete("/api/trends/sources/{sid}")
def del_trend_source(sid: int):
    db.conn().execute("DELETE FROM trend_items WHERE source_id=?", (sid,))
    db.conn().execute("DELETE FROM trend_sources WHERE id=?", (sid,))
    db.conn().commit()
    return {"ok": True}


@app.post("/api/trends/fetch/{sid}")
def trend_fetch(sid: int):
    src = db.one("SELECT * FROM trend_sources WHERE id=?", (sid,))
    if not src:
        raise HTTPException(404)
    try:
        return {"added": trends.fetch_source(src)}
    except Exception as e:
        raise HTTPException(400, f"Preuzimanje nije uspelo: {e}")


@app.post("/api/trends/items")
def add_trend_item(body: dict = Body(...)):
    song = (body.get("song") or "").strip()
    if not song:
        raise HTTPException(400, "Upisi izvodjaca i naslov.")
    artist, _, title = song.partition(" - ")
    link = (body.get("link") or "").strip()
    if link and not link.startswith(("http://", "https://")):
        raise HTTPException(400, "Link mora poceti sa http:// ili https://")
    db.conn().execute("INSERT INTO trend_items(source_id, title, artist, song, link, track_id, fetched) VALUES(0,?,?,?,?,?,?)",
                      (title or artist, artist if title else "", song, link, nowplaying.match_song(song),
                       datetime.now().isoformat(timespec="seconds")))
    db.conn().commit()
    return {"ok": True}


@app.get("/api/trends/items")
def trend_items():
    items = db.rows("SELECT * FROM trend_items ORDER BY COALESCE(fit, -1) DESC, id DESC LIMIT 300")
    for i in items:
        i["track"] = library.get_track(i["track_id"]) if i["track_id"] else None
        if not i["track"] and i["artist"]:
            i["similar"] = db.rows("SELECT * FROM tracks WHERE LOWER(artist)=? ORDER BY playcount DESC LIMIT 3", (i["artist"].lower(),))
    return items


@app.delete("/api/trends/items/{iid}")
def del_trend_item(iid: int):
    db.conn().execute("DELETE FROM trend_items WHERE id=?", (iid,))
    db.conn().commit()
    return {"ok": True}


# ---------- events ----------

@app.get("/api/events")
def events():
    evs = db.rows("SELECT * FROM events ORDER BY id DESC")
    for e in evs:
        e["parts"] = db.rows("SELECT * FROM event_parts WHERE event_id=? ORDER BY pos", (e["id"],))
        e["plays"] = db.one("SELECT COUNT(*) n FROM plays WHERE event_id=?", (e["id"],))["n"]
    return evs


@app.post("/api/events")
def create_event(body: dict = Body(...)):
    c = db.conn()
    eid = c.execute("INSERT INTO events(name, date, venue) VALUES(?,?,?)",
                    (body["name"], body.get("date") or datetime.now().date().isoformat(), body.get("venue", ""))).lastrowid
    c.commit()
    return {"id": eid}


@app.get('/api/events/{eid}/days')
def event_days(eid: int):
    try:return event_exports.days(eid)
    except ValueError as exc:raise HTTPException(400,str(exc))


@app.post('/api/events/{eid}/days/{day}/export')
def export_event_day(eid: int, day: str, request: Request, body: dict = Body(...)):
    _shortcut_local(request)
    try:
        event_exports.day_value(day);event_exports.event(eid)
        kind=body.get('kind','m3u')
        if kind not in ('m3u','songs'):raise ValueError('Nepoznata vrsta izvoza.')
        if kind=='songs':
            return start_job('Izvoz pesama za '+day,lambda:event_exports.export(eid,day,True,_progress,JOB_STOP),kind='day_export',cancellable=True)
        return event_exports.export(eid,day)
    except (ValueError,OSError) as exc:raise HTTPException(400,str(exc))


@app.post("/api/events/{eid}/activate")
def activate_event(eid: int):
    c = db.conn()
    c.execute("UPDATE events SET active=0")
    c.execute("UPDATE events SET active=1 WHERE id=?", (eid,))
    c.commit()
    return {"ok": True}


@app.post("/api/events/{eid}/requests-open")
def requests_open(eid: int, body: dict = Body(...)):
    db.conn().execute("UPDATE events SET requests_open=? WHERE id=?", (int(body["open"]), eid))
    db.conn().commit()
    return {"ok": True}


@app.post("/api/events/{eid}/parts")
def set_parts(eid: int, body: dict = Body(...)):
    c = db.conn()
    c.execute("DELETE FROM event_parts WHERE event_id=?", (eid,))
    for i, p in enumerate(body.get("parts", [])):
        c.execute("INSERT INTO event_parts(event_id, pos, name, energy, minutes) VALUES(?,?,?,?,?)",
                  (eid, i, p["name"], int(p.get("energy") or 5), int(p.get("minutes") or 30)))
    c.commit()
    return {"ok": True}


@app.get("/api/events/{eid}/part-suggest/{pos}")
def part_suggest(eid: int, pos: int):
    parts = db.rows("SELECT * FROM event_parts WHERE event_id=? ORDER BY pos", (eid,))
    if pos >= len(parts):
        raise HTTPException(404)
    part = parts[pos]
    frac = pos / max(1, len(parts) - 1)
    played = {r["track_id"] for r in db.rows("SELECT track_id FROM plays WHERE event_id=?", (eid,))}
    ban = suggest.banned(eid)
    rows = [t for t in db.rows("SELECT * FROM tracks WHERE energy IS NOT NULL") if t["id"] not in played and not suggest.is_banned(t, ban)]
    rows.sort(key=lambda t: abs(t["energy"] - part["energy"]) + 3 * abs((t["avg_pos"] if t["avg_pos"] is not None else frac) - frac)
              - 0.05 * min(t["playcount"] or 0, 20))
    out = rows[:10]
    library.attach_tags(out)
    return {"part": part, "items": out}


@app.get("/api/bans")
def get_bans(event_id: int | None = None):
    return db.rows("SELECT * FROM bans WHERE event_id=?", (event_id or suggest.active_event_id(),))


@app.post("/api/bans")
def add_ban(body: dict = Body(...)):
    ev = body.get("event_id") or suggest.active_event_id()
    db.conn().execute("INSERT INTO bans(event_id, kind, value) VALUES(?,?,?)", (ev, body["kind"], str(body["value"])))
    db.conn().commit()
    return {"ok": True}


@app.delete("/api/bans/{bid}")
def del_ban(bid: int):
    db.conn().execute("DELETE FROM bans WHERE id=?", (bid,))
    db.conn().commit()
    return {"ok": True}


# ---------- requests (DJ side) ----------

@app.get("/api/requests")
def dj_requests():
    ev = suggest.active_event_id()
    cur = db.one("SELECT * FROM tracks WHERE id=?", (nowplaying.STATE["track_id"],)) if nowplaying.STATE["track_id"] else None
    played = {r["track_id"] for r in db.rows("SELECT track_id FROM plays WHERE event_id=?", (ev,))}
    ban = suggest.banned(ev)
    out = []
    for r in db.rows("SELECT * FROM requests WHERE event_id=? ORDER BY votes DESC, id", (ev,)):
        t = db.one("SELECT * FROM tracks WHERE id=?", (r["track_id"],)) if r["track_id"] else None
        if not t and r["free_text"]:
            tid = nowplaying.match_song(r["free_text"])
            t = db.one("SELECT * FROM tracks WHERE id=?", (tid,)) if tid else None
        r["in_library"] = bool(t)
        r["played"] = bool(t and t["id"] in played)
        r["banned"] = bool(t and suggest.is_banned(t, ban))
        if t:
            library.attach_tags([t])
        r["track"] = t
        if t and cur:
            r["fit"] = {"bpm": round(bpm_score(cur["bpm"], t["bpm"])[0], 2), "key": key_score(cur["camelot"], t["camelot"])}
        out.append(r)
    return out


@app.post("/api/requests/{rid}")
def update_request(rid: int, body: dict = Body(...)):
    db.conn().execute("UPDATE requests SET status=? WHERE id=?", (body["status"], rid))
    db.conn().commit()
    return {"ok": True}


@app.post("/api/requests/{rid}/tip")
def mark_tip(rid: int, body: dict = Body(...)):
    db.conn().execute("UPDATE requests SET tip=? WHERE id=?", (body.get("tip"), rid))
    db.conn().commit()
    return {"ok": True}


@app.post("/api/tip-image")
async def upload_tip_image(request: Request):
    data = await request.body()
    ctype = request.headers.get("content-type", "")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(ctype)
    if not ext or len(data) > 3_000_000:
        raise HTTPException(400, "Dozvoljeni su PNG/JPG/WEBP do 3 MB.")
    name = f"tip.{ext}"
    (DATA / "uploads" / name).write_bytes(data)
    SETTINGS["tip_image"] = name
    config.save(SETTINGS)
    return {"ok": True}


@app.get("/api/qr")
def qr(kind: str = "guest"):
    data = guest_url() if kind == "guest" else SETTINGS["tip_ips_payload"]
    if not data:
        raise HTTPException(404)
    return _qr_png(data)


def _qr_png(data: str) -> Response:
    buf = BytesIO()
    qrcode.make(data, box_size=8, border=2).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ---------- guest side (public) ----------

def _guest_key(request: Request) -> str:
    return request.cookies.get("gk") or (request.client.host if request.client else "x")


@app.get("/api/guest/info")
def guest_info(request: Request):
    ev = db.one("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 1")
    resp = JSONResponse({
        "event": ev["name"] if ev else "", "open": bool(ev and ev["requests_open"]),
        "tip_text": SETTINGS["tip_text"], "tip_qr": bool(SETTINGS["tip_ips_payload"]),
        "tip_image": f"/api/guest/tip-image" if SETTINGS["tip_image"] else "",
        "limit": SETTINGS["guest_limit"]})
    if "gk" not in request.cookies:
        resp.set_cookie("gk", uuid.uuid4().hex, max_age=86400 * 3, httponly=True, samesite="lax")
    return resp


@app.get("/api/guest/tip-image")
def guest_tip_image():
    p = DATA / "uploads" / (SETTINGS["tip_image"] or "none")
    if not SETTINGS["tip_image"] or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/guest/tip-qr.png")
def guest_tip_qr():
    if not SETTINGS["tip_ips_payload"]:
        raise HTTPException(404)
    return _qr_png(SETTINGS["tip_ips_payload"])


@app.get("/api/guest/search")
def guest_search(q: str):
    if len(q.strip()) < 2:
        return []
    ban = suggest.banned(suggest.active_event_id())
    items = library.query_tracks({"q": q, "size": 40})["items"]
    return [{"id": t["id"], "artist": t["artist"], "title": t["title"]} for t in items if not suggest.is_banned(t, ban)][:15]


@app.get("/api/guest/list")
def guest_list():
    ev = suggest.active_event_id()
    rows = db.rows("SELECT r.id, r.votes, r.free_text, r.status, t.artist, t.title FROM requests r "
                   "LEFT JOIN tracks t ON t.id=r.track_id WHERE r.event_id=? AND r.status IN ('novo','prihvaceno','odlozeno') "
                   "ORDER BY r.votes DESC, r.id LIMIT 30", (ev,))
    return [{"id": r["id"], "votes": r["votes"], "status": r["status"],
             "song": f"{r['artist']} - {r['title']}" if r["title"] else r["free_text"]} for r in rows]


@app.post("/api/guest/request")
def guest_request(request: Request, body: dict = Body(...)):
    ev = db.one("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 1")
    if not ev or not ev["requests_open"]:
        raise HTTPException(403, "Zahtevi trenutno nisu otvoreni.")
    gk = _guest_key(request)
    lim = SETTINGS["guest_limit"]
    since = (datetime.now() - timedelta(minutes=int(lim["minutes"]))).isoformat(timespec="seconds")
    n = db.one("SELECT COUNT(*) n FROM requests WHERE event_id=? AND guest_key=? AND created>=?", (ev["id"], gk, since))["n"]
    if n >= int(lim["count"]):
        raise HTTPException(429, f"Moze najvise {lim['count']} zahteva na {lim['minutes']} min. Probaj malo kasnije.")
    tid = body.get("track_id")
    free = (body.get("free_text") or "").strip()[:150]
    t = db.one("SELECT * FROM tracks WHERE id=?", (tid,)) if tid else None
    if not t and not free:
        raise HTTPException(400, "Izaberi pesmu ili upisi naziv.")
    status = "novo"
    if t and suggest.is_banned(t, suggest.banned(ev["id"])):
        status = "odbijeno"
    c = db.conn()
    rid = c.execute("INSERT INTO requests(event_id, track_id, free_text, guest, message, guest_key, status, created) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (ev["id"], t["id"] if t else None, free if not t else "", (body.get("name") or "")[:40],
                     (body.get("message") or "")[:200], gk, status, datetime.now().isoformat(timespec="seconds"))).lastrowid
    c.execute("INSERT OR IGNORE INTO votes VALUES(?,?)", (rid, gk))
    c.commit()
    if status == "odbijeno":
        return {"ok": True, "msg": "Hvala! Ovu pesmu veceras nece biti na listi, probaj neku drugu."}
    return {"ok": True, "msg": "Zahtev je poslat. Hvala!"}


@app.post("/api/guest/vote/{rid}")
def guest_vote(rid: int, request: Request):
    gk = _guest_key(request)
    c = db.conn()
    if c.execute("INSERT OR IGNORE INTO votes VALUES(?,?)", (rid, gk)).rowcount:
        c.execute("UPDATE requests SET votes=votes+1 WHERE id=?", (rid,))
    c.commit()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/audio/status")
def audio_status():
    return audio_rhythm.status()


@app.post("/api/audio/scan")
def audio_scan():
    return start_job("Audio deep scan", lambda: audio_rhythm.scan(_progress, JOB_STOP), kind="audio", cancellable=True)


@app.post("/api/grids/scan")
def grids_scan():
    return start_job("Traktor gridovi", lambda: grid_scan.scan(_progress, JOB_STOP), kind="grid_meta", cancellable=True)


@app.get("/api/grids")
def grids_list():
    return grid_scan.results()


@app.get("/api/audio/tracks")
def audio_tracks(q: str = ''):
    return audio_rhythm.scanned_tracks(q)


@app.get("/api/audio/matches")
def audio_matches(track_id: int, threshold: float | None = None):
    try:
        report = audio_rhythm.matches(track_id, cutoff=threshold)
        report['recommendation'] = suggest.suggest(track_id).get('audio_diagnostics', {})
        report['saved_threshold'] = audio_rhythm.threshold()
        return report
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/audio/threshold")
def save_audio_threshold(body: dict = Body(...)):
    try:
        value = audio_rhythm.threshold(body['threshold'])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    SETTINGS['audio_similarity_threshold'] = value
    config.save(SETTINGS)
    return {'ok': True, 'threshold': value}


@app.post('/api/grids/audio-search')
def audio_grid_search():
    return start_job('Audio pretraga promenljivog tempa', lambda: grid_scan.audio_search(_progress, JOB_STOP), kind='grid_audio', cancellable=True)


# Native shortcuts are only configurable from this computer, never from guest/tablet clients.
def _shortcut_local(request):
    if not _local(request):
        raise HTTPException(403, "Precice su dostupne samo na DJ racunaru.")


@app.get('/api/shortcuts')
def shortcut_status(request: Request):
    _shortcut_local(request)
    return shortcuts.manager.status()


@app.get('/api/shortcuts/ports')
def shortcut_ports(request: Request):
    _shortcut_local(request)
    try:
        return {'ports': shortcuts.manager.ports()}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/shortcuts/events')
def shortcut_events(request: Request, after: int = 0):
    _shortcut_local(request)
    return {'events': shortcuts.manager.queued(after)}


@app.post('/api/shortcuts/{operation:path}')
def shortcut_change(operation: str, request: Request, body: dict = Body(...)):
    _shortcut_local(request)
    m = shortcuts.manager
    try:
        if operation == 'midi/connect':
            m.connect_midi(str(body.get('name') or ''), bool(body.get('enabled')))
        elif operation == 'midi/learn':
            m.learn_midi(body.get('action'), body.get('combination', True))
        elif operation == 'midi/clear':
            m.clear_midi(body.get('action'))
        else:
            with m.lock:
                if operation == 'keyboard':
                    m.bind_keyboard(body.get('action'), body.get('binding'))
                elif operation == 'keyboard/enabled':
                    m.settings['keyboard_enabled'] = bool(body.get('enabled'))
                    m.persist()
                elif operation == 'keyboard-learning':
                    m.keyboard_learning_until = time.monotonic()+30 if body.get('enabled') else 0
                elif operation == 'editing':
                    m.editing = bool(body.get('editing'))
                elif operation == 'learn/cancel':
                    m.learning = None
                    m.keyboard_learning_until = 0
                else:
                    raise ValueError('Nepoznata opcija precice.')
        return m.status()
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc))



@app.get('/api/ui-preferences')
def ui_preferences(request: Request):
    _shortcut_local(request)
    saved = SETTINGS.get('ui_preferences') or {}
    return {'font_scale': saved.get('font_scale', 115),
            'ctrl_wheel_text': saved.get('ctrl_wheel_text', True)}


@app.post('/api/ui-preferences')
def save_ui_preferences(request: Request, body: dict = Body(...)):
    _shortcut_local(request)
    current = ui_preferences(request)
    scale = body.get('font_scale', current['font_scale'])
    enabled = body.get('ctrl_wheel_text', current['ctrl_wheel_text'])
    if type(scale) is not int or not 75 <= scale <= 160 or scale % 5 or type(enabled) is not bool:
        raise HTTPException(400, 'Velicina teksta mora biti 75–160%, u koracima od 5%.')
    SETTINGS['ui_preferences'] = {'font_scale': scale, 'ctrl_wheel_text': enabled}
    config.save(SETTINGS)
    return SETTINGS['ui_preferences']


@app.get('/api/audio/chorus/status')
def auto_audio_status():
    return auto_audio.status()


@app.post('/api/audio/chorus/settings')
def auto_audio_settings(request: Request, body: dict = Body(...)):
    _shortcut_local(request)
    try: return auto_audio.settings(body)
    except (ValueError,TypeError) as exc: raise HTTPException(400,str(exc))


@app.post('/api/audio/chorus/scan')
def auto_audio_scan(request: Request):
    _shortcut_local(request)
    return start_job('Audio ritam / refren biblioteke',lambda:auto_audio.scan(_progress,JOB_STOP),kind='chorus_library',cancellable=True)


@app.get('/api/audio/chorus/results')
def auto_audio_results():
    tid=nowplaying.STATE.get('track_id')
    if not tid: return {'items':[], 'audio_diagnostics':{}, 'audio_status':'Nema trenutne pesme u Traktoru.'}
    return suggest.suggest(tid)
