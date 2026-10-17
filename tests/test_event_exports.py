import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import config,db,event_exports as exports
from app.main import app
from app.scan_control import ScanCancelled


class DayExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.connection=sqlite3.connect(self.folder/'test.db',check_same_thread=False)
        self.connection.row_factory=sqlite3.Row
        self.patches=[patch.object(db,'conn',return_value=self.connection),patch.dict(config.SETTINGS,export_dir=str(self.folder/'exports'))]
        for p in self.patches:p.start()
        db.init();self.client=TestClient(app,client=('127.0.0.1',50000),cookies={'dj':config.SETTINGS['dj_pin']})
        for i in (1,2):
            folder=self.folder/str(i);folder.mkdir();song=folder/'Žurka.mp3';song.write_bytes(bytes([i])*2000)
            self.connection.execute('INSERT INTO tracks(id,pkey,path,artist,title,duration) VALUES(?,?,?,?,?,?)',(i,str(i),str(song),'Izvođač','Pesma '+str(i),180))
        self.connection.execute("INSERT INTO tracks(id,pkey,path,title) VALUES(3,'missing','missing.mp3','Nedostaje')")
        self.connection.execute("INSERT INTO events(id,name,date,active) VALUES(1,'Vece / Test','2026-10-16',1)")
        self.connection.execute("INSERT INTO events(id,name,date) VALUES(2,'Drugo','2026-10-17')")
        self.connection.executemany('INSERT INTO plays(event_id,track_id,ts) VALUES(?,?,?)',[(1,2,'2026-10-16T23:59:00'),(1,1,'2026-10-17T00:01:00'),(1,2,'2026-10-17T00:03:00'),(1,1,'2026-10-17T00:04:00'),(1,3,'2026-10-17T00:05:00'),(2,2,'2026-10-17T00:06:00')]);self.connection.commit()
    def tearDown(self):
        self.client.close();self.connection.close()
        for p in self.patches[::-1]:p.stop()
        self.temp.cleanup()
    def test_day_and_event_scoping_order_unicode_missing_and_repeats(self):
        result=exports.export(1,'2026-10-17')
        content=Path(result['playlist']).read_text(encoding='utf-8-sig')
        paths=[x for x in content.splitlines() if not x.startswith('#')]
        self.assertEqual([Path(x).parent.name for x in paths],['1','2','1'])
        self.assertIn('Izvođač',content);self.assertEqual(len(result['missing']),1)
        self.assertEqual(exports.days(1),[{'day':'2026-10-17','plays':4},{'day':'2026-10-16','plays':1}])
    def test_portable_copies_are_unique_no_collisions_and_originals_unchanged(self):
        before={p:p.read_bytes() for p in self.folder.glob('*/Žurka.mp3')}
        result=exports.export(1,'2026-10-17',True);folder=Path(result['path'])
        self.assertEqual(result['copied'],2)
        names=[x for x in Path(result['playlist']).read_text(encoding='utf-8-sig').splitlines() if not x.startswith('#')]
        self.assertEqual(names[0],names[2]);self.assertNotEqual(names[0],names[1])
        self.assertTrue(all((folder/n).is_file() and not Path(n).is_absolute() for n in names))
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)
        again=exports.export(1,'2026-10-17',True);self.assertNotEqual(result['path'],again['path'])
    def test_export_api_validates_date_and_does_not_switch_active_event(self):
        self.assertEqual(self.client.get('/api/events/1/days').status_code,200)
        self.assertEqual(self.client.post('/api/events/1/days/2026-10-17/export',json={'kind':'m3u'}).status_code,200)
        self.assertEqual(self.client.post('/api/events/1/days/2026-10-99/export',json={'kind':'m3u'}).status_code,400)
        self.assertEqual(self.client.post('/api/events/1/days/2026-10-17/export',json={'kind':'move'}).status_code,400)
        self.assertEqual(db.one('SELECT id FROM events WHERE active=1')['id'],1)
    def test_stop_keeps_completed_results_and_removes_partial_copy(self):
        stop=threading.Event()
        def progress(i,*args):
            if i==1:stop.set()
        with self.assertRaises(ScanCancelled) as caught:exports.export(1,'2026-10-17',True,progress,stop)
        folder=Path(caught.exception.result['path'])
        self.assertTrue(Path(caught.exception.result['playlist']).is_file())
        self.assertFalse(list(folder.glob('*.partial')))


if __name__=='__main__':unittest.main()
