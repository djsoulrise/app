"""HTTP contract tests run in the Windows build with the app dependencies installed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import config, db
from app.main import app


class AudioApiTests(unittest.TestCase):
    def test_threshold_and_explorer_http_contract(self):
        old_file, old_conn = db.DB_FILE, getattr(db._local, 'c', None)
        previous = config.SETTINGS.get('audio_similarity_threshold', .97)
        with tempfile.TemporaryDirectory() as folder:
            try:
                db.DB_FILE = Path(folder)/'test.db'
                db._local.c = None
                db.init()
                with patch.object(config, 'SETTINGS_FILE', Path(folder)/'settings.json'):
                    client = TestClient(app, cookies={'dj': config.SETTINGS['dj_pin']})
                    for value in (True, 'nan', .49, 1.01):
                        self.assertEqual(client.post('/api/audio/threshold', json={'threshold': value}).status_code, 400)
                    response = client.post('/api/audio/threshold', json={'threshold': .85})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(client.get('/api/audio/status').json()['threshold'],.85)
                    self.assertEqual(json.loads(config.SETTINGS_FILE.read_text())['audio_similarity_threshold'],.85)
                    self.assertEqual(client.get('/api/audio/tracks?q=no-such-track').json(),[])
                    self.assertEqual(client.get('/api/audio/matches?track_id=999999').status_code,400)
                    self.assertEqual(client.post('/api/settings',json={'audio_similarity_threshold': 2}).status_code,400)
                    client.close()
            finally:
                config.SETTINGS['audio_similarity_threshold'] = previous
                if getattr(db._local, 'c', None):
                    db._local.c.close()
                db._local.c = old_conn
                db.DB_FILE = old_file


if __name__ == '__main__':
    unittest.main()
