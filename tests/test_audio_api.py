"""HTTP contract tests run in the Windows build with the app dependencies installed."""
import json
import sqlite3
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import config, db
from app.main import app, JOB
from app import audio_rhythm, grid_scan
from app.scan_control import ScanCancelled


class AudioApiTests(unittest.TestCase):
    def test_threshold_and_explorer_http_contract(self):
        previous = config.SETTINGS.get('audio_similarity_threshold', .97)
        with tempfile.TemporaryDirectory() as folder:
            connection = sqlite3.connect(Path(folder)/'test.db', check_same_thread=False)
            connection.row_factory = sqlite3.Row
            try:
                # Sequential TestClient requests share one explicitly owned connection,
                # so Windows cleanup cannot race an abandoned worker-thread connection.
                with patch.object(db, 'conn', return_value=connection), patch.object(config, 'SETTINGS_FILE', Path(folder)/'settings.json'):
                    db.init()
                    client = TestClient(app, cookies={'dj': config.SETTINGS['dj_pin']})
                    try:
                        for value in (True, 'nan', .49, 1.01):
                            self.assertEqual(client.post('/api/audio/threshold', json={'threshold': value}).status_code, 400)
                        response = client.post('/api/audio/threshold', json={'threshold': .85})
                        self.assertEqual(response.status_code,200)
                        self.assertEqual(client.get('/api/audio/status').json()['threshold'],.85)
                        self.assertEqual(json.loads(config.SETTINGS_FILE.read_text())['audio_similarity_threshold'],.85)
                        self.assertEqual(client.get('/api/audio/tracks?q=no-such-track').json(),[])
                        self.assertEqual(client.get('/api/audio/matches?track_id=999999').status_code,400)
                        self.assertEqual(client.post('/api/settings',json={'audio_similarity_threshold': 2}).status_code,400)
                        started=threading.Event()
                        def fake_scan(progress,stop):
                            started.set()
                            stop.wait(3)
                            raise ScanCancelled({'scanned':1})
                        with patch.object(audio_rhythm,'scan',side_effect=fake_scan):
                            self.assertEqual(client.post('/api/audio/scan').status_code,200)
                            self.assertTrue(started.wait(1))
                            self.assertEqual(client.post('/api/job/stop',json={'kind':'grid_audio'}).status_code,409)
                            self.assertEqual(client.post('/api/job/stop',json={'kind':'audio'}).status_code,200)
                            for _ in range(100):
                                if not JOB['running']: break
                                time.sleep(.01)
                            self.assertFalse(JOB['running'])
                            self.assertTrue(JOB['cancelled'])
                            self.assertEqual(JOB['result'],{'scanned':1})
                        # Cancelling NML verification keeps the previous complete grid table.
                        connection.execute('CREATE TABLE grid_scan(track_id INTEGER PRIMARY KEY,markers INTEGER,bpm_values INTEGER)')
                        connection.execute('INSERT INTO grid_scan VALUES(123,2,1)')
                        connection.execute("INSERT INTO tracks(id,pkey,path) VALUES(123,'p','unused')")
                        connection.commit()
                        stop=threading.Event()
                        def grid_progress(*args): stop.set()
                        source=[{'pkey':'p','grid_markers':4,'grid_bpms':3},{'pkey':'other','grid_markers':2,'grid_bpms':1}]
                        with patch.object(grid_scan.nml,'parse_collection',return_value=(source,[])), patch.dict(config.SETTINGS,{'nml_path':'unused.nml'}):
                            with self.assertRaises(ScanCancelled): grid_scan.scan(grid_progress,stop)
                        self.assertEqual(connection.execute('SELECT markers FROM grid_scan WHERE track_id=123').fetchone()[0],2)
                    finally:
                        client.close()
            finally:
                connection.close()
                config.SETTINGS['audio_similarity_threshold'] = previous



if __name__ == '__main__':
    unittest.main()
