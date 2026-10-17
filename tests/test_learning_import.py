import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import db,learning_import as m,history_context

class LearningImportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.c=sqlite3.connect(':memory:',check_same_thread=False);self.c.row_factory=sqlite3.Row;self.p=patch.object(db,'conn',return_value=self.c);self.p.start();db.init()
  for i,path in enumerate(['/music/A.mp3','/music/B.mp3','/music/C.mp3','/other/C.mp3'],1):self.c.execute('INSERT INTO tracks(id,pkey,path,artist,title) VALUES(?,?,?,?,?)',(i,path.lower(),path,'Artist',chr(64+i)*3))
  self.c.commit()
 def tearDown(self):self.p.stop();self.c.close();self.tmp.cleanup()
 def test_import_short_list_learns_and_context_includes_all_lists(self):
  text='#EXTM3U\n/music/A.mp3\n/music/B.mp3\n/music/A.mp3\n'
  with patch.object(m.library,'rebuild_learning') as learn:
   result=m.import_files([dict(name='wedding.m3u8',content=text)]);self.assertEqual(result['matched'],3);learn.assert_called_once()
  self.assertEqual(db.one('SELECT use_learn FROM src_playlists')['use_learn'],1)
  self.assertEqual(history_context.neighbors(1)['before'][0]['id'],2)
  again=m.import_files([dict(name='wedding.m3u8',content=text)])
  self.assertTrue(again['results'][0]['already_imported']);self.assertEqual(db.one('SELECT COUNT(*) n FROM src_playlists')['n'],1)
 def test_unique_filename_and_ambiguous_and_missing_keep_original_positions(self):
  result=m.import_files([dict(name='old.m3u',content='X:/old/A.mp3\nX:/old/C.mp3\nX:/old/B.mp3\nX:/old/no.mp3')])['results'][0]
  self.assertEqual((result['matched'],result['ambiguous'],result['unmatched']),(2,1,1))
  self.assertEqual(history_context.neighbors(1)['after'],[])
 def test_nml_collection_fallback_and_multiple_nested_playlists(self):
  xml='<NML><PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES><NODE TYPE="PLAYLIST" NAME="one"><PLAYLIST><ENTRY><PRIMARYKEY KEY="/:music/:A.mp3"/></ENTRY></PLAYLIST></NODE><NODE TYPE="PLAYLIST" NAME="two"><PLAYLIST><ENTRY><PRIMARYKEY KEY="/:music/:B.mp3"/></ENTRY></PLAYLIST></NODE></SUBNODES></NODE></PLAYLISTS></NML>'
  # Exact Windows keys cannot match Linux fixture paths; unique filename fallback does.
  r=m.import_files([dict(name='all.nml',content=xml)]);self.assertEqual(r['lists'],2)
  self.assertEqual(len(m.parse('history.nml','<NML><COLLECTION><ENTRY><LOCATION DIR="/:music/:" FILE="A.mp3" VOLUME="D:"/></ENTRY></COLLECTION></NML>')[0][1]),1)
