import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import numpy as np

from app import db, audio_rhythm
from test_audio_rhythm import drum_audio


class AudioCacheTests(unittest.TestCase):
    def test_decode_cache_invalidation_and_selection(self):
        old_file = db.DB_FILE
        old_conn = getattr(db._local, 'c', None)
        with tempfile.TemporaryDirectory() as folder:
            try:
                db._local.c = None
                db.DB_FILE = Path(folder)/'test.db'
                db.init()
                paths = []
                for i, kind in enumerate(('dance', 'dance', 'broken'), 1):
                    path = str(Path(folder)/f'{i}.wav')
                    y = drum_audio(kind, gain=.6)
                    with wave.open(path, 'wb') as f:
                        f.setnchannels(1); f.setsampwidth(2); f.setframerate(11025)
                        f.writeframes((np.clip(y, -1, 1)*32767).astype('<i2').tobytes())
                    paths.append(path)
                    db.conn().execute('INSERT INTO tracks(id,path,pkey) VALUES(?,?,?)', (i,path,path))
                db.conn().commit()
                first = audio_rhythm.scan()
                self.assertEqual(first['scanned'], 3)
                self.assertEqual(first['failed'], 0)
                with patch.object(audio_rhythm, 'analyze_file', side_effect=AssertionError('cache missed')):
                    self.assertEqual(audio_rhythm.scan()['cached'], 3)
                current = {'id': 1, 'path': paths[0]}
                candidates = [{'id': i, 'path': paths[i-1]} for i in (2,3)]
                selected, reason = audio_rhythm.fifth(current,candidates,set())
                self.assertEqual(selected['id'], 2)
                self.assertIsNone(reason)
                selected, reason = audio_rhythm.fifth(current,candidates,{2})
                self.assertIsNone(selected)
                row = db.one('SELECT * FROM audio_rhythm WHERE track_id=1')
                stat = os.stat(paths[0])
                os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns+1000000000))
                self.assertFalse(audio_rhythm.valid(row,current))
                selected, reason = audio_rhythm.fifth(current,candidates,set())
                self.assertIsNone(selected)
            finally:
                if getattr(db._local, 'c', None):
                    db._local.c.close()
                db._local.c = old_conn
                db.DB_FILE = old_file


if __name__ == '__main__':
    unittest.main()
