import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import db, duplicates, keys, library, names, nml, suggest  # noqa: E402
from app.config import SETTINGS  # noqa: E402

NML = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="20"><COLLECTION ENTRIES="4">
<ENTRY TITLE="Song A" ARTIST="Artist"><LOCATION DIR="/:Users/:Miljan/:Documents/:Muzikaa/:" FILE="Artist - Song A.mp3" VOLUME="C:"></LOCATION>
<INFO KEY="1m" COMMENT="10B - Energy 7, Folk, folk, www.djpool.com" PLAYTIME_FLOAT="200.5" IMPORT_DATE="2024/3/30" GENRE="Pop"></INFO>
<TEMPO BPM="100.0"></TEMPO>
<CUE_V2 NAME="Beat Marker" TYPE="4" START="1" LEN="0" REPEATS="-1" HOTCUE="-1"><GRID BPM="100.0"></GRID></CUE_V2>
<CUE_V2 NAME="Beat Marker" TYPE="4" START="9" LEN="0" REPEATS="-1" HOTCUE="-1"><GRID BPM="100.1"></GRID></CUE_V2>
<CUE_V2 NAME="Hot" TYPE="0" START="5" LEN="0" REPEATS="-1" HOTCUE="2"></CUE_V2></ENTRY>
<ENTRY TITLE="Song B"><LOCATION DIR="/:Users/:MILJAN/:Documents/:Muzikaa/:" FILE="Artist - Song B.mp3" VOLUME="C:"></LOCATION>
<INFO KEY="2m" PLAYTIME_FLOAT="210"></INFO><TEMPO BPM="102.0"></TEMPO>
<CUE_V2 NAME="AutoGrid" TYPE="4" START="1" LEN="0" REPEATS="-1" HOTCUE="-1"><GRID BPM="102.0"></GRID></CUE_V2></ENTRY>
<ENTRY TITLE="Song C"><LOCATION DIR="/:Users/:Miljan/:Documents/:Muzikaa/:" FILE="Artist - Song C.mp3" VOLUME="C:"></LOCATION>
<INFO KEY="5d" PLAYTIME_FLOAT="190"></INFO><TEMPO BPM="128.0"></TEMPO></ENTRY>
<ENTRY TITLE="Song A"><LOCATION DIR="/:Users/:Miljan/:Documents/:Muzikaa/:" FILE="Artist - Song A.flac" VOLUME="C:"></LOCATION>
<INFO KEY="1m" PLAYTIME_FLOAT="200.9"></INFO><TEMPO BPM="100.0"></TEMPO></ENTRY>
</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1"><NODE TYPE="PLAYLIST" NAME="Zurka 1"><PLAYLIST ENTRIES="3" TYPE="LIST" UUID="x">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:Miljan/:Documents/:Muzikaa/:Artist - Song A.mp3"/></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:Miljan/:Documents/:Muzikaa/:Artist - Song C.mp3"/></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:Miljan/:Documents/:Muzikaa/:Artist - Song B.mp3"/></ENTRY>
</PLAYLIST></NODE></SUBNODES></NODE></PLAYLISTS></NML>
""")


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "t.db")
    monkeypatch.setattr(db._local, "c", None, raising=False)
    nml_file = tmp_path / "collection.nml"
    nml_file.write_text(NML, encoding="utf-8")
    monkeypatch.setitem(SETTINGS, "nml_path", str(nml_file))
    monkeypatch.setitem(SETTINGS, "history_dir", "")
    monkeypatch.setitem(SETTINGS, "music_folders", [])
    db.init()
    # playlist has only 3 entries; lower the learn threshold by importing then enabling
    library.import_nml()
    db.conn().execute("UPDATE src_playlists SET use_learn=1")
    db.conn().commit()
    library.rebuild_learning()
    yield
    db.conn().close()
    db._local.c = None


def test_key_conversion():
    assert keys.parse_key("1m") == ("1m", "8A")
    assert keys.parse_key("12d") == ("12d", "7B")
    assert keys.parse_key("Am") == ("1m", "8A")
    assert keys.parse_key("C") == ("1d", "8B")
    assert keys.parse_key("7A") == ("12m", "7A")
    assert keys.parse_key("F#m")[1] == "11A"
    assert keys.from_musical_value(16) == ("2m", "9A")
    assert keys.from_musical_value(5) == ("12d", "7B")
    assert keys.parse_key("xyz") is None


def test_key_score():
    assert keys.key_score("8A", "8A") == 1.0
    assert keys.key_score("8A", "9A") == 0.9
    assert keys.key_score("8A", "8B") == 0.9
    assert keys.key_score("8A", "3B") == 0.0


def test_path_conversion():
    p = nml.traktor_path("/:Users/:Miljan/:Documents/:Muzikaa/:", "a.mp3", "C:")
    assert p == "C:\\Users\\Miljan\\Documents\\Muzikaa\\a.mp3"
    assert nml.playlist_key_to_path("C:/:Users/:Miljan/:a.mp3") == "C:\\Users\\Miljan\\a.mp3"
    assert nml.pkey("C:\\Users\\Miljan\\A.mp3") == nml.pkey("c:\\users\\MILJAN\\a.MP3")


def test_filename_split():
    assert names.split_filename("04. Artist - Title (Remix).mp3") == ("Artist", "Title (Remix)")
    assert names.split_filename("JustTitle.mp3") == ("", "JustTitle")


def test_nml_import(lib):
    a = db.one("SELECT * FROM tracks WHERE fmt='mp3' AND title='Song A'")
    assert a["camelot"] == "8A" and a["bpm"] == 100.0
    assert a["flex_grid"] == 1 and a["grid_markers"] == 2
    assert a["user_cues"] == 1
    assert a["energy"] == 7 and a["energy_src"] == "cue"
    b = db.one("SELECT * FROM tracks WHERE title='Song B'")
    assert b["flex_grid"] == 0 and b["artist"] == "Artist"
    assert db.one("SELECT COUNT(*) n FROM tracks")["n"] == 4  # Miljan/MILJAN folders are the same


def test_tags_merge_and_junk(lib):
    a = db.one("SELECT id FROM tracks WHERE fmt='mp3' AND title='Song A'")
    tags = {r["tag"] for r in db.rows("SELECT tag FROM track_tags WHERE track_id=?", (a["id"],))}
    assert tags == {"folk", "www.djpool.com"}
    assert db.one("SELECT junk FROM tag_defs WHERE name='www.djpool.com'")["junk"] == 1


def test_suggest_prefers_own_history(lib):
    a = db.one("SELECT id FROM tracks WHERE fmt='mp3' AND title='Song A'")["id"]
    res = suggest.suggest(a)
    titles = [t["title"] for t in res["items"]]
    assert "Song C" not in titles  # in history, but 28% BPM away and unrelated key: gates ignore history
    b = next(t for t in res["items"] if t["title"] == "Song B")
    assert "u tvojim listama" in b["why"][0]


def test_style_gate_blocks_folk_after_house():
    from app import styles
    house = styles.style_vector("C:\\Muzikaa\\Top Lista\\1 - Party\\Divolly - All Night.flac", "Afro House", {"party hits"}, "All Night")
    folk = styles.style_vector("C:\\Muzikaa\\Top Lista\\Domacice  - pop,misc\\Domacice - NARODNO^\\Semsa - Pristajem.flac", "", set(), "Pristajem")
    pop = styles.style_vector("C:\\Muzikaa\\Top Lista\\1 - Party Hits\\X - Y.mp3", "Pop", {"party hits"}, "Y")
    assert styles.primary(folk) == "narodno"
    assert styles.compat(house, folk) < 0.3
    assert styles.compat(house, pop) > 0.3
    assert styles.compat({}, folk) is None


def test_style_override_wins():
    from app import styles
    assert styles.style_vector("x", "Pop", set(), "t", override="narodno") == {"narodno": 1.0}


def test_duplicates_flac_keep_and_protection(lib):
    groups = duplicates.find_duplicates()
    assert len(groups) == 1
    g = groups[0]
    flac = db.one("SELECT id FROM tracks WHERE fmt='flac'")["id"]
    assert g["keep_id"] == flac
    assert g["conflict"] is True  # mp3 has user cues, flac has none


def test_ogg_comment_parsing():
    import struct
    from app import nowplaying
    comments = [b"ARTIST=Zdravko Colic", b"TITLE=Caje Sukarije"]
    hdr = struct.pack("<I", 5) + b"vendr" + struct.pack("<I", len(comments))
    hdr += b"".join(struct.pack("<I", len(c)) + c for c in comments)
    buf = b"OggS....." + b"\x03vorbis" + hdr + b"\x01\x00"
    i = buf.find(b"\x03vorbis")
    assert nowplaying._parse_ogg_comments(buf, i + 7) == "Zdravko Colic - Caje Sukarije"
    with pytest.raises(Exception):
        nowplaying._parse_ogg_comments(buf[: i + 20], i + 7)


def test_match_song_from_broadcast(lib):
    from app import nowplaying
    a = db.one("SELECT id FROM tracks WHERE fmt='mp3' AND title='Song A'")["id"]
    assert nowplaying.match_song("Artist - Song A") == a
    assert nowplaying.match_song("Artist - Song C (Official Video)") == db.one("SELECT id FROM tracks WHERE title='Song C'")["id"]
    assert nowplaying.match_song("Nepoznat - Nepostojeca") is None


def test_mashup_not_duplicate():
    assert names.version_signature("Song (Miljan mashup)") != names.version_signature("Song")
