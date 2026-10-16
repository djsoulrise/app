import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from app import db, library, suggest, duplicates
from app.config import SETTINGS
tmp = Path(tempfile.mkdtemp())
db.DB_FILE = tmp / "smoke.db"
SETTINGS["nml_path"] = r"c:\Users\Rasto\Documents\Application2\collection.nml"
SETTINGS["history_dir"] = ""
db.init()
t = time.time()
print(library.import_nml())
print("import s", round(time.time() - t, 1))
for q in ["SELECT COUNT(*) n, SUM(flex_grid) flex, SUM(no_artist) noart, SUM(unsorted) uns, SUM(energy_src='cue') cue FROM tracks",
          "SELECT COUNT(*) n FROM tag_defs", "SELECT COUNT(*) n FROM tag_defs WHERE junk=1",
          "SELECT COUNT(*) n FROM src_playlists WHERE use_learn=1", "SELECT COUNT(*) n FROM transitions",
          "SELECT COUNT(*) n FROM tracks WHERE camelot IS NULL", "SELECT COUNT(*) n FROM tracks WHERE bpm IS NULL"]:
    print(q[:70], db.one(q))
tid = db.one("SELECT t.id FROM tracks t JOIN transitions r ON r.a=t.id GROUP BY t.id ORDER BY SUM(r.n) DESC LIMIT 1")["id"]
t = time.time()
res = suggest.suggest(tid)
print("suggest s", round(time.time() - t, 2))
print("CUR", res["current"]["artist"], "-", res["current"]["title"], res["current"]["camelot"], res["current"]["bpm"])
for i in res["items"]:
    print(i["label"], i["score"], i["artist"], "-", i["title"], "|", "; ".join(i["why"]))
t = time.time()
g = duplicates.find_duplicates()
print("dupe groups", len(g), round(time.time() - t, 1))
for x in g[:5]:
    print(x["title"], [f["fmt"] for f in x["files"]], x["conflict"])
print(db.rows("SELECT artist,title,key_open,camelot FROM tracks WHERE title LIKE '%Pick Up%' LIMIT 2"))
