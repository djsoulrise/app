import json,threading,time
from pathlib import Path
import pytest
from scanner import storage,features,matcher,jobs,ENGINE
from scanner.self_test import music,write

@pytest.fixture
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DATABASE',tmp_path/'scanner.db');storage.init();jobs.STOP.clear();jobs.PAUSE.clear();jobs.STATE.update(started=time.time(),errors=[],active={})
    return tmp_path

def test_real_workers_decoder_broken_file_and_resume(database):
    write(database/'valid.wav',music());(database/'broken.wav').write_bytes(b'broken')
    result=jobs.scan([str(database)],2)
    assert result['scanned']==1 and result['failed']==1
    assert jobs.scan([str(database)],2)['pending_at_start']==0
    assert jobs.scan([str(database)],2,retry=True)['failed']==1
    assert len(storage.load_records())==1

def test_changed_file_before_upsert_never_commits_partial_features(database):
    path=database/'valid.wav';write(path,music());result=features.extract(path)
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError):storage.save(result)
    assert storage.rows('SELECT * FROM features')==[]

def test_content_hash_export_relocate_and_feedback(database):
    path=database/'alpha.wav';write(path,music());r=features.extract(path);storage.save(r)
    new=database/'relocated';new.mkdir();copy=new/'alpha.wav';copy.write_bytes(path.read_bytes())
    archive=database/'full.djscan';storage.export(archive,True);path.unlink()
    storage.DATABASE=database/'new.db';storage.init();result=storage.import_full(archive,[str(new)])
    assert result['relocated']==1 and result['unresolved']==0
    assert storage.rows("SELECT * FROM files WHERE status='ok'")[0]['path']==str(copy.resolve())

def test_turbo_workers_respect_cpu_and_ram(monkeypatch):
    monkeypatch.setattr(jobs.os,'cpu_count',lambda:16);monkeypatch.setattr(jobs,'memory_available',lambda:16*1024**3)
    assert jobs.workers(4,True)==16
    monkeypatch.setattr(jobs,'memory_available',lambda:2*1024**3)
    assert jobs.workers(16,True)==1

def test_stop_and_threshold_controls():
    event=threading.Event();event.set()
    with pytest.raises(InterruptedError):features.run_engine({'operation':'probe'},stop=event)
    m={'tonal':{'score':90,'contrast':1.1,'seconds':20}}
    assert matcher.decision(m)==[]
    m['tonal']['contrast']=2;assert matcher.decision(m)==['tonal']
    m['tonal']['seconds']=2;assert matcher.decision(m)==[]
