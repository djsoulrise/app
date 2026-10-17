import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import config,db,history_context as history,suggest
from app.main import app


class HistoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.connection=sqlite3.connect(Path(self.temp.name)/'db',check_same_thread=False);self.connection.row_factory=sqlite3.Row
        self.patch=patch.object(db,'conn',return_value=self.connection);self.patch.start();db.init()
        for i in range(1,15):self.connection.execute('INSERT INTO tracks(id,pkey,artist,title) VALUES(?,?,?,?)',(i,str(i),'Artist',('Italiana' if i==1 else 'Song '+chr(64+i)*3)))
        sequences=[(1,'history:one','history',0,[2,1,5]),(2,'history:two','history',1,[2,1,6]),(3,'history:three','history',1,[3,1,5]),(4,'history:four','history',0,[4,1,7]),(5,'Unplayed sorted playlist','collection',1,[8,1,9])]
        for pid,name,source,learn,ids in sequences:
            self.connection.execute('INSERT INTO src_playlists(id,name,source,use_learn) VALUES(?,?,?,?)',(pid,name,source,learn))
            self.connection.executemany('INSERT INTO src_entries VALUES(?,?,?)',[(pid,i,tid) for i,tid in enumerate(ids)])
        self.connection.commit()
    def tearDown(self):
        self.connection.close();self.patch.stop();self.temp.cleanup()
    def test_specific_track_three_before_three_after_from_all_real_history(self):
        result=history.neighbors(1)
        self.assertEqual([t['id'] for t in result['before']],[2,3,4])
        self.assertEqual([t['id'] for t in result['after']],[5,6,7])
        self.assertEqual(result['before'][0]['times'],2)
        self.assertEqual(result['before'][0]['lists'],2)
        self.assertNotIn(8,[t['id'] for t in result['before']])
        self.assertNotIn(9,[t['id'] for t in result['after']])
        self.assertEqual(history.neighbors(14)['before'],[])
    def test_played_log_and_audio_exclusions_and_direction(self):
        self.connection.execute("INSERT INTO events(id,name) VALUES(1,'Vece')")
        self.connection.executemany('INSERT INTO plays(event_id,track_id,ts) VALUES(1,?,?)',[(10,'2026-10-17T20:00:00'),(1,'2026-10-17T20:05:00'),(11,'2026-10-17T20:09:00')]);self.connection.commit()
        result=history.neighbors(1,excluded_ids={2,5})
        self.assertEqual([t['id'] for t in result['before']],[3,4,10])
        self.assertEqual([t['id'] for t in result['after']],[6,7,11])
        all_ids=[t['id'] for t in result['before']+result['after']]
        self.assertEqual(len(all_ids),len(set(all_ids)))
    def test_http_context_is_added_without_changing_left_recommendations(self):
        fixture={'items':[{'id':20},{'id':21},{'id':22},{'id':23},{'id':24}],'audio_diagnostics':{'accepted_top':[{'id':2}]}}
        with patch.object(suggest,'suggest',return_value=fixture.copy()):
            client=TestClient(app,client=('127.0.0.1',50000))
            response=client.get('/api/suggest?track_id=1').json();client.close()
        self.assertEqual(response['items'],fixture['items'])
        self.assertNotIn(2,[t['id'] for t in response['history_context']['before']])


if __name__=='__main__':unittest.main()
