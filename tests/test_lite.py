import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
import pytest
from app import config, db, suggest, shortcuts, export_paths

@pytest.fixture
def collection(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_FILE',tmp_path/'db')
    monkeypatch.setattr(db._local,'c',None,raising=False)
    db.init()
    c=db.conn()
    for i in range(1,101):
        c.execute('INSERT INTO tracks(id,pkey,path,artist,title,bpm,camelot,energy,style,on_disk,in_traktor) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
          (i,str(i),'missing.mp3',f'Artist {i%13}',f'Track {i}',100+i%15,'8A' if i%3 else '9A',3+i%6,json.dumps({'pop_dance':1} if i%4 else {'house':1}),1,1))
    c.execute("INSERT INTO events(id,name,active) VALUES(1,'Test',1)")
    c.execute('INSERT INTO plays(event_id,track_id,rating) VALUES(1,99,1)')
    c.execute("INSERT INTO bans(event_id,kind,value) VALUES(1,'track','98')")
    c.execute('INSERT INTO transitions(a,b,n,n2) VALUES(1,8,3,1)')
    c.commit()
    yield c
    c.close();db._local.c=None

def test_original_five_exactly_preserved_and_extra_candidates_unique(collection):
    spec=importlib.util.spec_from_file_location('app.reference_suggest',Path(__file__).with_name('reference_original_suggest.py'))
    reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
    for current in (1,2,20):
        for mode in ('ravno','podizanje','spustanje'):
            for strict in ('strogo','normalno','slobodno'):
                expected=reference.suggest(current,mode,strict=strict)
                actual=suggest.suggest(current,mode,strict=strict)
                assert actual['items']==expected['items']
                ids=[t['id'] for t in actual['items']+actual['additional']]
                assert len(ids)==len(set(ids));assert not ({current,98,99}&set(ids))
                assert len(actual['additional'])<=3

def test_no_heavy_modules_or_midi_opened_on_startup():
    code="from app.main import app; import sys; print([x for x in sys.modules if x.split('.')[0] in {'mido','rtmidi','onnxruntime','numpy','scipy','librosa','torch'}]); assert not any('/api/audio' in r.path or '/api/performance' in r.path or r.path.endswith('/ports') for r in app.routes)"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='[]'

def test_saved_controller_config_never_loaded(monkeypatch):
    monkeypatch.setitem(config.SETTINGS,'shortcuts',{'midi_enabled':True,'midi_input':'CMD','midi':{'toggle_window':{}},'keyboard_enabled':True})
    manager=shortcuts.Manager()
    assert set(manager.settings)=={'keyboard','keyboard_enabled'}
    assert not hasattr(manager,'connect_midi')

def test_export_folder_created_and_unavailable_destination_falls_back(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'SETTINGS_FILE',tmp_path/'settings.json')
    monkeypatch.setitem(config.SETTINGS,'export_dir',str(tmp_path/'missing'/'nested'))
    assert export_paths.resolve()==tmp_path/'missing'/'nested'
    occupied=tmp_path/'not-a-folder';occupied.write_text('keep')
    monkeypatch.setitem(config.SETTINGS,'export_dir',str(occupied/'child'))
    monkeypatch.setattr(export_paths,'default_folder',lambda:tmp_path/'fallback')
    assert export_paths.resolve()==tmp_path/'fallback'
    assert occupied.read_text()=='keep'
    assert config.SETTINGS['export_dir']==str(tmp_path/'fallback')

def test_export_detects_other_windows_profile():
    assert export_paths.foreign_profile(r'C:\Users\Administrator\Documents\DJ asistent plejliste',r'C:\Users\Miljan')
    assert not export_paths.foreign_profile(r'C:\Users\MILJAN\Documents\Exports',r'C:\Users\Miljan')
    assert not export_paths.foreign_profile(r'D:\Music\Exports',r'C:\Users\Miljan')
