import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
from app.config import SETTINGS
db.DB_FILE = Path(tempfile.mkdtemp()) / "api.db"
SETTINGS.update(nml_path=r"c:\Users\Rasto\Documents\Application2\collection.nml", history_dir="", broadcast_enabled=False,
                export_dir=tempfile.mkdtemp(), dupes_dir=tempfile.mkdtemp())
from app.main import app
import time

with TestClient(app, client=("127.0.0.1", 5000)) as c:
    def ok(r):
        assert r.status_code == 200, (r.request.url, r.status_code, r.text[:300])
        return r
    assert c.get("/").status_code == 200 and c.get("/g").status_code == 200
    ok(c.post("/api/import-nml"))
    while c.get("/api/status").json()["job"]["running"]:
        time.sleep(0.2)
    st = ok(c.get("/api/status")).json(); print("status", st["counts"], st["job"]["error"])
    L = ok(c.get("/api/tracks", params={"view": "flex_grid", "size": 3})).json(); print("flex", L["total"], L["items"][0]["title"])
    print("bez_taga", ok(c.get("/api/tracks", params={"view": "bez_taga", "size": 1})).json()["total"])
    print("tag folk", ok(c.get("/api/tracks", params={"tag": "folk", "size": 1})).json()["total"])
    tid = L["items"][0]["id"]
    ok(c.post("/api/now", json={"track_id": tid}))
    s = ok(c.get("/api/suggest", params={"track_id": tid})).json(); print("suggest", len(s["items"]))
    ok(c.post(f"/api/track/{tid}", json={"tags": ["Test tag"], "energy": 8}))
    ok(c.post("/api/tracks/tag", json={"ids": [tid], "add": ["x1"], "remove": ["test tag"]}))
    print("plays", ok(c.get("/api/plays")).json())
    print("playlists", [p["name"] for p in ok(c.get("/api/playlists")).json()])
    ok(c.post("/api/playlists/0/export"))
    r = ok(c.post("/api/playlists/preview", json={"tags_all": ["folk"], "bpm_min": 90, "bpm_max": 110})); print("preview", len(r.json()))
    ev = ok(c.get("/api/events")).json()[0]
    ok(c.post(f"/api/events/{ev['id']}/parts", json={"parts": [{"name": "Docek", "energy": 3, "minutes": 30}, {"name": "Kraj", "energy": 9, "minutes": 30}]}))
    print("part", len(ok(c.get(f"/api/events/{ev['id']}/part-suggest/1")).json()["items"]))
    # guest flow (public, other client)
    with TestClient(app, client=("192.168.1.50", 6000)) as g:
        assert g.get("/api/tracks").status_code == 403
        print("guest info", ok(g.get("/api/guest/info")).json()["open"])
        res = ok(g.get("/api/guest/search", params={"q": "zdravko"})).json(); print("guest search", len(res))
        r1 = ok(g.post("/api/guest/request", json={"track_id": res[0]["id"], "name": "Ana"})); print(r1.json())
        ok(g.post("/api/guest/request", json={"free_text": "Neka pesma - Nepoznata"}))
        r3 = g.post("/api/guest/request", json={"free_text": "treca"}); print("limit", r3.status_code)
    rq = ok(c.get("/api/requests")).json(); print("requests", [(x["in_library"], x["status"]) for x in rq])
    ok(c.post("/api/bans", json={"kind": "genre", "value": "Rap"}))
    ok(c.post("/api/tags/rename", json={"name": "folk", "new": "Folk muzika"}))
    d = ok(c.get("/api/dupes")).json(); print("dupes", len(d))
    assert ok(c.get("/api/qr?kind=guest")).headers["content-type"] == "image/png"
    print("src", len(ok(c.get("/api/src-playlists")).json()))
    ok(c.post("/api/trends/items", json={"song": "Queen - We Are The Champions", "link": "https://youtu.be/abcdefghijk"}))
    print("trends", [(i["song"], i["track"] is not None) for i in ok(c.get("/api/trends/items")).json()])
    print("ai", c.post("/api/ai/situation", json={"text": "kraj"}).json())
print("OK")
