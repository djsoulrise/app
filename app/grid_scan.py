"""Inspect a read-only NML copy. Grid markers are distinct from audio tempo estimates."""
from . import db, nml, audio_rhythm
from .config import SETTINGS


def scan(progress=None):
    path = SETTINGS.get('nml_path')
    if not path:
        raise ValueError('Podesi putanju collection.nml u Podesavanjima.')
    tracks, _ = nml.parse_collection(path)
    c = db.conn()
    c.execute('''CREATE TABLE IF NOT EXISTS grid_scan(
        track_id INTEGER PRIMARY KEY, markers INTEGER, bpm_values INTEGER,
        checked_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    mapping = {r['pkey']: r['id'] for r in db.rows('SELECT id,pkey FROM tracks')}
    c.execute('DELETE FROM grid_scan')
    for i, t in enumerate(tracks):
        if t['pkey'] in mapping:
            c.execute('INSERT INTO grid_scan(track_id,markers,bpm_values) VALUES(?,?,?)',
                      (mapping[t['pkey']], t['grid_markers'], t['grid_bpms']))
        if progress and i % 200 == 0:
            progress(i, len(tracks), 'Proveravam Traktor grid markere')
    c.commit()
    return {'checked': len(tracks), 'matched': db.one('SELECT COUNT(*) n FROM grid_scan')['n']}


def results():
    audio_rhythm.init()
    has = db.one("SELECT name FROM sqlite_master WHERE name='grid_scan'")
    grids = {r['track_id']: r for r in db.rows('SELECT * FROM grid_scan')} if has else {}
    audio = {r['track_id']: r for r in db.rows("SELECT * FROM audio_rhythm WHERE status='ok'")}
    import json
    out = []
    for t in db.rows('SELECT * FROM tracks ORDER BY artist,title'):
        g = grids.get(t['id'])
        a = audio.get(t['id'])
        features = json.loads(a['features']) if audio_rhythm.valid(a, t) else {}
        variable = features.get('variable_tempo_suspected', False)
        if not (t['flex_grid'] or (g and g['markers'] > 1) or variable):
            continue
        reason = ('Razlicite BPM vrednosti u Traktor gridovima' if g and g['bpm_values'] > 1
                  else 'Vise Traktor markera; promena tempa nije potvrdjena' if (g and g['markers'] > 1) or t['flex_grid']
                  else 'Audio sumnja na promenljiv tempo; proveri u Traktoru')
        out.append({**t, 'grid_markers_checked': g['markers'] if g else t['grid_markers'],
                    'grid_bpm_values': g['bpm_values'] if g else None,
                    'audio_variable': variable, 'grid_reason': reason})
    return {'items': out, 'nml_checked': bool(has)}
