import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import config,db,auto_audio,audio_rhythm,learned_audio,audio_cache_transfer as m,nml

class TransferTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.c=sqlite3.connect(':memory:',check_same_thread=False);self.c.row_factory=sqlite3.Row
  self.patches=[patch.object(db,'conn',return_value=self.c),patch.object(config,'DATA',self.root),patch.object(db,'DB_FILE',self.root/'library.db')]
  for p in self.patches:p.start()
  db.init();m.init();self.file=self.root/'PC'/'song.mp3';self.file.parent.mkdir();self.file.write_bytes(b'exact file content')
  self.c.execute('INSERT INTO tracks(id,pkey,path,artist,title,on_disk) VALUES(1,?,?,?,?,1)',(nml.pkey(str(self.file)),str(self.file),'Artist','Song'));self.c.commit();self.track=db.one('SELECT * FROM tracks WHERE id=1');s=self.file.stat()
  data=json.dumps([dict(start=0,end=24,features={},song_phrase={'chroma':'fixture'})])
  self.c.execute('INSERT INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?,?)',(1,str(self.file),s.st_size,s.st_mtime_ns,auto_audio.VERSION,'ok',data,None,'[]',learned_audio.MODEL_ID));self.c.commit()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.c.close();self.tmp.cleanup();m.LAST.clear();m.EXPORTS.clear()
 def package(self):return m.read_package(m.export()['path'])
 def test_moved_file_roundtrip_and_readonly_check_without_decoder(self):
  package=self.package();new=self.root/'Laptop'/'song.mp3';new.parent.mkdir();new.write_bytes(self.file.read_bytes());os.utime(new,ns=(1,2))
  self.c.execute('UPDATE tracks SET path=?,pkey=? WHERE id=1',(str(new),nml.pkey(str(new))));self.c.execute('DELETE FROM auto_chorus');self.c.commit()
  with patch.object(audio_rhythm,'decoder',side_effect=AssertionError('must not decode')),patch.object(auto_audio,'analyze',side_effect=AssertionError('must not scan')):
   result=m.apply_package(package);self.assertEqual(result['matched'],1)
   check=m.check();self.assertEqual(check['counts']['skenirano'],1);self.assertEqual(check['database_path'],str(self.root/'library.db'))
  row=db.one('SELECT * FROM auto_chorus');self.assertEqual(row['path'],str(new));self.assertEqual(row['mtime'],new.stat().st_mtime_ns);self.assertTrue(auto_audio.valid(row,db.one('SELECT * FROM tracks')))
 def test_same_name_different_content_does_not_import_and_model_mismatch_is_visible(self):
  package=self.package();stamp=self.file.stat().st_mtime_ns;self.file.write_bytes(b'other file content');os.utime(self.file,ns=(stamp,stamp))
  self.assertEqual(m.apply_package(package)['matched'],0)
  self.file.write_bytes(b'exact file content');os.utime(self.file,ns=(stamp,stamp));package['items'][0]['chorus']['model']='other-model'
  result=m.apply_package(package);self.assertEqual(result['matched'],0);self.assertEqual(result['incompatible'],1)
 def test_manual_relocate_requires_hash_and_unique_missing_track(self):
  package=self.package();new=self.root/'moved'/'song.mp3';new.parent.mkdir();new.write_bytes(self.file.read_bytes());self.file.unlink()
  result=m.apply_package(package,[str(new.parent)]);self.assertEqual(result['relocated'],1);self.assertEqual(result['matched'],1)
  self.assertEqual(db.one('SELECT path FROM tracks')['path'],str(new))
 def test_http_check_is_local_and_does_not_start_scan(self):
  from fastapi.testclient import TestClient
  from app.main import app
  with patch.object(auto_audio,'ensure',side_effect=AssertionError('must not scan')):
   local=TestClient(app,client=('127.0.0.1',2000));r=local.get('/api/audio/cache/check');self.assertEqual(r.status_code,200);self.assertEqual(r.json()['counts']['skenirano'],1)
   m.EXPORTS['known']=self.file
   with patch('app.native_files.reveal_file') as reveal:
    self.assertEqual(local.post('/api/audio/cache/reveal/known').status_code,200);reveal.assert_called_once_with(str(self.file))
   self.assertEqual(local.post('/api/audio/cache/reveal/unknown').status_code,404)
   remote=TestClient(app,client=('192.0.2.1',2000));self.assertEqual(remote.get('/api/audio/cache/check').status_code,403)
 def test_bad_format_and_stop(self):
  import gzip,threading
  path=self.root/'bad.djaudio'
  with gzip.open(path,'wt') as f:json.dump(dict(format='not this app',version=1,items=[]),f)
  with self.assertRaises(ValueError):m.read_package(path)
  from app.scan_control import ScanCancelled
  stop=threading.Event();stop.set()
  with self.assertRaises(ScanCancelled):m.export(stop_event=stop)
