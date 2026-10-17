import hashlib,json,zipfile,threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import db,config,suggest,external_audio as audio
from app.main import app
from app.scan_control import ScanCancelled

@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_FILE',tmp_path/'library.db');monkeypatch.setattr(db._local,'c',None,raising=False)
    monkeypatch.setattr(config,'SETTINGS_FILE',tmp_path/'settings.json');monkeypatch.setitem(config.SETTINGS,'additional_source','original');db.init()
    c=db.conn();tracks=[]
    for i in range(1,41):
        name=f'Song {chr(65+i//26)}{chr(65+i%26)}.mp3';path=tmp_path/name;path.write_bytes(bytes([i])*32);hash=hashlib.sha256(path.read_bytes()).hexdigest()
        c.execute('INSERT INTO tracks(id,pkey,path,artist,title,bpm,camelot,energy,style,on_disk,in_traktor,size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(i,str(i),str(path),f'Artist {i}',name,120,'8A',6,'{"house":1}',1,1,32))
        tracks.append({'hash':hash,'path':'Z:/OldPC/Music/'+name,'name':name,'size':32,'seconds':100})
    c.execute("INSERT INTO events(id,name,active) VALUES(1,'Night',1)");c.commit()
    metrics=lambda score:{'tonal':{'score':score,'contrast':3,'seconds':15,'a_section':[1,16],'b_section':[3,18]}}
    payload={'format':'dj-miljan-audio-results','schema':1,'engine':audio.ENGINE,'tracks':tracks,'pairs':[{'a':tracks[0]['hash'],'b':tracks[i-1]['hash'],'metrics':metrics(95-i/10)} for i in (10,20,30,40)]}
    def save(data):
        path=tmp_path/'results.djmatches'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('manifest.json',json.dumps(data))
        return path
    yield tmp_path,tracks,payload,save
    c.close();db._local.c=None

def test_relocates_by_verified_content_without_audio_modules_and_preserves_five(library,monkeypatch):
    folder,tracks,data,save=library;before=suggest.suggest(1)['items'];result=audio.import_file(save(data))
    assert result['mapped']==40 and result['pairs']==4
    monkeypatch.setitem(config.SETTINGS,'additional_source','external')
    client=TestClient(app,client=('127.0.0.1',5000));answer=client.get('/api/suggest?track_id=1').json()
    assert answer['items']==before
    assert answer['additional_source']=='external';assert len(answer['additional'])==3
    assert not ({t['id'] for t in before}&{t['id'] for t in answer['additional']})
    assert all(t['external_audio'] for t in answer['additional'])
    assert len({t['id'] for t in answer['additional']})==3
    client.close()

def test_exclusions_bans_plays_and_no_random_fallback(library):
    folder,tracks,data,save=library;audio.import_file(save(data));c=db.conn()
    c.execute('INSERT INTO plays(event_id,track_id) VALUES(1,10)');c.execute("INSERT INTO bans(event_id,kind,value) VALUES(1,'track','20')");c.commit()
    assert [t['id'] for t in audio.recommend(1,{30})]==[40]
    assert audio.recommend(2)==[]
    assert audio.recommend(1,{30,40})==[]

def test_filename_is_never_enough_and_cancel_preserves_previous_import(library):
    folder,tracks,data,save=library;audio.import_file(save(data));before=audio.status()
    stop=threading.Event();stop.set()
    with pytest.raises(ScanCancelled):audio.import_file(save(data),stop=stop)
    assert audio.status()['pairs']==before['pairs']
    data['tracks'][0]['hash']='0'*64
    for pair in data['pairs']:pair['a']='0'*64
    result=audio.import_file(save(data));assert result['unresolved']==1 and result['pairs']==0
    assert audio.recommend(1)==[]

def test_changed_file_and_weak_control_reject_results(library):
    folder,tracks,data,save=library
    for pair in data['pairs']:pair['metrics']['tonal']['contrast']=1.1
    audio.import_file(save(data));assert audio.recommend(1)==[]
    for pair in data['pairs']:pair['metrics']['tonal']['contrast']=3
    audio.import_file(save(data));assert audio.recommend(1)
    (folder/tracks[0]['name']).write_bytes(b'changed');assert audio.recommend(1)==[]

def test_invalid_import_and_thresholds_are_rejected(library):
    folder,tracks,data,save=library;data['schema']=999
    with pytest.raises(ValueError):audio.import_file(save(data))
    client=TestClient(app,client=('127.0.0.1',5000))
    assert client.post('/api/settings',json={'external_audio_thresholds':{'tonal':101}}).status_code==400
    assert client.post('/api/settings',json={'additional_source':'fake'}).status_code==400
    client.close()
